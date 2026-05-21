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
from app.events.runner_event_ingest import LOGIN_SLOT_FINAL_EVENT_TYPES, apply_login_slot_final_event
from app.infra.redis_streams import EVENT_STREAM_KEY
from app.repositories.control_plane_repository import ControlPlaneRepository
from app.services.store_service import get_process_store

log = logging.getLogger("runner_event_consumer")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _canonical_slot_id(value: Any) -> str:
    raw = _clean(value)
    if raw.lower().startswith("slot_"):
        return f"slot-{raw[5:]}"
    return raw


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "ok", "ready", "healthy", "verified"}


def _login_slot_command_type(payload: dict[str, Any]) -> str:
    return (
        _clean(payload.get("requested_cmd_type"))
        or _clean(payload.get("command_type"))
        or _clean(payload.get("cmd_type"))
    ).upper()


def _merge_event_field_payload(fields: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    merged = dict(payload or {})
    for key in (
        "status",
        "reason",
        "error",
        "error_code",
        "message",
        "reservation_id",
        "login_reservation_id",
        "elapsed_ms",
        "slot_ttl_sec",
        "expires_at",
        "account_login",
        "account_server",
        "runner_event_type",
    ):
        value = fields.get(key)
        if value not in (None, "") and key not in merged:
            merged[key] = value
    return merged


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
        payload = _merge_event_field_payload(fields, payload if isinstance(payload, dict) else {})
        event_type = _clean(fields.get("event_type")).upper()
        command_id = _clean(fields.get("command_id")) or _clean(payload.get("command_id")) or None
        runner_id = _clean(fields.get("runner_id")) or _clean(payload.get("runner_id")) or None
        slot_id = _canonical_slot_id(fields.get("slot_id") or payload.get("slot_id")) or None
        account_id = int(fields["account_id"]) if _clean(fields.get("account_id")) else None
        deployment_id = int(fields["deployment_id"]) if _clean(fields.get("deployment_id")) else None
        trace_id = _clean(fields.get("trace_id")) or _clean(payload.get("trace_id")) or None
        severity = _clean(fields.get("severity")) or "info"

        if event_type == "RUNTIME_LOG" and _login_slot_command_type(payload) == "RESERVE_OR_LOGIN_SLOT":
            prepared = _truthy(payload.get("prepared"))
            status = _clean(payload.get("status")).lower()
            error_text = _clean(payload.get("error") or payload.get("reason") or payload.get("message"))
            if command_id and (prepared or status in {"healthy", "verified", "ready"}):
                self._repo.complete_login_reservation(
                    command_id=command_id,
                    ok=True,
                    runner_id=runner_id,
                    slot_id=slot_id,
                    error_text=None,
                    payload={**payload, "stream_id": stream_id, "compat_event_type": "RUNTIME_LOG_PREPARED"},
                    ttl_sec=int(payload.get("login_slot_ttl_sec") or 300),
                )
            elif command_id and status in {"failed", "error", "broken"}:
                self._repo.complete_login_reservation(
                    command_id=command_id,
                    ok=False,
                    runner_id=runner_id,
                    slot_id=slot_id,
                    error_text=error_text or "runtime_log_login_slot_failed",
                    payload={**payload, "stream_id": stream_id, "compat_event_type": "RUNTIME_LOG_FAILED"},
                )
        elif event_type in LOGIN_SLOT_FINAL_EVENT_TYPES:
            apply_login_slot_final_event(
                self._repo,
                event_type_value=event_type,
                account_id=account_id,
                command_id=command_id,
                runner_id=runner_id or "",
                slot_id=slot_id,
                payload_map={**payload, "stream_id": stream_id},
            )
        self._repo.upsert_execution_audit(
            event_id=str(fields.get("event_id") or "").strip(),
            command_id=command_id,
            trace_id=trace_id,
            account_id=account_id,
            deployment_id=deployment_id,
            runner_id=runner_id,
            slot_id=slot_id,
            event_type=event_type,
            severity=severity,
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
                    log.debug("runner_event_consumer idle redis read timeout: %s", message)
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
