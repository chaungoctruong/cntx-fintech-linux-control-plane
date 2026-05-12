"""Structured logging context for hubbot.

Mirrors backend_ai/backend/app/core/log_context.py with the same surface but
isolated so hubbot has zero coupling to the backend package. Stdlib only.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import time
import traceback
import uuid
from contextvars import ContextVar
from typing import Any, Dict, Optional


_REQUEST_ID_VAR: ContextVar[Optional[str]] = ContextVar("hubbot_request_id", default=None)
_USER_ID_VAR: ContextVar[Optional[str]] = ContextVar("hubbot_user_id", default=None)
_CHAT_ID_VAR: ContextVar[Optional[str]] = ContextVar("hubbot_chat_id", default=None)
_UPDATE_ID_VAR: ContextVar[Optional[int]] = ContextVar("hubbot_update_id", default=None)
_HANDLER_VAR: ContextVar[Optional[str]] = ContextVar("hubbot_handler", default=None)

_HOSTNAME = socket.gethostname()


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def bind_log_context(
    *,
    request_id: Optional[str] = None,
    user_id: Optional[str | int] = None,
    chat_id: Optional[str | int] = None,
    update_id: Optional[int] = None,
    handler: Optional[str] = None,
) -> None:
    if request_id is not None:
        _REQUEST_ID_VAR.set(str(request_id))
    if user_id is not None:
        _USER_ID_VAR.set(str(user_id))
    if chat_id is not None:
        _CHAT_ID_VAR.set(str(chat_id))
    if update_id is not None:
        try:
            _UPDATE_ID_VAR.set(int(update_id))
        except (TypeError, ValueError):
            _UPDATE_ID_VAR.set(None)
    if handler is not None:
        _HANDLER_VAR.set(str(handler))


def reset_log_context() -> None:
    _REQUEST_ID_VAR.set(None)
    _USER_ID_VAR.set(None)
    _CHAT_ID_VAR.set(None)
    _UPDATE_ID_VAR.set(None)
    _HANDLER_VAR.set(None)


def current_log_context() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    rid = _REQUEST_ID_VAR.get()
    if rid:
        out["request_id"] = rid
    uid = _USER_ID_VAR.get()
    if uid:
        out["user_id"] = uid
    cid = _CHAT_ID_VAR.get()
    if cid:
        out["chat_id"] = cid
    upid = _UPDATE_ID_VAR.get()
    if upid is not None:
        out["update_id"] = upid
    handler = _HANDLER_VAR.get()
    if handler:
        out["handler"] = handler
    return out


_CONTEXT_KEYS = ("request_id", "user_id", "chat_id", "update_id", "handler")

_RESERVED_RECORD_KEYS = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "thread", "threadName", "taskName",
})

_SENSITIVE_KEY_FRAGMENTS = (
    "password", "passwd", "secret", "token", "api_key", "apikey", "x-api-key",
    "authorization", "auth_header", "cookie", "private_key", "credential",
    "bot_token", "secret_hex", "init_data", "initdata",
)
_REDACTED = "[REDACTED]"


def _is_sensitive_key(key: Any) -> bool:
    try:
        lowered = str(key).lower()
    except Exception:
        return False
    return any(frag in lowered for frag in _SENSITIVE_KEY_FRAGMENTS)


class ContextEnricherFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in current_log_context().items():
            if not hasattr(record, key):
                setattr(record, key, value)
        return True


def _safe_jsonable(value: Any, *, _depth: int = 0) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if _depth > 4:
        return str(value)[:512]
    if isinstance(value, (list, tuple, set)):
        return [_safe_jsonable(v, _depth=_depth + 1) for v in list(value)[:64]]
    if isinstance(value, dict):
        return {
            str(k)[:128]: (_REDACTED if _is_sensitive_key(k) else _safe_jsonable(v, _depth=_depth + 1))
            for k, v in list(value.items())[:64]
        }
    return str(value)[:1024]


class JsonFormatter(logging.Formatter):
    def __init__(self, *, service_name: str = "hubbot") -> None:
        super().__init__()
        self._service = str(service_name or "hubbot")
        self._pid = os.getpid()

    def format(self, record: logging.LogRecord) -> str:
        try:
            payload: Dict[str, Any] = {
                "ts": int(record.created * 1000),
                "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created))
                + f".{int(record.msecs):03d}",
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
                "service": self._service,
                "pid": self._pid,
                "host": _HOSTNAME,
            }

            # Read live contextvars; logger.addFilter on root does not re-run
            # for records originating from child loggers, so we cannot rely on
            # the filter to inject context. Fetch it here at format time.
            for key, value in current_log_context().items():
                if value is not None and key not in payload:
                    payload[key] = value

            for key in _CONTEXT_KEYS:
                value = getattr(record, key, None)
                if value is not None and key not in payload:
                    payload[key] = value

            for key, value in record.__dict__.items():
                if (
                    key in _RESERVED_RECORD_KEYS
                    or key.startswith("_")
                    or key in payload
                    or key in _CONTEXT_KEYS
                ):
                    continue
                payload[key] = _REDACTED if _is_sensitive_key(key) else _safe_jsonable(value)

            if record.exc_info:
                exc_type = getattr(record.exc_info[0], "__name__", "") if record.exc_info[0] else ""
                payload["exc_type"] = exc_type
                payload["exc"] = "".join(traceback.format_exception(*record.exc_info)).strip()
            elif record.exc_text:
                payload["exc"] = str(record.exc_text)

            return json.dumps(payload, ensure_ascii=False, default=str)
        except Exception:
            try:
                return json.dumps(
                    {"level": record.levelname, "msg": str(record.getMessage())[:1024], "logger": record.name},
                    ensure_ascii=False,
                )
            except Exception:
                return '{"level":"ERROR","msg":"log_format_failed"}'


def install_context_filter(*loggers: str | logging.Logger) -> None:
    targets = loggers or ("",)
    for entry in targets:
        logger = logging.getLogger(entry) if isinstance(entry, str) else entry
        if any(isinstance(f, ContextEnricherFilter) for f in logger.filters):
            continue
        logger.addFilter(ContextEnricherFilter())
