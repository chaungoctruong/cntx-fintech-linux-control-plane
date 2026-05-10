"""Runtime settings for GsAlgoVIP.

Multi-tenant model: one OS process per tenant. The platform spawns a separate
process for each tenant with a distinct environment block (ENV vars). The bot
itself does NOT do tenant routing in code — it just trusts the env it gets.

Env vars (all platform-injected):
- TENANT_ID         per-tenant identifier (e.g. user_42)
- INSTANCE_ID       per-process identifier (e.g. user_42-slot_a)
- APP_HOST/APP_PORT bind addr (one tenant = one port)
- DATABASE_URL      bot's OWN PostgreSQL DB (per tenant or per schema)
- WEBHOOK_SECRET    TradingView shared secret for THIS tenant
- MT5_*             this tenant's MT5 credentials and slot
- TELEGRAM_*        optional telemetry
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or default).strip()


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name, "true" if default else "false").lower()
    return raw in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except Exception:
        return default


def _default_log_path(root_dir: Path) -> Path:
    raw_log_dir = (_env("CNTX_LOG_DIR", "") or _env("LOG_DIR", "")).strip()
    if raw_log_dir:
        return (Path(raw_log_dir).expanduser().resolve() / "runner" / "gsalgovip.log").resolve()
    if root_dir.parent.name == "bot-trading":
        return (root_dir.parents[1] / "logs" / "runner" / "gsalgovip.log").resolve()
    return (root_dir / "logs" / "gsalgovip.log").resolve()


def _env_symbol_map(name: str) -> dict[str, str]:
    raw = _env(name, "")
    symbol_map: dict[str, str] = {}
    for item in raw.split(","):
        if not item.strip() or ":" not in item:
            continue
        source, target = item.split(":", 1)
        source = source.strip().upper()
        target = target.strip()
        if source and target:
            symbol_map[source] = target
    return symbol_map


def _mask_db_url(url: str) -> str:
    """Mask credentials in a DATABASE_URL for safe logging."""
    if not url:
        return ""
    try:
        scheme_sep = "://"
        if scheme_sep not in url:
            return url
        scheme, rest = url.split(scheme_sep, 1)
        if "@" not in rest:
            return url
        creds, host = rest.split("@", 1)
        return f"{scheme}{scheme_sep}***:***@{host}"
    except Exception:
        return "***"


@dataclass(slots=True)
class Settings:
    tenant_id: str
    instance_id: str
    app_host: str
    app_port: int
    database_url: str
    log_path: Path
    webhook_secret: str
    webhook_path: str
    trading_enabled: bool
    dry_run: bool
    poll_interval_sec: float
    default_volume: float
    max_spread_points: float
    max_entry_drift_points: float
    max_slippage_points: int
    symbol_map: dict[str, str]
    mt5_terminal_path: str
    mt5_login: int
    mt5_password: str
    mt5_server: str
    mt5_magic: int
    telegram_bot_token: str
    telegram_chat_id: str

    @property
    def database_url_safe(self) -> str:
        """Credential-masked DATABASE_URL safe to write to logs."""
        return _mask_db_url(self.database_url)

    @property
    def runtime_label(self) -> str:
        """Compact identifier for log lines / metric labels.

        Format: ``<tenant_id or '-'> @ <instance_id or pid>``.
        """
        return f"{self.tenant_id or '-'}@{self.instance_id or os.getpid()}"

    @classmethod
    def from_env(cls, root_dir: Path) -> "Settings":
        _load_env_file(root_dir / ".env")
        log_default = _default_log_path(root_dir)
        log_path = Path(_env("LOG_PATH", str(log_default)))
        if not log_path.is_absolute():
            log_path = (root_dir / log_path).resolve()
        else:
            log_path = log_path.resolve()
        return cls(
            tenant_id=_env("TENANT_ID", ""),
            instance_id=_env("INSTANCE_ID", ""),
            app_host=_env("APP_HOST", "0.0.0.0"),
            app_port=int(_env("APP_PORT", "8017")),
            database_url=_env("DATABASE_URL", ""),
            log_path=log_path,
            webhook_secret=_env("WEBHOOK_SECRET", ""),
            webhook_path=_env("WEBHOOK_PATH", "/webhook/tradingview"),
            trading_enabled=_env_bool("TRADING_ENABLED", False),
            dry_run=_env_bool("DRY_RUN", True),
            poll_interval_sec=max(0.2, _env_float("POLL_INTERVAL_SEC", 1.0)),
            default_volume=max(0.01, _env_float("DEFAULT_VOLUME", 0.01)),
            max_spread_points=max(0.0, _env_float("MAX_SPREAD_POINTS", 0.0)),
            max_entry_drift_points=max(0.0, _env_float("MAX_ENTRY_DRIFT_POINTS", 0.0)),
            max_slippage_points=int(_env("MAX_SLIPPAGE_POINTS", "30")),
            symbol_map=_env_symbol_map("SYMBOL_MAP"),
            mt5_terminal_path=_env("MT5_TERMINAL_PATH", ""),
            mt5_login=int(_env("MT5_LOGIN", "0")),
            mt5_password=_env("MT5_PASSWORD", ""),
            mt5_server=_env("MT5_SERVER", ""),
            mt5_magic=int(_env("MT5_MAGIC", "0")),
            telegram_bot_token=_env("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=_env("TELEGRAM_CHAT_ID", ""),
        )
