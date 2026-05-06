from __future__ import annotations

import asyncio
import json
import logging
import socket
from typing import Any

from app.core.log_hygiene import log_periodic, noisy_log_cooldown_sec
from app.core.redis_client import (
    get_redis_write,
    is_redis_retryable_connection_error,
    reset_redis_write_client,
)
from app.infra.redis_streams import EVENT_STREAM_KEY
from app.repositories.control_plane_repository import ControlPlaneRepository
from app.services.store_service import get_process_store

log = logging.getLogger("runner_event_consumer")


class RunnerEventConsumerService:
    def __init__(
        self,
        repo: ControlPlaneRepository | None = None,
        *,
        stream_key: str = EVENT_STREAM_KEY,
        group_name: str = "control-plane-event-audit",
        consumer_name: str | None = None,
        block_ms: int = 5000,
        batch_size: int = 50,
    ) -> None:
        self._repo = repo or ControlPlaneRepository(get_process_store())
        self._stream_key = str(stream_key or EVENT_STREAM_KEY).strip() or EVENT_STREAM_KEY
        self._group_name = str(group_name or "control-plane-event-audit").strip() or "control-plane-event-audit"
        self._consumer_name = str(consumer_name or f"{socket.gethostname()}:{id(self)}").strip()
        self._block_ms = max(1000, int(block_ms or 5000))
        self._batch_size = max(1, min(int(batch_size or 50), 500))

    async def _redis(self) -> Any:
        redis = await get_redis_write(decode_responses=True)
        if redis is None:
            raise RuntimeError("redis_unavailable")
        return redis

    async def _ensure_group(self, redis: Any) -> None:
        try:
            await redis.xgroup_create(name=self._stream_key, groupname=self._group_name, id="$", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def _process_stream_entry(self, *, stream_id: str, fields: dict[str, Any]) -> None:
        payload_raw = str(fields.get("payload_json") or "{}")
        try:
            payload = json.loads(payload_raw)
        except Exception:
            payload = {"_raw_payload_json": payload_raw}
        self._repo.upsert_execution_audit(
            event_id=str(fields.get("event_id") or "").strip(),
            command_id=str(fields.get("command_id") or "").strip() or None,
            trace_id=str(fields.get("trace_id") or "").strip() or None,
            account_id=int(fields["account_id"]) if str(fields.get("account_id") or "").strip() else None,
            deployment_id=int(fields["deployment_id"]) if str(fields.get("deployment_id") or "").strip() else None,
            runner_id=str(fields.get("runner_id") or "").strip() or None,
            slot_id=str(fields.get("slot_id") or "").strip() or None,
            event_type=str(fields.get("event_type") or "").strip(),
            severity=str(fields.get("severity") or "info").strip() or "info",
            audit_status="stream_projected",
            payload=payload,
            source_stream_id=stream_id,
        )

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        redis = None
        group_ready = False
        while not stop_event.is_set():
            try:
                if redis is None:
                    redis = await self._redis()
                    group_ready = False
                if not group_ready:
                    await self._ensure_group(redis)
                    group_ready = True
                rows = await redis.xreadgroup(
                    groupname=self._group_name,
                    consumername=self._consumer_name,
                    streams={self._stream_key: ">"},
                    count=self._batch_size,
                    block=self._block_ms,
                )
                if not rows:
                    continue
                for _, messages in rows:
                    for stream_id, fields in messages:
                        try:
                            await self._process_stream_entry(stream_id=str(stream_id), fields=dict(fields or {}))
                            await redis.xack(self._stream_key, self._group_name, stream_id)
                        except Exception as exc:
                            log.warning("runner_event_consumer failed stream_id=%s: %s", stream_id, exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                message = str(exc or "").strip()
                if "Timeout reading from" in message:
                    log.debug("runner_event_consumer idle poll timeout: %s", message)
                else:
                    log_periodic(
                        log,
                        logging.WARNING,
                        "runner_event_consumer loop error: %s",
                        exc,
                        key=f"runner_event_consumer_loop:{type(exc).__name__}:{message[:160]}",
                        cooldown_sec=noisy_log_cooldown_sec(),
                    )
                if is_redis_retryable_connection_error(exc):
                    await reset_redis_write_client()
                    redis = None
                    group_ready = False
                await asyncio.sleep(2.0)
