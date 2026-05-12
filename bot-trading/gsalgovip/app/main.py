from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from .config import Settings
from .logger import build_logger
from .state_store import StateStore
from .webhook import build_router


ROOT_DIR = Path(__file__).resolve().parents[1]
settings = Settings.from_env(ROOT_DIR)
logger = build_logger(settings.log_path, instance_label=settings.runtime_label)
logger.info(
    "gsalgovip_boot tenant_id=%s instance_id=%s db=%s host=%s port=%s "
    "webhook_path=%s dry_run=%s trading_enabled=%s",
    settings.tenant_id or "-",
    settings.instance_id or "-",
    settings.database_url_safe,
    settings.app_host,
    settings.app_port,
    settings.webhook_path,
    settings.dry_run,
    settings.trading_enabled,
)
store = StateStore(settings.database_url)

app = FastAPI(title=f"GsAlgoVIP Webhook [{settings.runtime_label}]")
app.include_router(
    build_router(store, settings.webhook_secret),
    prefix=settings.webhook_path.removesuffix("/webhook/tradingview"),
)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {
        "status": "ok",
        "bot": "gsalgovip",
        "tenant_id": settings.tenant_id or "",
        "instance_id": settings.instance_id or "",
        "dry_run": str(bool(settings.dry_run)).lower(),
        "trading_enabled": str(bool(settings.trading_enabled)).lower(),
    }
