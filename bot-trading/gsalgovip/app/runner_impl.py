"""GsAlgoVIP platform runner stub.

Safe entrypoint for the Spider AI MT5 SaaS catalog. This wrapper:
- does NOT trade
- does NOT call MetaTrader5
- does NOT send orders
- does NOT modify SL/TP
- does NOT touch the network

The platform calls ``run(ctx)`` where ``ctx`` provides:
- ``ctx.config``: dict matching ``config/schema.json`` (already validated)
- ``ctx.stop_event``: ``threading.Event``-like with ``is_set()`` / ``wait(timeout)``
- ``ctx.logger``: structured logger; falls back to stdlib logging
- ``ctx.runtime`` (optional): platform-injected runtime context. Secrets such as
  ``WEBHOOK_SECRET`` and ``MT5_PASSWORD`` MUST come from here, never from disk.

Real runtime code lives in ``app.main`` (FastAPI webhook) and ``app.run_worker``
(MT5 worker). On Windows runners the platform spawns those directly through its
own slot/worker lifecycle; this stub is what the Linux catalog imports for
validation, dry-run wiring tests, and health probes.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Mapping

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PACKAGE_ROOT / "config" / "default.json"
MANIFEST_PATH = PACKAGE_ROOT / "bot_manifest.json"


def _fallback_logger() -> logging.Logger:
    logger = logging.getLogger("bot.gsalgovip.runner")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


def _load_default_config() -> dict[str, Any]:
    if not DEFAULT_CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_manifest_summary() -> dict[str, Any]:
    if not MANIFEST_PATH.exists():
        return {}
    try:
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    keep = ("bot_id", "bot_code", "bot_name", "version", "profile_class")
    return {k: data.get(k) for k in keep if k in data}


def _resolve_config(ctx: Any) -> Mapping[str, Any]:
    cfg = getattr(ctx, "config", None)
    if isinstance(cfg, Mapping):
        return cfg
    if isinstance(ctx, Mapping) and isinstance(ctx.get("config"), Mapping):
        return ctx["config"]
    return _load_default_config()


def _resolve_logger(ctx: Any) -> logging.Logger:
    candidate = getattr(ctx, "logger", None)
    if isinstance(candidate, logging.Logger):
        return candidate
    if isinstance(ctx, Mapping) and isinstance(ctx.get("logger"), logging.Logger):
        return ctx["logger"]
    return _fallback_logger()


def _resolve_stop_event(ctx: Any) -> Any:
    stop = getattr(ctx, "stop_event", None)
    if stop is None and isinstance(ctx, Mapping):
        stop = ctx.get("stop_event")
    return stop


def run(ctx: Any = None) -> int:
    """Platform entrypoint. Returns 0 on graceful shutdown."""
    logger = _resolve_logger(ctx)
    config = _resolve_config(ctx)
    stop_event = _resolve_stop_event(ctx)
    manifest = _load_manifest_summary()

    trading = dict(config.get("trading", {})) if isinstance(config, Mapping) else {}
    lot_size = config.get("lot_size") if isinstance(config, Mapping) else None
    sl_default = config.get("stop_loss") if isinstance(config, Mapping) else None
    tp_default = config.get("take_profit") if isinstance(config, Mapping) else None
    symbol = config.get("symbol") if isinstance(config, Mapping) else None
    timeframe = config.get("timeframe") if isinstance(config, Mapping) else None
    risk_mode = config.get("risk_mode") if isinstance(config, Mapping) else None
    dca_enabled = config.get("dca_enabled") if isinstance(config, Mapping) else None

    logger.info(
        "gsalgovip_runner_startup bot_id=%s version=%s profile=%s "
        "trading_enabled=%s dry_run=%s lot=%s sl=%s tp=%s symbol=%s tf=%s "
        "risk_mode=%s dca=%s",
        manifest.get("bot_id", "gsalgovip"),
        manifest.get("version", "unknown"),
        manifest.get("profile_class", "normal"),
        bool(trading.get("trading_enabled", False)),
        bool(trading.get("dry_run", True)),
        lot_size,
        sl_default,
        tp_default,
        symbol,
        timeframe,
        risk_mode,
        dca_enabled,
    )

    if stop_event is None:
        logger.info("gsalgovip_runner_no_stop_event_provided returning_immediately")
        return 0

    poll = 1.0
    if isinstance(trading.get("poll_interval_sec"), (int, float)):
        poll = max(0.2, float(trading["poll_interval_sec"]))

    while True:
        try:
            if hasattr(stop_event, "wait"):
                if stop_event.wait(timeout=poll):
                    break
            elif hasattr(stop_event, "is_set"):
                if stop_event.is_set():
                    break
                time.sleep(poll)
            else:
                break
        except KeyboardInterrupt:
            break

    logger.info("gsalgovip_runner_shutdown")
    return 0


__all__ = ["run"]
