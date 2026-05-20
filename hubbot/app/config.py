# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from dotenv import load_dotenv

load_dotenv()

LOG_LEVEL: str = (os.getenv("LOG_LEVEL", "INFO") or "INFO").strip().upper()

BOT_TOKEN: str = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
BACKEND_URL: str = (os.getenv("BACKEND_URL", "http://127.0.0.1:8001") or "").strip().rstrip("/")
BACKEND_API_KEY: str = (os.getenv("BACKEND_API_KEY") or "").strip()
PUBLIC_BASE_URL: str = (os.getenv("PUBLIC_BASE_URL") or BACKEND_URL).strip().rstrip("/")
MINIAPP_RELEASE: str = (
    os.getenv("MINIAPP_RELEASE")
    or os.getenv("NEXT_PUBLIC_RELEASE")
    or os.getenv("VERCEL_GIT_COMMIT_SHA")
    or ""
).strip()

AI_ENABLED: bool = (os.getenv("AI_ENABLED", "1") or "1").strip().lower() in ("1", "true", "yes", "y", "on")
AI_COOLDOWN_SEC: float = float((os.getenv("AI_COOLDOWN_SEC", "1.2") or "1.2").strip())
AI_MAX_CHARS: int = int((os.getenv("AI_MAX_CHARS", "1200") or "1200").strip())
RADAR_LOG_ALL_MESSAGES: bool = (
    (os.getenv("RADAR_LOG_ALL_MESSAGES", "0") or "0").strip().lower() in ("1", "true", "yes", "y", "on")
)

API_TIMEOUT_SEC: float = float((os.getenv("API_TIMEOUT_SEC", "20") or "20").strip())
API_RETRIES: int = int((os.getenv("API_RETRIES", "2") or "2").strip())
API_MAX_KEEPALIVE: int = int((os.getenv("API_MAX_KEEPALIVE", "50") or "50").strip())
API_MAX_CONNECTIONS: int = int((os.getenv("API_MAX_CONNECTIONS", "500") or "500").strip())
API_MAX_CONCURRENCY: int = int((os.getenv("API_MAX_CONCURRENCY", "250") or "250").strip())
API_CACHE_TTL_SEC: float = float((os.getenv("API_CACHE_TTL_SEC", "1.5") or "1.5").strip())
BOT_BUTTON_COOLDOWN_SEC: float = float((os.getenv("BOT_BUTTON_COOLDOWN_SEC", "2.0") or "2.0").strip())
CALLBACK_DEDUP_TTL_SEC: float = float((os.getenv("CALLBACK_DEDUP_TTL_SEC", "2.0") or "2.0").strip())
TELEGRAM_MAX_CONCURRENT_UPDATES: int = int(
    (os.getenv("TELEGRAM_MAX_CONCURRENT_UPDATES", "32") or "32").strip()
)
REDIS_URL: str = (os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0") or "redis://127.0.0.1:6379/0").strip()
INFLIGHT_BOT_CONTROL_TTL_SEC: int = int((os.getenv("INFLIGHT_BOT_CONTROL_TTL_SEC", "8") or "8").strip())
# Safety-first: do NOT auto-start bot after profile transitions to CONNECTED unless explicitly enabled.
AUTO_RETRY_START_ON_PROFILE_READY: bool = (
    (os.getenv("AUTO_RETRY_START_ON_PROFILE_READY", "0") or "0").strip().lower() in ("1", "true", "yes", "y", "on")
)
MAX_RECONCILE_TASKS: int = int((os.getenv("MAX_RECONCILE_TASKS", "200") or "200").strip())

SYSTEM_BOT_TOKEN: str = (os.getenv("SYSTEM_BOT_TOKEN") or "").strip()
DEV_CHAT_ID: str = (os.getenv("DEV_CHAT_ID", "") or "").strip()

USE_WEBHOOK: bool = (os.getenv("TELEGRAM_USE_WEBHOOK", "0") or "0").strip().lower() in ("1", "true", "yes", "y", "on")
WEBHOOK_URL: str = (os.getenv("TELEGRAM_WEBHOOK_URL") or "").strip()
WEBHOOK_PORT: int = int((os.getenv("TELEGRAM_WEBHOOK_PORT", "8081") or "8081").strip())
WEBHOOK_LISTEN: str = (os.getenv("TELEGRAM_WEBHOOK_LISTEN", "0.0.0.0") or "0.0.0.0").strip()
WEBHOOK_PATH: str = (os.getenv("TELEGRAM_WEBHOOK_PATH", "/telegram/webhook") or "/telegram/webhook").strip()
WEBHOOK_SECRET_TOKEN: str = (os.getenv("TELEGRAM_WEBHOOK_SECRET_TOKEN") or "").strip()
