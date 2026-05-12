# -*- coding: utf-8 -*-
"""Debug JSONL logging for hubbot.

Each `_dbg`/`_dbg_lock` call appends to its dedicated JSONL file (legacy debug
trace) AND mirrors the same payload onto the stdlib logger so it lands in the
unified hubbot.log / hubbot.jsonl as well. Mirroring is best-effort and never
raises.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional


_radar_log = logging.getLogger("hubbot.debug.radar")
_lock_log = logging.getLogger("hubbot.debug.lock")


def _hubbot_log_dir() -> Path:
    raw_hubbot = os.getenv("HUBBOT_LOG_DIR", "").strip()
    if raw_hubbot:
        return Path(raw_hubbot).expanduser().resolve()
    raw_root = (os.getenv("CNTX_LOG_DIR") or os.getenv("LOG_DIR") or "").strip()
    if raw_root:
        return (Path(raw_root).expanduser().resolve() / "hubbot").resolve()
    return (Path(__file__).resolve().parents[2] / "logs" / "hubbot").resolve()


_DEBUG_LOG_PATH = (_hubbot_log_dir() / "hubbot-debug-radar.jsonl").resolve()
_DEBUG_LOCK_LOG_PATH = (_hubbot_log_dir() / "hubbot-debug-lock.jsonl").resolve()


def _mirror_to_logger(
    logger: logging.Logger,
    message: str,
    data: Optional[dict],
    hypothesis_id: str,
    run_id: str,
) -> None:
    try:
        logger.info(
            "%s",
            message,
            extra={
                "debug_data": data or {},
                "hypothesis_id": hypothesis_id,
                "debug_run_id": run_id,
            },
        )
    except Exception:
        pass


def _dbg(
    message: str,
    data: Optional[dict] = None,
    *,
    hypothesis_id: str = "",
    run_id: str = "pre-fix",
) -> None:
    try:
        payload = {
            "sessionId": "d90ece",
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": "hubbot/app/debug.py",
            "message": str(message),
            "data": data or {},
            "timestamp": int(time.time() * 1000),
        }
        _DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _DEBUG_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
    _mirror_to_logger(_radar_log, message, data, hypothesis_id, run_id)


def _dbg_lock(
    message: str,
    data: Optional[dict] = None,
    *,
    hypothesis_id: str = "",
    run_id: str = "pre-fix",
) -> None:
    try:
        payload = {
            "sessionId": "eb6b19",
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": "hubbot/app/debug.py",
            "message": str(message),
            "data": data or {},
            "timestamp": int(time.time() * 1000),
        }
        _DEBUG_LOCK_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _DEBUG_LOCK_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
    _mirror_to_logger(_lock_log, message, data, hypothesis_id, run_id)
