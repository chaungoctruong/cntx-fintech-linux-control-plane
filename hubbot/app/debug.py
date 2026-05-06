# -*- coding: utf-8 -*-
"""Debug logging for hubbot (writes to debug-*.log under project root)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

_DEBUG_LOG_PATH = (Path(__file__).resolve().parents[1] / "debug-d90ece.log").resolve()
_DEBUG_LOCK_LOG_PATH = (Path(__file__).resolve().parents[1] / "debug-eb6b19.log").resolve()


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
        with _DEBUG_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


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
        with _DEBUG_LOCK_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
