from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from app.settings import settings

_MARKER_LOCK = threading.Lock()
_EMIT_MARKERS: dict[str, float] = {}


def noisy_log_cooldown_sec(default: int = 600) -> int:
    raw = int(getattr(settings, "NOISY_LOG_COOLDOWN_SEC", default) or default)
    return max(1, raw)


def should_emit_periodic(key: str, cooldown_sec: int) -> bool:
    marker = str(key or "").strip()
    if not marker:
        return True
    cooldown = max(1, int(cooldown_sec or 1))
    now = time.time()
    with _MARKER_LOCK:
        expiry = float(_EMIT_MARKERS.get(marker) or 0.0)
        if expiry > now:
            return False
        _EMIT_MARKERS[marker] = now + cooldown
        expired = [name for name, ts in _EMIT_MARKERS.items() if ts <= now]
        for name in expired:
            _EMIT_MARKERS.pop(name, None)
    return True


def log_periodic(
    logger: logging.Logger,
    level: int,
    msg: str,
    *args: Any,
    key: str,
    cooldown_sec: int,
) -> bool:
    if not should_emit_periodic(key, cooldown_sec):
        return False
    logger.log(level, msg, *args)
    return True


def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _default_log_dir() -> Path:
    raw = (os.getenv("CNTX_LOG_DIR") or os.getenv("LOG_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (_project_root() / "logs").resolve()


def debug_trace_enabled() -> bool:
    return bool(getattr(settings, "DEBUG_TRACE_FILE_ENABLED", False))


def debug_trace_file_path() -> Path:
    raw = str(getattr(settings, "DEBUG_TRACE_FILE_PATH", "") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (_default_log_dir() / "backend" / "debug-trace.jsonl").resolve()


def debug_trace_file_max_bytes(default: int = 2_000_000) -> int:
    raw = int(getattr(settings, "DEBUG_TRACE_FILE_MAX_BYTES", default) or default)
    return max(64 * 1024, raw)


def append_debug_trace(*, location: str, message: str, data: dict[str, Any], hypothesis_id: str) -> None:
    if not debug_trace_enabled():
        return
    try:
        path = debug_trace_file_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "sessionId": "fc5226",
            "runId": "saas-refactor-1",
            "hypothesisId": hypothesis_id,
            "location": str(location),
            "message": str(message),
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


def cleanup_debug_trace_file() -> dict[str, Any]:
    path = debug_trace_file_path()
    if not path.exists():
        return {"path": str(path), "deleted": False, "truncated": False, "size_bytes": 0}

    try:
        stat = path.stat()
        size_bytes = int(stat.st_size or 0)
        if not debug_trace_enabled():
            path.unlink(missing_ok=True)
            return {"path": str(path), "deleted": True, "truncated": False, "size_bytes": size_bytes, "reason": "disabled"}

        retention_days = max(1, int(getattr(settings, "LOG_RETENTION_DAYS", 7) or 7))
        cutoff_ts = time.time() - (retention_days * 24 * 3600)
        if float(stat.st_mtime) < cutoff_ts:
            path.unlink(missing_ok=True)
            return {"path": str(path), "deleted": True, "truncated": False, "size_bytes": size_bytes, "reason": "retention_expired"}

        max_bytes = debug_trace_file_max_bytes()
        if size_bytes <= max_bytes:
            return {"path": str(path), "deleted": False, "truncated": False, "size_bytes": size_bytes}

        keep_bytes = max(max_bytes // 2, 64 * 1024)
        with path.open("rb") as f:
            if size_bytes > keep_bytes:
                f.seek(-keep_bytes, os.SEEK_END)
            tail = f.read()
        with path.open("wb") as f:
            f.write(tail)
        return {
            "path": str(path),
            "deleted": False,
            "truncated": True,
            "size_bytes": size_bytes,
            "remaining_bytes": len(tail),
        }
    except Exception as exc:
        return {"path": str(path), "deleted": False, "truncated": False, "error": str(exc)}
