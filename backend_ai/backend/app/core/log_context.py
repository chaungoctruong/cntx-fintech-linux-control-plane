"""Structured logging context for the backend control-plane.

Lightweight by design: no third-party deps, no log-record mutation that breaks
existing handlers. Adds:

  * contextvars for request-scoped identifiers (request_id, user_id, account_id,
    deployment_id, runner_id, trace_id) so every log line emitted during a
    request inherits them automatically.
  * ContextEnricherFilter — injects those identifiers as attributes on each
    LogRecord so any formatter (text or JSON) can pick them up.
  * JsonFormatter — renders one-line JSON for `.jsonl` files; never raises.
  * install_context_filter — idempotent helper to wire the filter onto loggers.

Existing text-format `.log` files keep their current shape; JSON sinks live in
parallel `.jsonl` files so we can grep/jq without touching legacy tooling.
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
from typing import Any, Dict, Iterable, Optional


_REQUEST_ID_VAR: ContextVar[Optional[str]] = ContextVar("cntx_request_id", default=None)
_USER_ID_VAR: ContextVar[Optional[str]] = ContextVar("cntx_user_id", default=None)
_ACCOUNT_ID_VAR: ContextVar[Optional[int]] = ContextVar("cntx_account_id", default=None)
_DEPLOYMENT_ID_VAR: ContextVar[Optional[int]] = ContextVar("cntx_deployment_id", default=None)
_RUNNER_ID_VAR: ContextVar[Optional[str]] = ContextVar("cntx_runner_id", default=None)
_TRACE_ID_VAR: ContextVar[Optional[str]] = ContextVar("cntx_trace_id", default=None)

_HOSTNAME = socket.gethostname()


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def get_request_id() -> Optional[str]:
    return _REQUEST_ID_VAR.get()


def set_request_id(value: Optional[str]) -> None:
    _REQUEST_ID_VAR.set(value)


def bind_log_context(
    *,
    request_id: Optional[str] = None,
    user_id: Optional[str | int] = None,
    account_id: Optional[int] = None,
    deployment_id: Optional[int] = None,
    runner_id: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> None:
    """Set any subset of the context identifiers. Pass None to leave a slot untouched."""
    if request_id is not None:
        _REQUEST_ID_VAR.set(str(request_id))
    if user_id is not None:
        _USER_ID_VAR.set(str(user_id))
    if account_id is not None:
        try:
            _ACCOUNT_ID_VAR.set(int(account_id))
        except (TypeError, ValueError):
            _ACCOUNT_ID_VAR.set(None)
    if deployment_id is not None:
        try:
            _DEPLOYMENT_ID_VAR.set(int(deployment_id))
        except (TypeError, ValueError):
            _DEPLOYMENT_ID_VAR.set(None)
    if runner_id is not None:
        _RUNNER_ID_VAR.set(str(runner_id))
    if trace_id is not None:
        _TRACE_ID_VAR.set(str(trace_id))


def reset_log_context() -> None:
    _REQUEST_ID_VAR.set(None)
    _USER_ID_VAR.set(None)
    _ACCOUNT_ID_VAR.set(None)
    _DEPLOYMENT_ID_VAR.set(None)
    _RUNNER_ID_VAR.set(None)
    _TRACE_ID_VAR.set(None)


def current_log_context() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    request_id = _REQUEST_ID_VAR.get()
    if request_id:
        out["request_id"] = request_id
    user_id = _USER_ID_VAR.get()
    if user_id:
        out["user_id"] = user_id
    account_id = _ACCOUNT_ID_VAR.get()
    if account_id is not None:
        out["account_id"] = account_id
    deployment_id = _DEPLOYMENT_ID_VAR.get()
    if deployment_id is not None:
        out["deployment_id"] = deployment_id
    runner_id = _RUNNER_ID_VAR.get()
    if runner_id:
        out["runner_id"] = runner_id
    trace_id = _TRACE_ID_VAR.get()
    if trace_id:
        out["trace_id"] = trace_id
    return out


_CONTEXT_KEYS = ("request_id", "user_id", "account_id", "deployment_id", "runner_id", "trace_id")

_RESERVED_RECORD_KEYS = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "thread", "threadName", "taskName",
})

# Substrings (case-insensitive) that mark a field as sensitive — values get
# replaced with `[REDACTED]` before they hit any sink. Add new patterns here
# rather than per-call: scrubbing once at the boundary is the safest place.
_SENSITIVE_KEY_FRAGMENTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "x-api-key",
    "authorization",
    "auth_header",
    "cookie",
    "private_key",
    "credential",
    "bot_token",
    "secret_hex",
    "init_data",
    "initdata",
)

_REDACTED = "[REDACTED]"


def _is_sensitive_key(key: Any) -> bool:
    try:
        lowered = str(key).lower()
    except Exception:
        return False
    return any(frag in lowered for frag in _SENSITIVE_KEY_FRAGMENTS)


class ContextEnricherFilter(logging.Filter):
    """Annotate every record with current contextvar identifiers (no-op if unset)."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401 — Filter API
        ctx = current_log_context()
        for key, value in ctx.items():
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
    """Render LogRecords as one-line JSON. Never raises — falls back to a minimal payload."""

    def __init__(self, *, service_name: str) -> None:
        super().__init__()
        self._service = str(service_name or "backend")
        self._pid = os.getpid()

    def format(self, record: logging.LogRecord) -> str:
        try:
            ts_ms = int(record.created * 1000)
            payload: Dict[str, Any] = {
                "ts": ts_ms,
                "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created))
                + f".{int(record.msecs):03d}",
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
                "service": self._service,
                "pid": self._pid,
                "host": _HOSTNAME,
            }

            # Pull live contextvars first (handles records from child loggers
            # whose ancestor's logger.filter does not run during propagation).
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
    """Idempotently attach ContextEnricherFilter to the named loggers (root if none given)."""
    targets = loggers or ("",)
    for entry in targets:
        logger = logging.getLogger(entry) if isinstance(entry, str) else entry
        if any(isinstance(f, ContextEnricherFilter) for f in logger.filters):
            continue
        logger.addFilter(ContextEnricherFilter())
