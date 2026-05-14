from __future__ import annotations
from pathlib import Path
from typing import List, Union
from urllib.parse import urlparse
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

def _backend_root() -> Path:
    return Path(__file__).resolve().parents[1]

def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]

_ENV_FILE = (_backend_root() / ".env").resolve()
_PRODUCTION_ENV_NAMES = {"prod", "production"}
_DEFAULT_REDIS_URL = "redis://127.0.0.1:6379/0"
_PLACEHOLDER_SECRET_VALUES = {
    "",
    "change_me",
    "changeme",
    "change-me",
    "default",
    "secret",
    "password",
    "spider",
    "test",
    "example",
    "sample",
}


def _normalized_secret_value(value: str | None) -> str:
    return str(value or "").strip().strip("'\"").lower()


def _is_placeholder_secret(value: str | None) -> bool:
    normalized = _normalized_secret_value(value)
    if normalized in _PLACEHOLDER_SECRET_VALUES:
        return True
    return "change_me" in normalized or "changeme" in normalized


def _url_has_placeholder_secret(value: str | None) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    if _is_placeholder_secret(raw):
        return True
    try:
        parsed = urlparse(raw)
    except Exception:
        return False
    return _is_placeholder_secret(parsed.password)


def _url_missing_password(value: str | None) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    try:
        parsed = urlparse(raw)
    except Exception:
        return False
    return bool(parsed.scheme and parsed.netloc and not parsed.password)


def _is_production_env(value: str | None) -> bool:
    return str(value or "").strip().lower() in _PRODUCTION_ENV_NAMES


def _is_truthy_setting(value: Union[bool, str, None]) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


class Settings(BaseSettings):
    APP_ENV: str = "development"
    DEBUG: Union[bool, str] = False
    BACKEND_HOST: str = "127.0.0.1"
    BACKEND_PORT: int = 8001
    BACKEND_URL: str = ""
    RUNNER_CONTROL_PLANE_URL: str = ""  # Base URL runners use for short HTTP calls (bootstrap/register/heartbeat/events).
    APP_SECRET_KEY: str = "CHANGE_ME"
    APP_SECRET_OLD_KEYS: str = ""
    BACKEND_API_KEY: str = ""
    PUBLIC_BASE_URL: str = ""
    CORS_ORIGINS: List[str] = []
    TOKENS_TTL_SEC: int = 300

    # External WEB B connect (Telegram-safe: completion via server-to-server only)
    WEB_B_SHARED_SECRET: str = ""
    WEB_B_CONNECT_URL: str = ""
    BROKER_API_CTRADER_BASE_URL: str = ""
    BROKER_API_CTRADER_SHARED_KEY: str = ""
    BROKER_API_CTRADER_TIMEOUT_SEC: float = 15.0
    TELEGRAM_BOT_USERNAME: str = ""
    TELEGRAM_MINI_APP_SHORT_NAME: str = ""
    CONNECT_SESSION_TTL_SECONDS: int = 600
    CONNECT_RETURN_STARTAPP: str = "bot_connected"
    MINIAPP_BOTS_CACHE_TTL_SEC: float = 1.5
    MT5_BOT_CATALOG_DISABLED_CODES: List[str] = []
    BOT_CONTROL_COOLDOWN_SEC: int = 60
    PUBLIC_OVERVIEW_CACHE_TTL_SEC: float = 15.0
    PUBLIC_OVERVIEW_FEATURED_BOTS_MAX: int = 6
    # Frozen legacy broker-adapter settings. Keep only for archive/compatibility;
    # active product direction is Linux control plane + Windows MT5 runner.
    CTRADER_CLIENT_ID: str = ""
    CTRADER_CLIENT_SECRET: str = ""
    CTRADER_REDIRECT_URI: str = ""
    CTRADER_AUTH_URL: str = "https://id.ctrader.com/my/settings/openapi/grantingaccess/"
    CTRADER_TOKEN_URL: str = "https://openapi.ctrader.com/apps/token"
    CTRADER_PRODUCT: str = "web"
    CTRADER_DEFAULT_SCOPE: str = "accounts"
    CTRADER_BROKER_NAME: str = Field(
        default="ic_markets",
        validation_alias=AliasChoices("CTRADER_BROKER_NAME", "CTRADER_DEFAULT_BROKER_LABEL"),
    )
    CTRADER_HOST: str = "live"
    CTRADER_OAUTH_STATE_TTL_SEC: int = 600
    CTRADER_RUNNER_POLL_SEC: float = 5.0
    CTRADER_TOKEN_REFRESH_GRACE_SEC: int = 300
    CTRADER_ACCOUNTS_PROBE_TIMEOUT_SEC: float = 20.0
    CTRADER_MARKET_DATA_TIMEOUT_SEC: float = 20.0
    CTRADER_ORDER_TIMEOUT_SEC: float = 25.0
    CTRADER_LIVE_EXECUTION_ENABLED: bool = False

    TELEGRAM_BOT_TOKEN: str = ""
    DEV_CHAT_ID: str = ""
    ADMIN_TELEGRAM_IDS: str = ""
    MINIAPP_FULL_ACCESS_TELEGRAM_IDS: str = ""
    # Keep disabled until product is ready to expose the legal consent flow.
    # When enabled, connect/start/token flows require the current terms version.
    MINIAPP_TERMS_ENFORCEMENT_ENABLED: bool = False
 
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash-lite"
    AI_PROVIDER: str = Field(
        default="ollama",
        validation_alias=AliasChoices("AI_PROVIDER", "AI_CHAT_PROVIDER"),
    )
    # Legacy alias kept for old env files; new code reads AI_PROVIDER first.
    AI_CHAT_PROVIDER: str = ""
    AI_CHAT_OLLAMA_USE_GEMINI_FOR_SEARCH: bool = False
    AI_CHAT_OLLAMA_USE_GEMINI_FOR_COMPLEX: bool = False
    # Legacy local-model fallback only. Prefer OLLAMA_MODEL for Ollama and GEMINI_MODEL for Gemini.
    AI_MODEL: str = ""
    OLLAMA_BASE_URL: str = "http://127.0.0.1:11434"
    OLLAMA_MODEL: str = ""
    OLLAMA_TIMEOUT_SEC: float = 45.0
    OLLAMA_KEEP_ALIVE: str = "5m"
    AI_LOCAL_MAX_CONCURRENT: int = 1
    AI_LOCAL_MAX_QUEUED: int = 4
    AI_LOCAL_QUEUE_WAIT_SEC: float = 3.0
    AI_CHAT_RETRY_AFTER_SEC: int = 15
    AI_CHAT_TIMEOUT_SEC: float = 8.0
    AI_CHAT_IMMEDIATE_FALLBACK_ENABLED: bool = True
    AI_CHAT_SYNC_GENERATION_ENABLED: bool = True
    AI_CHAT_LEARNED_DIRECT_REPLY_ENABLED: bool = True
    AI_CHAT_LEARNED_DIRECT_REPLY_MIN_SCORE: float = 0.76
    AI_DEFERRED_QUEUE_ENABLED: bool = True
    AI_DEFERRED_QUEUE_MAX: int = 12
    AI_DEFERRED_QUEUE_RETRY_AFTER_SEC: int = 5
    AI_DEFERRED_QUEUE_MAX_ATTEMPTS: int = 2
    AI_DEFERRED_QUEUE_REQUEUE_DELAY_SEC: float = 2.0
    AI_DEFERRED_QUEUE_JOB_TTL_SEC: int = 1800
    AI_DEFERRED_QUEUE_KEY_PREFIX: str = "ai:deferred:job:"
    AI_CHAT_MEMORY_ENABLED: bool = True
    AI_CHAT_MEMORY_MAX_MESSAGES: int = 10
    AI_CHAT_MEMORY_TTL_SEC: int = 21600
    AI_CHAT_MEMORY_KEY_PREFIX: str = "ai:chat:memory:"
    AI_CHAT_DB_MEMORY_ENABLED: bool = True
    AI_CHAT_DB_CACHE_ENABLED: bool = True
    # 0 means PostgreSQL AI learning memory does not expire at read time.
    AI_CHAT_DB_CACHE_TTL_SEC: int = 0
    AI_CHAT_DB_CACHE_MIN_QUESTION_LEN: int = 4
    AI_CHAT_DB_CACHE_MAX_QUESTION_LEN: int = 1000
    AI_CHAT_DB_CACHE_MAX_ANSWER_LEN: int = 4000
    AI_CHAT_DB_MESSAGE_MAX_CONTENT: int = 4000
    AI_CHAT_DB_GLOBAL_LEARNING_ENABLED: bool = True
    AI_CHAT_DB_LEARNED_CONTEXT_LIMIT: int = 3
    AI_CHAT_DB_LEARNED_SCAN_LIMIT: int = 80
    AI_CHAT_DB_LEARNED_SIMILARITY_THRESHOLD: float = 0.42
    AI_PLATFORM_KNOWLEDGE_ENABLED: bool = True
    AI_PLATFORM_KNOWLEDGE_MAX_CHUNKS: int = 4
    AI_PLATFORM_KNOWLEDGE_SCAN_LIMIT: int = 120
    AI_PLATFORM_KNOWLEDGE_SIMILARITY_THRESHOLD: float = 0.38
    AI_PLATFORM_KNOWLEDGE_CHUNK_MAX_CHARS: int = 1800
    AI_PLATFORM_KNOWLEDGE_CONTEXT_CHARS: int = 2800
    AI_PLATFORM_KNOWLEDGE_MIN_TRUST_LEVEL: int = 40
    AI_PLATFORM_KNOWLEDGE_VECTOR_ENABLED: bool = False
    AI_PLATFORM_KNOWLEDGE_EMBEDDING_PROVIDER: str = "sentence_transformers"
    AI_PLATFORM_KNOWLEDGE_EMBEDDING_MODEL: str = "BAAI/bge-m3"
    AI_PLATFORM_KNOWLEDGE_EMBEDDING_DEVICE: str = "cpu"
    AI_PLATFORM_KNOWLEDGE_PGVECTOR_DIM: int = 1024
    AI_PLATFORM_KNOWLEDGE_VECTOR_TOP_K: int = 8
    AI_PLATFORM_KNOWLEDGE_VECTOR_MIN_SCORE: float = 0.45
    AI_PLATFORM_KNOWLEDGE_VECTOR_SCAN_LIMIT: int = 240
    AI_CONTINUOUS_LEARNING_ENABLED: bool = False
    AI_CONTINUOUS_LEARNING_INTERVAL_SEC: int = 900
    AI_CONTINUOUS_LEARNING_MANIFEST_PATH: str = ""
    AI_CONTINUOUS_LEARNING_ALLOWED_DOMAINS: List[str] = []
    AI_CONTINUOUS_LEARNING_REDIS_QUEUE: str = "ai:knowledge:ingest:requests"
    AI_CONTINUOUS_LEARNING_REDIS_RESULT_PREFIX: str = "ai:knowledge:ingest:result:"
    AI_CONTINUOUS_LEARNING_REDIS_RESULT_TTL_SEC: int = 86400
    AI_CONTINUOUS_LEARNING_MAX_JOBS_PER_TICK: int = 5
    AI_TRAINING_CAPTURE_ENABLED: bool = True
    AI_TRAINING_MIN_PROMPT_CHARS: int = 6
    AI_TRAINING_MAX_PROMPT_CHARS: int = 1800
    AI_TRAINING_MAX_COMPLETION_CHARS: int = 5000
    AI_TRAINING_DEFAULT_EXPORT_LIMIT: int = 1000
    AI_TRAINING_EXPORT_DIR: str = "ops/ai/training_exports"
    AI_CARE_CAMPAIGN_ENABLED: bool = False
    AI_CARE_DRY_RUN: bool = True
    AI_CARE_CHECK_INTERVAL_SEC: int = 60
    AI_CARE_TIMEZONE_OFFSET_HOURS: int = 7
    AI_CARE_MORNING_HOUR: int = 7
    AI_CARE_MORNING_WINDOW_MIN: int = 20
    AI_CARE_REQUIRE_REDIS: bool = True
    AI_CARE_INCLUDE_HUBBOT_CHATS: bool = True
    AI_CARE_HUBBOT_STATE_PATH: str = ""
    AI_CARE_INCLUDE_NEWS: bool = True
    AI_CARE_NEWS_USE_GEMINI: bool = False
    AI_CARE_NEWS_MAX_LINKS: int = 10
    AI_CARE_NEWS_CACHE_TTL_SEC: int = 1800
    AI_CARE_NEWS_STALE_HOURS: int = 720
    AI_CARE_DIGEST_CACHE_TTL_SEC: int = 86400
    AI_CARE_GOOGLE_NEWS_HL: str = "en-US"
    AI_CARE_GOOGLE_NEWS_GL: str = "US"
    AI_CARE_GOOGLE_NEWS_CEID: str = "US:en"
    AI_CARE_EXTRA_TELEGRAM_IDS: str = ""
    AI_CARE_OFFLINE_HOURS: int = 24
    AI_CARE_REENGAGE_COOLDOWN_HOURS: int = 24
    AI_CARE_MAX_SEND_PER_MIN: int = 120
    AI_CARE_SEND_BATCH_SIZE: int = 25
    AI_CARE_SEND_BATCH_SLEEP_SEC: float = 0.9
    AI_CARE_INCLUDE_NEWBIE_GUIDE: bool = True
    AI_CARE_MESSAGE_STYLE: str = "mixed"

    DATA_DIR: str = ""
    SERVICE_MODE: str = Field(
        default="local",
        validation_alias=AliasChoices("SERVICE_MODE", "RUNNER_MODE"),
    )
    LOG_RETENTION_DAYS: int = 7
    AUDIT_PER_USER_RETENTION_COUNT: int = 1000
    NOISY_LOG_COOLDOWN_SEC: int = 600
    ACCESS_LOG_NOISE_FILTER_ENABLED: bool = True
    SCHEMA_BOOTSTRAP_VERBOSE: bool = False
    DEBUG_TRACE_FILE_ENABLED: bool = False
    DEBUG_TRACE_FILE_PATH: str = ""
    DEBUG_TRACE_FILE_MAX_BYTES: int = 2000000
    REQUEST_LOG_ENABLED: bool = True
    STRUCTURED_LOG_FILE_ENABLED: bool = True
    CLIENT_TELEMETRY_ENABLED: bool = True
    CLIENT_EVENT_LOG_PATH: str = ""
    SLOW_REQUEST_MS_THRESHOLD: float = 1500.0
    # Distributed login lease (spec §6.5). Default fully OFF — flip per the
    # canary timeline. ENABLED toggles tracking; ENFORCED upgrades conflicts
    # from telemetry-only WARN to a 409 LOGIN_BUSY response.
    LOGIN_LEASE_ENABLED: bool = False
    LOGIN_LEASE_ENFORCED: bool = False
    LOGIN_LEASE_TTL_SEC: int = 60
    DRY_RUN: int = 1
    # Grace window after backend restart/deploy to avoid stale false negatives while streams/pings warm up.
    RUNTIME_RESTART_GRACE_SEC: int = 300

    DB_MODE: str = "postgres"
    POSTGRES_USER: str = "cntxlabserver_app"
    POSTGRES_PASSWORD: str = ""
    POSTGRES_HOST: str = "127.0.0.1"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "cntxlabserver"
    POSTGRES_POOL_MIN: int = 5
    POSTGRES_POOL_MAX: int = 50
    REDIS_URL: str = ""
    # Primary Redis master for all mutations (BLPOP, LPUSH, XADD, XACK, streams, queues).
    REDIS_WRITE_URL: str = ""
    # Optional read replica for GET / XREAD-only paths; empty = use write pool.
    REDIS_READ_URL: str = ""
    REDIS_MAX_CONNECTIONS: int = 200
    # Serialize migration / repair during multi-worker startup to avoid every HTTP worker repeating heavy boot work.
    STARTUP_SINGLETON_ENABLED: bool = True
    STARTUP_SINGLETON_KEY_PREFIX: str = "spider:backend:startup"
    STARTUP_SINGLETON_LOCK_TTL_SEC: int = 180
    STARTUP_SINGLETON_READY_TTL_SEC: int = 300
    STARTUP_SINGLETON_POLL_SEC: float = 0.5
    # Only one HTTP worker should own backend background jobs (consumers, reconciliation, cleanup).
    BACKGROUND_SINGLETON_ENABLED: bool = True
    BACKGROUND_SINGLETON_KEY: str = "spider:backend:background-owner"
    BACKGROUND_SINGLETON_TTL_SEC: int = 90
    BACKGROUND_SINGLETON_RENEW_SEC: int = 30
    CONTROL_PLANE_RECONCILE_INTERVAL_SEC: int = 30
    CONTROL_PLANE_RUNNER_STALE_SEC: int = 180
    CONTROL_PLANE_DEPLOYMENT_STALE_SEC: int = 180
    CONTROL_PLANE_STOP_RECONCILE_SEC: int = 30
    ACCOUNT_RUNTIME_START_GUARD_STALE_SEC: int = 180
    RUNNER_HEARTBEAT_WRITE_THROTTLE_SEC: float = 5.0
    RUNNER_RECOMMENDED_TRANSPORT: str = "redis_queue"  # Bootstrap hint only; command transport is Redis queue.
    RUNNER_SLOT_PROJECTION_EVENT_LOOKBACK_SEC: int = 21600
    RUNNER_CATALOG_SYNC_TTL_SEC: int = 600
    MINIAPP_DASHBOARD_CACHE_TTL_SEC: float = 5.0
    SCHEDULER_RUNNER_QUEUE_BACKLOG_THRESHOLD: int = 20
    CONFIG_RESTART_COMMAND_TIMEOUT_SEC: int = 180
    CONFIG_HOT_UPDATE_COMMAND_TIMEOUT_SEC: int = 180
    COMMAND_DELIVERY_REPLAY_ENABLED: bool = True
    COMMAND_DELIVERY_REPLAY_INTERVAL_SEC: int = 15
    COMMAND_DELIVERY_REPLAY_BATCH_SIZE: int = 100
    COMMAND_DELIVERY_REPLAY_OLDER_THAN_SEC: int = 10
    COMMAND_DELIVERY_REPLAY_STALE_DEGRADED_SEC: int = 90
    COMMAND_DELIVERY_PROCESSING_REQUEUE_ENABLED: bool = True
    COMMAND_DELIVERY_START_QUEUE_TIMEOUT_SEC: int = 60
    COMMAND_DELIVERY_PROCESSING_REQUEUE_TIMEOUT_SEC: int = 180
    COMMAND_DELIVERY_PROCESSING_REQUEUE_BATCH_SIZE: int = 100
    COMMAND_DELIVERY_PROCESSING_REQUEUE_SCAN_LIMIT: int = 500
    COMMAND_DELIVERY_DEDUPE_TTL_SEC: int = 604800
    STICKY_SLOT_MIDNIGHT_RELEASE_ENABLED: bool = True
    STICKY_SLOT_MIDNIGHT_RELEASE_TIMEZONE: str = "Asia/Ho_Chi_Minh"
    STICKY_SLOT_MIDNIGHT_RELEASE_BATCH_SIZE: int = 500
    CONTROL_PLANE_EVENT_CONSUMER_ENABLED: bool = True
    CONTROL_PLANE_EVENT_CONSUMER_GROUP: str = "control-plane-event-audit"
    CONTROL_PLANE_EVENT_CONSUMER_BLOCK_MS: int = 5000
    EVENT_STREAM_MAXLEN: int = 20000
    OPS_VERIFICATION_BACKLOG_THRESHOLD: int = 20
    OPS_COMMAND_BACKLOG_THRESHOLD: int = 40
    OPS_EVENT_BACKLOG_THRESHOLD: int = 100
    ZINGSERVER_API_BASE_URL: str = "https://api.zingserver.com"
    ZINGSERVER_API_TOKEN: str = ""
    ZINGSERVER_API_TIMEOUT_SEC: float = 15.0
    ZINGSERVER_DEFAULT_DATACENTER: str = ""
    ZINGSERVER_DEFAULT_PLAN_ID: str = ""
    ZINGSERVER_DEFAULT_OS_ID: int = 0
    ZINGSERVER_DEFAULT_LOCATION_ID: str = ""
    ZINGSERVER_DEFAULT_PERIOD: str = "monthly"
    ZINGSERVER_MAX_ACTIVE_CLOUDS: int = 3
    ZINGSERVER_MAX_CREATE_QUANTITY: int = 1
    ZINGSERVER_MAX_CREATE_COST_VND: int = 2000000
    WEBHOOK_DELIVERY_ENABLED: bool = True
    WEBHOOK_DELIVERY_TICK_SEC: int = 5
    # TradingView alert ingress: optional shared secret (header X-TradingView-Secret, query `secret`, or JSON field `secret`).
    # Empty = no auth (dev only); set in production.
    TRADINGVIEW_WEBHOOK_SECRET: str = ""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    def resolved_data_dir(self) -> str:
        if str(self.DATA_DIR or "").strip():
            return str(Path(self.DATA_DIR).expanduser().resolve())
        return str((_project_root() / "data").resolve())

    @property
    def RUNNER_MODE(self) -> str:
        return self.SERVICE_MODE

    @property
    def CTRADER_DEFAULT_BROKER_LABEL(self) -> str:
        return self.CTRADER_BROKER_NAME

    def resolved_public_base_url(self) -> str:
            if (self.PUBLIC_BASE_URL or "").strip():
                return (self.PUBLIC_BASE_URL or "").strip().rstrip("/")
            host = (self.BACKEND_HOST or "").strip()
            return f"http://{host}:{self.BACKEND_PORT}"

    def resolved_backend_url(self) -> str:
        if (self.BACKEND_URL or "").strip():
            return (self.BACKEND_URL or "").strip().rstrip("/")
        host = (self.BACKEND_HOST or "").strip()
        return f"http://{host}:{self.BACKEND_PORT}"

    def validate_for_prod(self) -> None:
        if not _is_production_env(self.APP_ENV):
            return

        app_secret = str(self.APP_SECRET_KEY or "").strip()
        backend_api_key = str(self.BACKEND_API_KEY or "").strip()
        if _is_placeholder_secret(app_secret) or len(app_secret) < 32:
            raise RuntimeError("APP_SECRET_KEY must be a non-placeholder value with at least 32 characters in production")
        if _is_placeholder_secret(backend_api_key) or len(backend_api_key) < 24:
            raise RuntimeError("BACKEND_API_KEY must be a non-placeholder value with at least 24 characters in production")
        if _is_truthy_setting(self.DEBUG) or _is_truthy_setting(self.DEBUG_TRACE_FILE_ENABLED):
            raise RuntimeError("DEBUG and DEBUG_TRACE_FILE_ENABLED must be disabled in production")
        if self.DB_MODE.lower() == "postgres":
            if _is_placeholder_secret(self.POSTGRES_PASSWORD):
                raise RuntimeError("POSTGRES_PASSWORD must be a non-placeholder value in production")
        redis_write_url = (self.REDIS_WRITE_URL or "").strip() or (self.REDIS_URL or "").strip() or _DEFAULT_REDIS_URL
        for name, value in (
            ("REDIS_WRITE_URL", redis_write_url),
            ("REDIS_URL", self.REDIS_URL),
            ("REDIS_READ_URL", self.REDIS_READ_URL),
        ):
            if _url_missing_password(value):
                raise RuntimeError(f"{name} must include a Redis password in production")
            if _url_has_placeholder_secret(value):
                raise RuntimeError(f"{name} must not contain a placeholder Redis password in production")

    def secret_old_keys(self) -> list[str]:
        raw = str(self.APP_SECRET_OLD_KEYS or "").strip()
        if not raw:
            return []
        return [item.strip() for item in raw.split(",") if item.strip()]


settings = Settings()

settings.DATA_DIR = settings.resolved_data_dir()

if not (settings.PUBLIC_BASE_URL or "").strip():
    settings.PUBLIC_BASE_URL = settings.resolved_public_base_url()

if not (settings.BACKEND_URL or "").strip():
    settings.BACKEND_URL = settings.resolved_backend_url()

# Canonical Redis write URL (master): explicit write + legacy REDIS_URL + default
_rw = (
    (settings.REDIS_WRITE_URL or "").strip()
    or (settings.REDIS_URL or "").strip()
    or _DEFAULT_REDIS_URL
)
if not (settings.REDIS_WRITE_URL or "").strip():
    settings.REDIS_WRITE_URL = _rw

settings.validate_for_prod()
