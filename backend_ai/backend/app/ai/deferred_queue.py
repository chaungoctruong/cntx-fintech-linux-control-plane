from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from app.ai.chat_memory import append_chat_exchange
from app.core.redis_client import get_redis_read, get_redis_write
from app.ai.errors import AIOverloadedError, AIProviderUnavailableError
from app.ai.executor import ai_executor
from app.settings import settings

log = logging.getLogger("ai_deferred_queue")

_TELEGRAM_MAX_TEXT_LEN = 4096
_TELEGRAM_SAFE_TEXT_CHUNK_LEN = 3900


def _telegram_plain_text_chunks(text: Any, max_len: int = _TELEGRAM_SAFE_TEXT_CHUNK_LEN) -> list[str]:
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    while "\n\n\n" in value:
        value = value.replace("\n\n\n", "\n\n")
    if not value:
        return []
    limit = max(1000, min(int(max_len or _TELEGRAM_SAFE_TEXT_CHUNK_LEN), _TELEGRAM_MAX_TEXT_LEN))
    if len(value) <= limit:
        return [value]

    chunks: list[str] = []
    remaining = value
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, limit)
        if split_at < limit * 0.5:
            split_at = remaining.rfind(" ", 0, limit)
        if split_at < limit * 0.5:
            split_at = limit
        chunk = remaining[:split_at].rstrip()
        remaining = remaining[split_at:].lstrip()
        if chunk:
            chunks.append(chunk)
    return chunks


@dataclass
class DeferredAIJob:
    job_id: str
    user_msg: str
    user_id: str
    mode: str
    channel: str
    use_search: bool
    context: dict[str, Any]
    error_code: Optional[str] = None
    attempt: int = 0
    queued_at: float = field(default_factory=time.time)


class DeferredAIQueue:
    def __init__(self) -> None:
        self.enabled = bool(getattr(settings, "AI_DEFERRED_QUEUE_ENABLED", True))
        self.maxsize = max(1, int(getattr(settings, "AI_DEFERRED_QUEUE_MAX", 12) or 12))
        self.retry_after_sec = max(1, int(getattr(settings, "AI_DEFERRED_QUEUE_RETRY_AFTER_SEC", 5) or 5))
        self.max_attempts = max(1, int(getattr(settings, "AI_DEFERRED_QUEUE_MAX_ATTEMPTS", 2) or 2))
        self.requeue_delay_sec = max(0.5, float(getattr(settings, "AI_DEFERRED_QUEUE_REQUEUE_DELAY_SEC", 2.0) or 2.0))
        self.job_ttl_sec = max(60, int(getattr(settings, "AI_DEFERRED_QUEUE_JOB_TTL_SEC", 1800) or 1800))
        self.key_prefix = str(getattr(settings, "AI_DEFERRED_QUEUE_KEY_PREFIX", "ai:deferred:job:") or "ai:deferred:job:").strip()
        self._queue: asyncio.Queue[DeferredAIJob] = asyncio.Queue(maxsize=self.maxsize)
        self._worker_task: Optional[asyncio.Task] = None
        self._http_client: Optional[httpx.AsyncClient] = None
        self._local_jobs: dict[str, dict[str, Any]] = {}
        self._processed = 0
        self._failed = 0
        self._requeued = 0
        self._last_error = ""
        self._last_completed_at = 0.0

    def is_enabled(self) -> bool:
        return bool(self.enabled)

    def can_accept(self, *, user_id: str, channel: str) -> bool:
        _ = (user_id, channel)
        return self.is_enabled()

    def can_notify_telegram(self, *, user_id: str, channel: str) -> bool:
        if not self.is_enabled():
            return False
        token = str(getattr(settings, "TELEGRAM_BOT_TOKEN", "") or "").strip()
        chat_id = str(user_id or "").strip()
        return bool(token and chat_id.isdigit() and str(channel or "").strip().lower() == "telegram")

    def runtime_status(self) -> dict[str, Any]:
        worker_alive = bool(self._worker_task is not None and not self._worker_task.done())
        return {
            "enabled": self.is_enabled(),
            "worker_alive": worker_alive,
            "queued": int(self._queue.qsize()),
            "max_queued": self.maxsize,
            "processed": self._processed,
            "failed": self._failed,
            "requeued": self._requeued,
            "retry_after_sec": self.retry_after_sec,
            "job_ttl_sec": self.job_ttl_sec,
            "last_completed_at": int(self._last_completed_at) if self._last_completed_at > 0 else 0,
            "last_error": self._last_error[:180],
        }

    def _job_key(self, job_id: str) -> str:
        return f"{self.key_prefix}{job_id}"

    def _snapshot_job(
        self,
        job: DeferredAIJob,
        *,
        status: str,
        reply: str = "",
        error: str = "",
        detail: str = "",
        notify_delivered: Optional[bool] = None,
    ) -> dict[str, Any]:
        return {
            "job_id": job.job_id,
            "status": str(status or "").strip().lower(),
            "reply": str(reply or ""),
            "error": str(error or ""),
            "detail": str(detail or ""),
            "mode": job.mode,
            "channel": job.channel,
            "user_id": job.user_id,
            "attempt": int(job.attempt),
            "queued_at": float(job.queued_at),
            "updated_at": float(time.time()),
            "notify_delivered": notify_delivered,
        }

    async def _persist_job_state(self, payload: dict[str, Any]) -> None:
        job_id = str(payload.get("job_id") or "").strip()
        if not job_id:
            return
        self._local_jobs[job_id] = dict(payload)
        redis = await get_redis_write(decode_responses=True)
        if redis is None:
            return
        try:
            await redis.set(self._job_key(job_id), json.dumps(payload, ensure_ascii=False), ex=self.job_ttl_sec)
        except Exception as exc:
            self._last_error = str(exc)[:180]

    async def get_job_state(self, job_id: str) -> dict[str, Any] | None:
        job_id_s = str(job_id or "").strip()
        if not job_id_s:
            return None
        local = self._local_jobs.get(job_id_s)
        if isinstance(local, dict):
            return dict(local)
        redis = await get_redis_read(decode_responses=True)
        if redis is None:
            return None
        try:
            raw = await redis.get(self._job_key(job_id_s))
        except Exception:
            return None
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None

    async def start(self) -> None:
        if not self.is_enabled():
            log.info("Deferred AI queue disabled by settings.")
            return
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop(), name="ai_deferred_queue_worker")
            log.info("Deferred AI queue started maxsize=%s", self.maxsize)

    async def stop(self) -> None:
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                log.warning("Deferred AI queue worker shutdown failed: %s", exc)
            self._worker_task = None
        if self._http_client is not None:
            try:
                await self._http_client.aclose()
            except Exception:
                pass
            self._http_client = None

    async def enqueue(self, job: DeferredAIJob) -> int:
        await self._persist_job_state(self._snapshot_job(job, status="queued"))
        self._queue.put_nowait(job)
        return int(self._queue.qsize())

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=10.0,
                limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
            )
        return self._http_client

    async def _notify_user_telegram(self, user_id: str, text: str) -> bool:
        token = str(getattr(settings, "TELEGRAM_BOT_TOKEN", "") or "").strip()
        chat_id = str(user_id or "").strip()
        if not token or not chat_id.isdigit():
            return False
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        chunks = _telegram_plain_text_chunks(text)
        if not chunks:
            return False
        try:
            client = await self._get_http_client()
            for idx, chunk in enumerate(chunks, start=1):
                final_text = chunk
                if len(chunks) > 1:
                    prefix = f"[{idx}/{len(chunks)}]\n"
                    if len(prefix) + len(final_text) > _TELEGRAM_MAX_TEXT_LEN:
                        final_text = final_text[: _TELEGRAM_MAX_TEXT_LEN - len(prefix) - 1].rstrip()
                    final_text = prefix + final_text
                res = await client.post(url, json={"chat_id": chat_id, "text": final_text})
                if res.status_code == 429:
                    try:
                        retry_after = int((res.json().get("parameters") or {}).get("retry_after") or 1)
                    except Exception:
                        retry_after = 1
                    await asyncio.sleep(max(1, min(retry_after, 10)))
                    res = await client.post(url, json={"chat_id": chat_id, "text": final_text})
                if not res.is_success:
                    self._last_error = f"telegram_delivery_failed:{res.status_code}"
                    return False
                if len(chunks) > 1:
                    await asyncio.sleep(0.05)
            return True
        except Exception as exc:
            self._last_error = str(exc)
            return False

    async def _worker_loop(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                await self._persist_job_state(self._snapshot_job(job, status="processing"))
                reply = await ai_executor.handle_user_issue(
                    user_msg=job.user_msg,
                    error_code=job.error_code,
                    user_id=job.user_id,
                    mode=job.mode,
                    channel=job.channel,
                    use_search=job.use_search,
                    context=job.context,
                )
                await append_chat_exchange(
                    job.user_id,
                    job.user_msg,
                    reply,
                    mode=job.mode,
                    status="done",
                    source="deferred_executor",
                    context=job.context,
                    use_search=job.use_search,
                )
                delivered = False
                if self.can_notify_telegram(user_id=job.user_id, channel=job.channel):
                    delivered = await self._notify_user_telegram(job.user_id, reply)
                    if not delivered:
                        self._last_error = "telegram_delivery_failed"
                await self._persist_job_state(
                    self._snapshot_job(
                        job,
                        status="done",
                        reply=reply,
                        notify_delivered=delivered,
                        detail="reply_ready",
                    )
                )
                self._processed += 1
                self._last_completed_at = time.time()
                if delivered:
                    self._last_error = ""
            except (AIOverloadedError, AIProviderUnavailableError) as exc:
                if job.attempt + 1 < self.max_attempts:
                    self._requeued += 1
                    await asyncio.sleep(self.requeue_delay_sec)
                    try:
                        await self.enqueue(
                            DeferredAIJob(
                                job_id=job.job_id,
                                user_msg=job.user_msg,
                                user_id=job.user_id,
                                mode=job.mode,
                                channel=job.channel,
                                use_search=job.use_search,
                                context=job.context,
                                error_code=job.error_code,
                                attempt=job.attempt + 1,
                            )
                        )
                    except asyncio.QueueFull:
                        self._failed += 1
                        self._last_error = "deferred_queue_full_on_retry"
                        await self._persist_job_state(
                            self._snapshot_job(
                                job,
                                status="failed",
                                error="deferred_queue_full_on_retry",
                                detail="queue_full_during_retry",
                            )
                        )
                    continue

                self._failed += 1
                self._last_error = str(getattr(exc, "detail", str(exc)))[:180]
                fail_reply = "⚠️ Hiện đang có nhiều câu hỏi nên mình chưa trả lời xong câu này. Sếp thử lại sau ít phút nhé.\n\n👉 Bấm /start để quay lại menu chính."
                await self._persist_job_state(
                    self._snapshot_job(
                        job,
                        status="failed",
                        reply=fail_reply,
                        error="ai_overloaded",
                        detail=str(getattr(exc, "detail", str(exc)))[:180],
                    )
                )
                if self.can_notify_telegram(user_id=job.user_id, channel=job.channel):
                    await self._notify_user_telegram(job.user_id, fail_reply)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._failed += 1
                self._last_error = str(exc)[:180]
                fail_reply = "⚠️ Mình đã nhận câu hỏi nhưng chưa xử lý xong. Sếp thử lại sau ít phút nhé.\n\n👉 Bấm /start để quay lại menu chính."
                await self._persist_job_state(
                    self._snapshot_job(
                        job,
                        status="failed",
                        reply=fail_reply,
                        error="ai_execution_failed",
                        detail=str(exc)[:180],
                    )
                )
                if self.can_notify_telegram(user_id=job.user_id, channel=job.channel):
                    await self._notify_user_telegram(job.user_id, fail_reply)
            finally:
                self._queue.task_done()

    async def submit(
        self,
        *,
        user_msg: str,
        user_id: str,
        mode: str,
        channel: str,
        use_search: bool,
        context: dict[str, Any],
        error_code: Optional[str] = None,
    ) -> tuple[str, int]:
        job = DeferredAIJob(
            job_id=secrets.token_urlsafe(12),
            user_msg=user_msg,
            user_id=user_id,
            mode=mode,
            channel=channel,
            use_search=bool(use_search),
            context=context,
            error_code=error_code,
        )
        position = await self.enqueue(job)
        return job.job_id, position


deferred_ai_queue = DeferredAIQueue()


async def start_deferred_ai_queue() -> None:
    await deferred_ai_queue.start()


async def stop_deferred_ai_queue() -> None:
    await deferred_ai_queue.stop()


def deferred_ai_queue_status() -> dict[str, Any]:
    return deferred_ai_queue.runtime_status()


async def get_deferred_ai_job(job_id: str) -> dict[str, Any] | None:
    return await deferred_ai_queue.get_job_state(job_id)
