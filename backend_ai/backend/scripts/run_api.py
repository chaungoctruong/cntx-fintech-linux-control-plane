#!/usr/bin/env python3
"""
Launcher cho control-plane API: đọc INSTANCE_ID (PM2 instance_var), tính port 8001+N,
rồi exec uvicorn để PM2 nhận SIGTERM trực tiếp tại process uvicorn.
Chạy từ backend_ai/backend với: python scripts/run_api.py (hoặc venv/bin/python3 scripts/run_api.py).
"""
from __future__ import annotations

import os
import sys
import time

# Đảm bảo cwd là backend_ai/backend và PYTHONPATH có "."
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_BACKEND_DIR)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

# Đọc INSTANCE_ID do PM2 set (instance_var). Fallback NODE_APP_INSTANCE để chịu lỗi cấu hình.
INSTANCE_ID = int(os.environ.get("INSTANCE_ID") or os.environ.get("NODE_APP_INSTANCE", 0))
PORT_BASE = int(os.environ.get("API_PORT_BASE", "8001"))
PORT = PORT_BASE + INSTANCE_ID
API_HOST = (
    os.environ.get("API_HOST")
    or os.environ.get("BACKEND_HOST")
    or "127.0.0.1"
)
os.environ["PORT"] = str(PORT)

# Trễ khởi động (staggered start): instance 0 ngay, 1 trễ 0.5s, 2 trễ 1s, 3 trễ 1.5s → tránh đâm sầm DB
time.sleep(INSTANCE_ID * 0.5)

VENV_PYTHON = os.path.join(_BACKEND_DIR, "venv", "bin", "python3")
if not os.path.isfile(VENV_PYTHON):
    VENV_PYTHON = sys.executable

UVICORN_ARGS = [
    VENV_PYTHON,
    "-m",
    "uvicorn",
    "app.main:app",
    "--host",
    API_HOST,
    "--port",
    str(PORT),
    "--workers",
    "1",
    "--limit-concurrency",
    "1000",
]
os.execve(VENV_PYTHON, UVICORN_ARGS, os.environ)
