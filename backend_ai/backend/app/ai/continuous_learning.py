from __future__ import annotations

import asyncio
import json
import logging
import secrets
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.core.redis_client import get_redis_write
from app.settings import settings

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.ingest_platform_knowledge import ingest_source  # noqa: E402
from scripts.ingest_platform_sources import _host_allowed, ingest_manifest  # noqa: E402

log = logging.getLogger("ai_continuous_learning")

_SERVICE: "AIContinuousLearningService | None" = None


def _coerce_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item or "").strip() for item in value if str(item or "").strip()]
    raw = str(value or "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = None
    if isinstance(parsed, list):
        return [str(item or "").strip() for item in parsed if str(item or "").strip()]
    return [item.strip() for item in raw.split(",") if item.strip()]


def _settings_allowed_domains() -> list[str]:
    return _coerce_str_list(getattr(settings, "AI_CONTINUOUS_LEARNING_ALLOWED_DOMAINS", []))


def _resolve_project_path(value: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("path_required")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    resolved = path.resolve()
    try:
        resolved.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise ValueError("path_outside_project_root") from exc
    return resolved


def _url_host(url: str) -> str:
    return str(urlparse(str(url or "")).hostname or "").lower()


def _new_job_id() -> str:
    return f"ai-ingest-{int(time.time())}-{secrets.token_hex(4)}"


class AIContinuousLearningService:
    """Continuously ingest curated knowledge sources for AI RAG context.

    This is deliberately not automatic fine-tuning or automatic model deployment.
    Redis is only a queue/result cache; PostgreSQL remains the durable knowledge store
    through PlatformKnowledgeStore inside the existing ingest helpers.
    """

    def __init__(self) -> None:
        self.enabled = bool(getattr(settings, "AI_CONTINUOUS_LEARNING_ENABLED", False))
        self.interval_sec = max(30, int(getattr(settings, "AI_CONTINUOUS_LEARNING_INTERVAL_SEC", 900) or 900))
        self.manifest_path = str(getattr(settings, "AI_CONTINUOUS_LEARNING_MANIFEST_PATH", "") or "").strip()
        self.redis_queue = str(
            getattr(settings, "AI_CONTINUOUS_LEARNING_REDIS_QUEUE", "ai:knowledge:ingest:requests")
            or "ai:knowledge:ingest:requests"
        ).strip()
        self.result_prefix = str(
            getattr(settings, "AI_CONTINUOUS_LEARNING_REDIS_RESULT_PREFIX", "ai:knowledge:ingest:result:")
            or "ai:knowledge:ingest:result:"
        ).strip()
        self.result_ttl_sec = max(
            60,
            int(getattr(settings, "AI_CONTINUOUS_LEARNING_REDIS_RESULT_TTL_SEC", 86400) or 86400),
        )
        self.max_jobs_per_tick = max(
            1,
            int(getattr(settings, "AI_CONTINUOUS_LEARNING_MAX_JOBS_PER_TICK", 5) or 5),
        )
        self._task: asyncio.Task | None = None
        self._processed = 0
        self._failed = 0
        self._last_tick_at = 0.0
        self._last_success_at = 0.0
        self._last_error = ""
        self._last_result: dict[str, Any] = {}

    def runtime_status(self) -> dict[str, Any]:
        worker_alive = bool(self._task is not None and not self._task.done())
        return {
            "enabled": bool(self.enabled),
            "worker_alive": worker_alive,
            "interval_sec": self.interval_sec,
            "manifest_configured": bool(self.manifest_path),
            "redis_queue": self.redis_queue,
            "max_jobs_per_tick": self.max_jobs_per_tick,
            "allowed_domains": _settings_allowed_domains(),
            "processed": int(self._processed),
            "failed": int(self._failed),
            "last_tick_at": int(self._last_tick_at) if self._last_tick_at > 0 else 0,
            "last_success_at": int(self._last_success_at) if self._last_success_at > 0 else 0,
            "last_error": self._last_error[:180],
            "last_result": dict(self._last_result),
        }

    async def start(self) -> bool:
        if not self.enabled:
            log.info("AI continuous learning disabled by settings.")
            return False
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run_forever(), name="ai_continuous_learning_worker")
            log.info(
                "AI continuous learning started interval_sec=%s queue=%s manifest_configured=%s",
                self.interval_sec,
                self.redis_queue,
                bool(self.manifest_path),
            )
        return True

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.warning("AI continuous learning worker shutdown failed: %s", exc)
        self._task = None

    async def run_forever(self) -> None:
        while True:
            try:
                await self.run_once()
                await asyncio.sleep(self.interval_sec)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._failed += 1
                self._last_error = str(exc)[:180]
                log.warning("AI continuous learning tick failed: %s", exc)
                await asyncio.sleep(min(self.interval_sec, 60))

    async def run_once(self) -> dict[str, Any]:
        if not self.enabled:
            result = {"enabled": False, "status": "disabled"}
            self._last_result = result
            return result

        self._last_tick_at = time.time()
        redis = await get_redis_write(decode_responses=True)
        summary: dict[str, Any] = {
            "enabled": True,
            "manifest": None,
            "redis_jobs": [],
            "processed": 0,
            "failed": 0,
        }

        if self.manifest_path:
            manifest_result = await self._ingest_manifest_configured()
            summary["manifest"] = manifest_result
            if manifest_result.get("status") == "ok":
                summary["processed"] += 1
            else:
                summary["failed"] += 1

        if redis is not None:
            for _ in range(self.max_jobs_per_tick):
                raw = await self._pop_raw_job(redis)
                if not raw:
                    break
                job_result = await self._process_raw_job(raw)
                summary["redis_jobs"].append(job_result)
                await self._persist_job_result(redis, job_result)
                if job_result.get("status") == "ok":
                    summary["processed"] += 1
                else:
                    summary["failed"] += 1
        else:
            summary["redis_unavailable"] = True

        self._processed += int(summary["processed"])
        self._failed += int(summary["failed"])
        if int(summary["processed"]) > 0 and int(summary["failed"]) == 0:
            self._last_success_at = time.time()
            self._last_error = ""
        elif int(summary["failed"]) > 0:
            self._last_error = "one_or_more_ingest_jobs_failed"
        self._last_result = summary
        return summary

    async def _pop_raw_job(self, redis: Any) -> str:
        try:
            raw = await redis.lpop(self.redis_queue)
        except Exception as exc:
            self._last_error = str(exc)[:180]
            log.warning("AI continuous learning Redis pop failed: %s", exc)
            return ""
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return str(raw or "")

    async def _persist_job_result(self, redis: Any, result: dict[str, Any]) -> None:
        job_id = str(result.get("job_id") or "").strip()
        if not job_id:
            return
        try:
            await redis.set(
                f"{self.result_prefix}{job_id}",
                json.dumps(result, ensure_ascii=False),
                ex=self.result_ttl_sec,
            )
        except Exception as exc:
            self._last_error = str(exc)[:180]
            log.warning("AI continuous learning result persist failed: %s", exc)

    async def _ingest_manifest_configured(self) -> dict[str, Any]:
        try:
            path = _resolve_project_path(self.manifest_path)
            if not path.is_file():
                raise ValueError("manifest_file_missing")
            summary = await asyncio.to_thread(ingest_manifest, path, dry_run=False)
            return {"status": "ok", "path": str(path), "summary": summary}
        except Exception as exc:
            return {"status": "failed", "path": self.manifest_path, "error": str(exc)[:240]}

    async def _process_raw_job(self, raw: str) -> dict[str, Any]:
        try:
            payload = json.loads(str(raw or ""))
            if not isinstance(payload, dict):
                raise ValueError("job_payload_must_be_object")
            return await self._process_job_payload(payload)
        except Exception as exc:
            return {
                "job_id": _new_job_id(),
                "status": "failed",
                "kind": "unknown",
                "error": str(exc)[:240],
                "processed_at": int(time.time()),
            }

    async def _process_job_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        job_id = str(payload.get("job_id") or "").strip() or _new_job_id()
        kind = str(payload.get("kind") or "source").strip().lower()
        try:
            if kind == "manifest":
                result = await self._process_manifest_job(payload)
            elif kind == "source":
                result = await self._process_source_job(payload)
            else:
                raise ValueError("unsupported_job_kind")
            return {
                "job_id": job_id,
                "kind": kind,
                "status": "ok",
                "processed_at": int(time.time()),
                **result,
            }
        except Exception as exc:
            return {
                "job_id": job_id,
                "kind": kind,
                "status": "failed",
                "error": str(exc)[:240],
                "processed_at": int(time.time()),
            }

    async def _process_manifest_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        path = _resolve_project_path(str(payload.get("manifest") or payload.get("path") or ""))
        if not path.is_file():
            raise ValueError("manifest_file_missing")
        dry_run = bool(payload.get("dry_run", False))
        summary = await asyncio.to_thread(ingest_manifest, path, dry_run=dry_run)
        return {"path": str(path), "summary": summary}

    async def _process_source_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = str(payload.get("url") or "").strip()
        if not url:
            raise ValueError("source_url_required")
        if payload.get("file"):
            raise ValueError("redis_source_file_not_allowed")

        global_allowed = _settings_allowed_domains()
        if not global_allowed:
            raise ValueError("continuous_learning_allowed_domains_required")
        if not _host_allowed(url, global_allowed):
            raise ValueError(f"url_host_not_allowed:{_url_host(url)}")
        job_domains = _coerce_str_list(payload.get("allowed_domains"))
        if job_domains and not _host_allowed(url, job_domains):
            raise ValueError("url_host_not_allowed_by_job_domains")

        source_key, chunks = await asyncio.to_thread(
            ingest_source,
            url=url,
            file="",
            source_key=str(payload.get("source_key") or "").strip(),
            title=str(payload.get("title") or "").strip(),
            source_type=str(payload.get("source_type") or "web").strip() or "web",
            trust_level=int(payload.get("trust_level") or 50),
            max_chars=int(payload.get("max_chars") or getattr(settings, "AI_PLATFORM_KNOWLEDGE_CHUNK_MAX_CHARS", 1800) or 1800),
            timeout_sec=float(payload.get("timeout_sec") or 20.0),
            metadata={
                "continuous_learning": True,
                "job_id": str(payload.get("job_id") or ""),
                "queued_via": "redis",
                "tags": payload.get("tags") or [],
            },
        )
        return {"source_key": source_key, "chunks": int(chunks), "url_host": _url_host(url)}


async def start_ai_continuous_learning() -> bool:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = AIContinuousLearningService()
    return await _SERVICE.start()


async def stop_ai_continuous_learning() -> None:
    if _SERVICE is not None:
        await _SERVICE.stop()


def ai_continuous_learning_status() -> dict[str, Any]:
    if _SERVICE is None:
        return {
            "enabled": bool(getattr(settings, "AI_CONTINUOUS_LEARNING_ENABLED", False)),
            "worker_alive": False,
            "interval_sec": max(30, int(getattr(settings, "AI_CONTINUOUS_LEARNING_INTERVAL_SEC", 900) or 900)),
            "manifest_configured": bool(str(getattr(settings, "AI_CONTINUOUS_LEARNING_MANIFEST_PATH", "") or "").strip()),
            "redis_queue": str(
                getattr(settings, "AI_CONTINUOUS_LEARNING_REDIS_QUEUE", "ai:knowledge:ingest:requests")
                or "ai:knowledge:ingest:requests"
            ).strip(),
            "allowed_domains": _settings_allowed_domains(),
            "processed": 0,
            "failed": 0,
            "last_error": "",
        }
    return _SERVICE.runtime_status()
