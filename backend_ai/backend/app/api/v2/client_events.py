"""Receive structured error/event reports from the frontend Mini App.

Stores raw events as JSONL under `logs/frontend/client-events.jsonl` and mirrors
each one onto the stdlib logger so it also lands in the standard service log.

This is a low-trust endpoint: anyone can POST. Mitigations:
  * Hard caps on batch size and per-field length
  * No code execution / no eval — fields are normalised strings
  * Toggleable via CLIENT_TELEMETRY_ENABLED env (default on)
  * Existing rate limiter middleware applies
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.core.log_context import bind_log_context, new_request_id
from app.logging_config import resolve_log_dir


router = APIRouter(prefix="/system", tags=["system", "telemetry"])

log = logging.getLogger("api.client_event")

_MAX_EVENTS_PER_REQUEST = 50
_MAX_FIELD_LEN = 8000


class ClientEvent(BaseModel):
    type: str = Field(default="error")
    message: str = ""
    severity: str = "error"
    occurred_at: int | None = None
    page_url: str | None = None
    user_agent: str | None = None
    stack: str | None = None
    extra: dict | None = None


class ClientEventBatch(BaseModel):
    events: list[ClientEvent] = Field(default_factory=list)
    session_id: str | None = None
    release: str | None = None


def _client_event_log_path() -> Path:
    raw = (os.getenv("CLIENT_EVENT_LOG_PATH") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (resolve_log_dir() / "frontend" / "client-events.jsonl").resolve()


def _client_telemetry_enabled() -> bool:
    raw = (os.getenv("CLIENT_TELEMETRY_ENABLED") or "1").strip().lower()
    return raw in ("1", "true", "yes", "y", "on")


def _truncate(value: str | None, max_len: int = _MAX_FIELD_LEN) -> str | None:
    if value is None:
        return None
    s = str(value)
    return s if len(s) <= max_len else s[:max_len] + "...[truncated]"


@router.post("/client-events")
async def receive_client_events(payload: ClientEventBatch, request: Request) -> dict[str, Any]:
    if not _client_telemetry_enabled():
        return {"accepted": 0, "skipped": True, "reason": "disabled"}

    events = payload.events[:_MAX_EVENTS_PER_REQUEST]
    if not events:
        return {"accepted": 0}

    request_id = (request.headers.get("x-request-id") or "").strip()[:64] or new_request_id()
    bind_log_context(request_id=request_id)

    log_path = _client_event_log_path()
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    accepted = 0
    now_ms = int(time.time() * 1000)
    client_ip = (request.client.host if request.client else "") or ""
    base = {
        "received_at": now_ms,
        "session_id": _truncate(payload.session_id, 64),
        "release": _truncate(payload.release, 64),
        "client_ip": client_ip,
        "request_id": request_id,
    }

    lines: list[str] = []
    for raw in events:
        record = {
            **base,
            "type": _truncate(raw.type, 64) or "error",
            "severity": _truncate(raw.severity, 32) or "error",
            "message": _truncate(raw.message),
            "occurred_at": int(raw.occurred_at) if raw.occurred_at else now_ms,
            "page_url": _truncate(raw.page_url, 1024),
            "user_agent": _truncate(raw.user_agent, 512),
            "stack": _truncate(raw.stack),
            "extra": raw.extra if isinstance(raw.extra, dict) else None,
        }
        try:
            lines.append(json.dumps(record, ensure_ascii=False, default=str))
            accepted += 1
        except Exception:
            continue

        log.warning(
            "client.%s %s",
            record["severity"],
            (record["message"] or "")[:200],
            extra={
                "client_event_type": record["type"],
                "client_severity": record["severity"],
                "client_page": record["page_url"] or "",
                "client_session": record["session_id"] or "",
            },
        )

    if lines:
        try:
            with log_path.open("a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except Exception as exc:
            log.warning("client_event_log_write_failed: %s", exc)

    return {"accepted": accepted}
