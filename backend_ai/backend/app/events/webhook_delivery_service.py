"""Delivery worker cho user webhooks tu Redis execution event stream."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import socket
import time
from datetime import datetime, timezone
from typing import Any, Protocol

import httpx

from app.core.log_hygiene import log_periodic, noisy_log_cooldown_sec
from app.core.redis_client import (
    get_redis_write,
    is_redis_retryable_connection_error,
    reset_redis_write_client,
)
from app.infra.redis_streams import EVENT_STREAM_KEY
from app.repositories.control_plane_repository import ControlPlaneRepository
from app.services.store_service import get_process_store
from app.settings import settings

log = logging.getLogger("webhook_delivery")

WEBHOOK_DELIVERY_GROUP = "webhook-delivery"
WEBHOOK_DELIVERABLE_EVENTS = {
    "BOT_STARTED",
    "BOT_STOPPED",
    "ORDER_FILLED",
    "ORDER_REJECTED",
    "SLOT_BROKEN",
}
WEBHOOK_BACKOFF_SEC = 60
WEBHOOK_DEACTIVATE_AFTER_FAILURES = 5


class _AsyncPoster(Protocol):
    async def post(self, url: str, *, content: bytes, headers: dict[str, str], timeout: float) -> Any:
        ...


def build_webhook_signature(*, body: bytes, secret_hex: str) -> str:
    secret_value = str(secret_hex or "").strip()
    try:
        secret = bytes.fromhex(secret_value)
    except ValueError as exc:
        raise ValueError("invalid_webhook_secret_hex") from exc
    digest = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _json_body_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _decode_payload_json(value: Any) -> dict[str, Any]:
    raw = str(value or "{}")
    try:
        payload = json.loads(raw)
    except Exception:
        return {"_raw_payload_json": raw}
    return payload if isinstance(payload, dict) else {"value": payload}


def _optional_int(value: Any) -> int | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _event_matches_filter(webhook: dict[str, Any], event_type: str) -> bool:
    raw_filter = webhook.get("event_filter") or []
    if not isinstance(raw_filter, list) or not raw_filter:
        return True
    allowed = {str(item or "").strip().upper() for item in raw_filter if str(item or "").strip()}
    return str(event_type or "").strip().upper() in allowed


def _last_delivery_age_sec(value: Any, *, now: float) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return max(0.0, now - float(value))
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, now - dt.timestamp())
    return None


def should_skip_for_backoff(webhook: dict[str, Any], *, now: float | None = None) -> bool:
    fail_count = int(webhook.get("fail_count") or 0)
    if fail_count <= 0:
        return False
    age = _last_delivery_age_sec(webhook.get("last_delivered_at"), now=float(time.time() if now is None else now))
    return age is not None and age < WEBHOOK_BACKOFF_SEC


def build_webhook_event_body(*, stream_id: str, fields: dict[str, Any], delivered_at: int | None = None) -> dict[str, Any]:
    return {
        "stream_id": str(stream_id or ""),
        "event_id": str(fields.get("event_id") or ""),
        "event_type": str(fields.get("event_type") or "").strip().upper(),
        "account_id": _optional_int(fields.get("account_id")),
        "deployment_id": _optional_int(fields.get("deployment_id")),
        "bot_id": str(fields.get("bot_id") or ""),
        "runner_id": str(fields.get("runner_id") or ""),
        "slot_id": str(fields.get("slot_id") or ""),
        "command_id": str(fields.get("command_id") or ""),
        "severity": str(fields.get("severity") or "info"),
        "trace_id": str(fields.get("trace_id") or ""),
        "payload": _decode_payload_json(fields.get("payload_json")),
        "delivered_at": int(time.time() if delivered_at is None else delivered_at),
    }


class WebhookDeliveryService:
    def __init__(
        self,
        repo: ControlPlaneRepository | None = None,
        *,
        http_client: _AsyncPoster | None = None,
        stream_key: str = EVENT_STREAM_KEY,
        group_name: str = WEBHOOK_DELIVERY_GROUP,
        consumer_name: str | None = None,
        block_ms: int = 5000,
        batch_size: int = 50,
        timeout_sec: float = 5.0,
    ) -> None:
        self._repo = repo or ControlPlaneRepository(get_process_store())
        self._http_client = http_client
        self._stream_key = str(stream_key or EVENT_STREAM_KEY).strip() or EVENT_STREAM_KEY
        self._group_name = str(group_name or WEBHOOK_DELIVERY_GROUP).strip() or WEBHOOK_DELIVERY_GROUP
        self._consumer_name = str(consumer_name or f"{socket.gethostname()}:{id(self)}").strip()
        self._block_ms = max(1000, int(block_ms or 5000))
        self._batch_size = max(1, min(int(batch_size or 50), 500))
        self._timeout_sec = max(0.5, float(timeout_sec or 5.0))

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

    async def _post(self, *, url: str, body: bytes, signature: str) -> int:
        headers = {
            "Content-Type": "application/json",
            "X-CNTx-Signature": signature,
        }
        if self._http_client is not None:
            response = await self._http_client.post(url, content=body, headers=headers, timeout=self._timeout_sec)
            return int(getattr(response, "status_code", 0) or 0)
        async with httpx.AsyncClient(timeout=self._timeout_sec) as client:
            response = await client.post(url, content=body, headers=headers)
            return int(response.status_code)

    async def _deliver_to_webhook(self, *, webhook: dict[str, Any], body_payload: dict[str, Any]) -> bool:
        body = _json_body_bytes(body_payload)
        try:
            signature = build_webhook_signature(body=body, secret_hex=str(webhook.get("secret_hex") or ""))
            status_code = await self._post(url=str(webhook.get("url") or ""), body=body, signature=signature)
            if 200 <= status_code < 300:
                self._repo.mark_webhook_delivery_success(webhook_id=int(webhook["id"]))
                return True
            self._repo.mark_webhook_delivery_failure(
                webhook_id=int(webhook["id"]),
                error_text=f"http_{status_code}",
                deactivate_after_failures=WEBHOOK_DEACTIVATE_AFTER_FAILURES,
            )
            return False
        except Exception as exc:
            self._repo.mark_webhook_delivery_failure(
                webhook_id=int(webhook["id"]),
                error_text=f"{exc.__class__.__name__}:{str(exc)[:180]}",
                deactivate_after_failures=WEBHOOK_DEACTIVATE_AFTER_FAILURES,
            )
            return False

    async def _process_stream_entry(self, *, stream_id: str, fields: dict[str, Any]) -> dict[str, int]:
        event_type = str(fields.get("event_type") or "").strip().upper()
        if event_type not in WEBHOOK_DELIVERABLE_EVENTS:
            return {"delivered": 0, "failed": 0, "skipped": 1}

        account_raw = str(fields.get("account_id") or "").strip()
        if not account_raw:
            return {"delivered": 0, "failed": 0, "skipped": 1}
        try:
            account_id = int(account_raw)
        except ValueError:
            log.warning("webhook skip invalid account_id stream_id=%s account_id=%r", stream_id, account_raw)
            return {"delivered": 0, "failed": 0, "skipped": 1}

        webhooks = self._repo.list_active_webhooks_for_account(account_id=account_id)
        body_payload = build_webhook_event_body(stream_id=stream_id, fields=fields)
        delivered = 0
        failed = 0
        skipped = 0
        now = time.time()

        for webhook in webhooks:
            if not _event_matches_filter(webhook, event_type):
                skipped += 1
                continue
            if should_skip_for_backoff(webhook, now=now):
                skipped += 1
                continue
            ok = await self._deliver_to_webhook(webhook=webhook, body_payload=body_payload)
            if ok:
                delivered += 1
            else:
                failed += 1
        return {"delivered": delivered, "failed": failed, "skipped": skipped}

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        redis = None
        group_ready = False
        tick_sec = max(1, int(getattr(settings, "WEBHOOK_DELIVERY_TICK_SEC", 5) or 5))
        log.info("Webhook delivery worker started interval=%ss group=%s", tick_sec, self._group_name)
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
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=tick_sec)
                    except asyncio.TimeoutError:
                        continue
                    continue
                for _, messages in rows:
                    for stream_id, fields in messages:
                        try:
                            await self._process_stream_entry(stream_id=str(stream_id), fields=dict(fields or {}))
                            await redis.xack(self._stream_key, self._group_name, stream_id)
                        except Exception as exc:
                            log.warning("webhook delivery failed stream_id=%s: %s", stream_id, exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                message = str(exc or "").strip()
                log_periodic(
                    log,
                    logging.WARNING,
                    "webhook delivery loop error: %s",
                    exc,
                    key=f"webhook_delivery_loop:{type(exc).__name__}:{message[:160]}",
                    cooldown_sec=noisy_log_cooldown_sec(),
                )
                if is_redis_retryable_connection_error(exc):
                    await reset_redis_write_client()
                    redis = None
                    group_ready = False
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=tick_sec)
                except asyncio.TimeoutError:
                    continue
        log.info("Webhook delivery worker stopped")
