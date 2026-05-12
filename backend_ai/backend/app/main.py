from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
import os
import socket
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Callable, Any
from zoneinfo import ZoneInfo

# Đảm bảo project root trong path để import shared ops helpers
_project_root = Path(__file__).resolve().parents[3]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from .api.v2.public import router as public_v2_router
from .api.v2.wallet import router as wallet_v2_router
from .api.v2.rewards import router as rewards_v2_router
from .api.v2.accounts import router as accounts_v2_router
from .api.v2.bots import router as bots_v2_router
from .api.v2.deployments import router as deployments_v2_router
from .api.v2.runners import router as runners_v2_router
from .api.v2.miniapp import router as miniapp_v2_router, mini_router as mini_v2_router
from .api.v2.system import router as system_v2_router
from .api.v2.client_events import router as client_events_v2_router
from .api.v2.me import router as me_v2_router
from .api.v2.public_status import router as public_status_v2_router
from .api.v2.streams import router as streams_v2_router
from .api.v2.admin import router as admin_v2_router
from .api.v2.tradingview_webhook import router as tradingview_webhook_v2_router
from .api.v2.error_catalog import (
    ControlPlaneHTTPException,
    control_plane_http_exception_handler,
)
from app.core.rate_limit import RateLimiter, resolve_identity, resolve_limit, resolve_policy

# AI
from .ai.routes_ai import router as ai_router
from .ai.care_campaign_service import start_ai_care_campaign, stop_ai_care_campaign
from .ai.continuous_learning import start_ai_continuous_learning, stop_ai_continuous_learning
from .ai.deferred_queue import start_deferred_ai_queue, stop_deferred_ai_queue

# Core / services
from .settings import settings
from .services.control_plane_service import reset_control_plane_service
from .services.store_service import close_process_store, get_process_store
from .services.watchdog import system_watchdog_loop, _refresh_system_maintenance_state
from .repositories.control_plane_repository import ControlPlaneRepository
from .monitoring.control_plane_metrics import ControlPlaneMetricsService
from .monitoring.control_plane_reconciler import ControlPlaneReconcilerService
from .risk.circuit_breaker_scheduler import CircuitBreakerSchedulerService
from .events.command_delivery_reconciler import CommandDeliveryReconcilerService
from .events.runner_event_consumer import RunnerEventConsumerService
from app.core.log_filters import install_control_plane_access_log_filter
from app.core.redis_client import close_redis, get_redis_write
from app.core.error_log import log_agent_failure
from app.core.log_context import get_request_id
from app.core.log_hygiene import cleanup_debug_trace_file, log_periodic, noisy_log_cooldown_sec
from app.core.request_logging import RequestContextMiddleware
from app.logging_config import configure_service_logging

# Optional clean shutdown hooks - fallback no-op nếu file cũ chưa có
try:
    from .ai.routes_ai import close_http_client as close_ai_routes_http_client
except Exception:
    async def close_ai_routes_http_client() -> None:
        return None

try:
    from .services.broker.ctrader_api_client import close_shared_ctrader_broker_http_client
except Exception:
    async def close_shared_ctrader_broker_http_client() -> None:
        return None

try:
    from .ai.providers.gemini import gemini_engine
except Exception:
    gemini_engine = None

try:
    from .ai.providers.ollama import ollama_engine
except Exception:
    ollama_engine = None

try:
    from .ai.status import get_ai_runtime_status_sync, is_ai_available_sync
except Exception:
    def get_ai_runtime_status_sync() -> dict:
        return {"provider": "unknown", "available": False, "configured": False, "model": "", "details": {}}

    def is_ai_available_sync() -> bool:
        return False

from ops_telegram_alerts import configure_telegram_alerts, notify_error_async, schedule_error_alert

configure_telegram_alerts(
    token=settings.TELEGRAM_BOT_TOKEN,
    chat_id=settings.DEV_CHAT_ID,
    service_name="CNTX-BACKEND",
)

configure_service_logging(
    "api",
    subdir="backend",
    level=os.getenv("LOG_LEVEL", "INFO"),
    mirror_logger_names=("uvicorn", "uvicorn.access"),
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
install_control_plane_access_log_filter()
log = logging.getLogger("api_gateway")

APP_VERSION = "0.2.2"
_STARTED_AT = time.time()

# Ensure static font assets from Next export are served with the expected MIME type.
mimetypes.add_type("font/woff2", ".woff2")

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = (BASE_DIR / "static").resolve()
IMG_DIR = (STATIC_DIR / "img").resolve()
FRONTEND_V2_OUT_DIR = (_project_root / "frontend-v2" / "out").resolve()
FRONTEND_V2_NEXT_DIR = (FRONTEND_V2_OUT_DIR / "_next").resolve()


def _startup_singleton_enabled() -> bool:
    return bool(getattr(settings, "STARTUP_SINGLETON_ENABLED", True))


def _startup_singleton_key_prefix() -> str:
    return str(getattr(settings, "STARTUP_SINGLETON_KEY_PREFIX", "spider:backend:startup") or "spider:backend:startup").strip()


def _startup_singleton_lock_ttl_sec() -> int:
    return max(30, int(getattr(settings, "STARTUP_SINGLETON_LOCK_TTL_SEC", 180) or 180))


def _startup_singleton_ready_ttl_sec() -> int:
    return max(_startup_singleton_lock_ttl_sec(), int(getattr(settings, "STARTUP_SINGLETON_READY_TTL_SEC", 300) or 300))


def _startup_singleton_poll_sec() -> float:
    return max(0.1, float(getattr(settings, "STARTUP_SINGLETON_POLL_SEC", 0.5) or 0.5))


def _startup_singleton_revision() -> str:
    watched_files = (
        Path(__file__).resolve(),
        (_project_root / "backend_ai" / "backend" / "init_pg_schema.py").resolve(),
    )
    parts = [APP_VERSION]
    for path in watched_files:
        try:
            stat = path.stat()
            parts.append(f"{path}:{stat.st_mtime_ns}:{stat.st_size}")
        except FileNotFoundError:
            parts.append(f"{path}:missing")
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _startup_singleton_lock_key() -> str:
    return f"{_startup_singleton_key_prefix()}:{_startup_singleton_revision()}:lock"


def _startup_singleton_ready_key() -> str:
    return f"{_startup_singleton_key_prefix()}:{_startup_singleton_revision()}:ready"


async def _release_key_if_token_matches(*, key: str, token: str) -> None:
    redis = await get_redis_write(decode_responses=True)
    if redis is None:
        return
    try:
        current = await redis.get(key)
        if current == token:
            await redis.delete(key)
    except Exception as exc:
        log.warning("redis token release failed key=%s: %s", key, exc)


async def _run_startup_once_per_revision(fn: Callable[[], Any], *, token: str) -> None:
    if not _startup_singleton_enabled():
        await _call_async_or_sync(fn)
        return

    redis = await get_redis_write(decode_responses=True)
    if redis is None:
        log.warning("startup_singleton disabled at runtime because Redis is unavailable; running startup tasks locally")
        await _call_async_or_sync(fn)
        return

    lock_key = _startup_singleton_lock_key()
    ready_key = _startup_singleton_ready_key()
    revision = _startup_singleton_revision()
    lock_ttl = _startup_singleton_lock_ttl_sec()
    ready_ttl = _startup_singleton_ready_ttl_sec()
    poll_sec = _startup_singleton_poll_sec()
    wait_logged = False

    while True:
        try:
            ready_value = await redis.get(ready_key)
            if ready_value:
                if wait_logged:
                    log.info("startup_singleton ready observed revision=%s; this worker skips bootstrap/repair", revision)
                return

            acquired = await redis.set(lock_key, token, nx=True, ex=lock_ttl)
            if acquired:
                log.info(
                    "startup_singleton lock acquired revision=%s lock_ttl=%ss ready_ttl=%ss",
                    revision,
                    lock_ttl,
                    ready_ttl,
                )
                try:
                    await _call_async_or_sync(fn)
                    await redis.set(ready_key, token, ex=ready_ttl)
                    log.info("startup_singleton marked ready revision=%s ttl=%ss", revision, ready_ttl)
                finally:
                    await _release_key_if_token_matches(key=lock_key, token=token)
                return

            if not wait_logged:
                wait_logged = True
                log.info(
                    "startup_singleton waiting for owner revision=%s lock_key=%s ready_key=%s",
                    revision,
                    lock_key,
                    ready_key,
                )
        except Exception as exc:
            log.warning(
                "startup_singleton coordination failed revision=%s: %s; running startup tasks locally",
                revision,
                exc,
            )
            await _call_async_or_sync(fn)
            return

        await asyncio.sleep(poll_sec)


def _background_singleton_enabled() -> bool:
    return bool(getattr(settings, "BACKGROUND_SINGLETON_ENABLED", True))


def _background_singleton_key() -> str:
    return str(
        getattr(settings, "BACKGROUND_SINGLETON_KEY", "spider:backend:background-owner")
        or "spider:backend:background-owner"
    ).strip()


def _background_singleton_ttl_sec() -> int:
    return max(30, int(getattr(settings, "BACKGROUND_SINGLETON_TTL_SEC", 90) or 90))


def _background_singleton_renew_sec() -> int:
    ttl_sec = _background_singleton_ttl_sec()
    renew_sec = int(getattr(settings, "BACKGROUND_SINGLETON_RENEW_SEC", 30) or 30)
    return max(5, min(ttl_sec - 5, renew_sec))


def _background_singleton_token() -> str:
    host = socket.gethostname().strip() or "backend"
    return f"{host}:{os.getpid()}:{int(_STARTED_AT)}"


async def _background_singleton_status(token: str) -> str:
    if not _background_singleton_enabled():
        return "disabled"

    redis = await get_redis_write(decode_responses=True)
    if redis is None:
        return "error"

    key = _background_singleton_key()
    ttl_sec = _background_singleton_ttl_sec()
    try:
        current = await redis.get(key)
        if current == token:
            await redis.set(key, token, ex=ttl_sec)
            return "owner"
        if current:
            return "busy"
        acquired = await redis.set(key, token, nx=True, ex=ttl_sec)
        return "owner" if acquired else "busy"
    except Exception as exc:
        log_periodic(
            log,
            logging.WARNING,
            "background_singleton lease check failed key=%s: %s",
            key,
            exc,
            key=f"background_singleton_lease:{key}:{type(exc).__name__}:{str(exc)[:120]}",
            cooldown_sec=noisy_log_cooldown_sec(),
        )
        return "error"


async def _release_background_singleton(token: str) -> None:
    if not _background_singleton_enabled():
        return

    await _release_key_if_token_matches(key=_background_singleton_key(), token=token)


def _run_schema_migration_or_fail() -> None:
    """
    Ensure Postgres schema is ready before serving any request.
    """
    try:
        from init_pg_schema import init_postgres_schema
    except Exception as exc:
        raise RuntimeError(
            f"[CRITICAL ERROR] Cannot import migration bootstrap. Details: {exc}"
        ) from exc

    try:
        init_postgres_schema()
    except Exception as exc:
        raise RuntimeError(
            f"[CRITICAL ERROR] Database migration failed. Backend will not start. Details: {exc}"
        ) from exc


async def _safe_cancel_task(task: Optional[asyncio.Task], name: str) -> None:
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        log.warning("Task '%s' shutdown with error: %s", name, exc)


async def _safe_call_async(name: str, fn: Optional[Callable[..., Any]]) -> None:
    if fn is None:
        return
    try:
        await _call_async_or_sync(fn)
    except Exception as exc:
        log.warning("Optional shutdown hook '%s' failed: %s", name, exc)


async def _call_async_or_sync(fn: Callable[..., Any]) -> Any:
    result = fn()
    if asyncio.iscoroutine(result):
        return await result
    return result


def _sync_ai_available() -> bool:
    return is_ai_available_sync()


def _install_observability_defaults(app: FastAPI) -> None:
    app.state.is_service_online = False
    app.state.service_health_status = {}
    app.state.startup_state = "created"
    app.state.startup_started_at = int(_STARTED_AT)
    app.state.startup_completed_at = 0
    app.state.startup_error = None
    app.state.background_singleton_enabled = _background_singleton_enabled()
    app.state.background_singleton_state = "created"
    app.state.background_singleton_updated_at = int(_STARTED_AT)
    app.state.control_plane_metrics = None
    app.state.control_plane_reconciler = None


def _set_startup_state(app: FastAPI, state: str, *, error: str | None = None) -> None:
    now = int(time.time())
    app.state.startup_state = str(state or "created")
    app.state.startup_started_at = int(getattr(app.state, "startup_started_at", int(_STARTED_AT)) or int(_STARTED_AT))
    if app.state.startup_state == "ready" and not int(getattr(app.state, "startup_completed_at", 0) or 0):
        app.state.startup_completed_at = now
    if error is not None:
        app.state.startup_error = error
    elif app.state.startup_state != "failed":
        app.state.startup_error = None


def _set_background_singleton_state(app: FastAPI, state: str) -> None:
    app.state.background_singleton_enabled = _background_singleton_enabled()
    app.state.background_singleton_state = str(state or "created")
    app.state.background_singleton_updated_at = int(time.time())


def _refresh_health_state(app: FastAPI) -> None:
    current = dict(getattr(app.state, "service_health_status", {}) or {})
    snapshot_updated_at = int(current.get("updated_at") or 0)
    current["service_online"] = bool(getattr(app.state, "is_service_online", False))
    current["ai_available"] = _sync_ai_available()
    current["ai_runtime"] = get_ai_runtime_status_sync()
    current["startup_state"] = str(getattr(app.state, "startup_state", "created") or "created")
    current["startup_completed_at"] = int(getattr(app.state, "startup_completed_at", 0) or 0)
    current["startup_error"] = getattr(app.state, "startup_error", None)
    current["background_singleton_enabled"] = bool(getattr(app.state, "background_singleton_enabled", False))
    current["background_singleton_state"] = str(getattr(app.state, "background_singleton_state", "created") or "created")
    current["background_singleton_updated_at"] = int(getattr(app.state, "background_singleton_updated_at", 0) or 0)
    current["updated_at"] = snapshot_updated_at or int(time.time())
    current["observability_updated_at"] = int(time.time())
    app.state.service_health_status = current


def _get_control_plane_metrics_service(app: FastAPI) -> ControlPlaneMetricsService | None:
    metrics_service = getattr(app.state, "control_plane_metrics", None)
    if metrics_service is not None:
        return metrics_service

    try:
        metrics_service = ControlPlaneMetricsService()
    except Exception as exc:
        log.warning("Control-plane metrics service unavailable: %s", exc)
        return None

    app.state.control_plane_metrics = metrics_service
    return metrics_service


def _build_observability_snapshot(app: FastAPI, *, force_refresh: bool = False) -> dict[str, Any]:
    metrics_service = _get_control_plane_metrics_service(app)
    if metrics_service is None:
        service_health = dict(getattr(app.state, "service_health_status", {}) or {})
        return {
            "status": "error",
            "live": True,
            "ready": False,
            "critical_failures": ["metrics_service_unavailable"],
            "warning_failures": [],
            "process": {
                "version": APP_VERSION,
                "env": settings.APP_ENV,
                "db_mode": settings.DB_MODE,
                "started_at": int(_STARTED_AT),
                "uptime_sec": max(0, int(time.time() - _STARTED_AT)),
            },
            "startup": {
                "state": str(getattr(app.state, "startup_state", "created") or "created"),
                "started_at": int(getattr(app.state, "startup_started_at", int(_STARTED_AT)) or int(_STARTED_AT)),
                "completed_at": int(getattr(app.state, "startup_completed_at", 0) or 0),
                "duration_sec": None,
                "error": getattr(app.state, "startup_error", None),
            },
            "background_singleton": {
                "enabled": bool(getattr(app.state, "background_singleton_enabled", False)),
                "state": str(getattr(app.state, "background_singleton_state", "created") or "created"),
                "updated_at": int(getattr(app.state, "background_singleton_updated_at", 0) or 0),
                "age_sec": None,
            },
            "service_health_status": service_health,
            "runtime": {
                "healthy": False,
                "degraded_reasons": ["runtime_summary_unavailable"],
                "summary": {},
                "observation": {
                    "ok": False,
                    "source": "error",
                    "collected_at": 0,
                    "age_sec": None,
                    "error": "metrics_service_unavailable",
                },
            },
            "checks": {
                "metrics_service_available": {
                    "ok": False,
                    "critical": True,
                    "detail": "control_plane_metrics_service_not_initialized",
                }
            },
            "reconciler": {},
            "status_code": 503,
        }

    return metrics_service.build_observability_snapshot(
        app_state=app.state,
        started_at=_STARTED_AT,
        version=APP_VERSION,
        force_refresh=force_refresh,
    )


async def _audit_logs_cleanup_at_3am() -> None:
    """
    Zero-lock cleanup audit_logs và debug trace lúc 3h sáng giờ VN (Asia/Ho_Chi_Minh).
    """
    tz_vn = ZoneInfo("Asia/Ho_Chi_Minh")
    store = get_process_store()
    while True:
        now = datetime.now(tz_vn)
        next_3am = now.replace(hour=3, minute=0, second=0, microsecond=0)
        if next_3am <= now:
            next_3am += timedelta(days=1)
        wait_sec = (next_3am - now).total_seconds()

        try:
            await asyncio.sleep(wait_sec)
        except asyncio.CancelledError:
            raise

        try:
            cutoff_ts = int(time.time()) - 30 * 24 * 3600
            total = 0
            while True:
                deleted = await asyncio.to_thread(
                    store.delete_audit_logs_batch_older_than,
                    cutoff_ts,
                    1000,
                )
                if deleted == 0:
                    break
                total += deleted
                await asyncio.sleep(0.1)

            debug_result = await asyncio.to_thread(cleanup_debug_trace_file)
            if total > 0 or debug_result.get("deleted") or debug_result.get("truncated"):
                log.info(
                    "Nightly log cleanup (3am Asia/Ho_Chi_Minh): audit_deleted=%d debug_deleted=%s debug_truncated=%s debug_path=%s",
                    total,
                    bool(debug_result.get("deleted")),
                    bool(debug_result.get("truncated")),
                    str(debug_result.get("path") or "-"),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Audit logs cleanup failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting CNTx labs Backend v%s in '%s' mode", APP_VERSION, settings.APP_ENV)

    watchdog_task: Optional[asyncio.Task] = None
    background_singleton_task: Optional[asyncio.Task] = None
    cleanup_task: Optional[asyncio.Task] = None
    control_plane_reconciler_task: Optional[asyncio.Task] = None
    control_plane_reconciler_stop: Optional[asyncio.Event] = None
    runner_event_consumer_task: Optional[asyncio.Task] = None
    runner_event_consumer_stop: Optional[asyncio.Event] = None
    command_delivery_reconciler_task: Optional[asyncio.Task] = None
    command_delivery_reconciler_stop: Optional[asyncio.Event] = None
    circuit_breaker_scheduler_task: Optional[asyncio.Task] = None
    circuit_breaker_scheduler_stop: Optional[asyncio.Event] = None
    ai_care_started = False
    ai_continuous_learning_started = False
    deferred_ai_queue_started = False
    background_singleton_owner = False
    background_singleton_enabled = _background_singleton_enabled()
    background_singleton_token = _background_singleton_token()
    startup_singleton_token = f"startup:{background_singleton_token}"
    control_plane_reconciler_service: Optional[ControlPlaneReconcilerService] = None
    circuit_breaker_scheduler_service: Optional[CircuitBreakerSchedulerService] = None
    command_delivery_reconciler_service: Optional[CommandDeliveryReconcilerService] = None

    _set_startup_state(app, "booting")
    _set_background_singleton_state(app, "starting" if background_singleton_enabled else "disabled")
    _refresh_health_state(app)

    async def _start_singleton_background_jobs() -> None:
        nonlocal cleanup_task
        nonlocal control_plane_reconciler_task
        nonlocal control_plane_reconciler_stop
        nonlocal runner_event_consumer_task
        nonlocal runner_event_consumer_stop
        nonlocal command_delivery_reconciler_task
        nonlocal command_delivery_reconciler_stop
        nonlocal circuit_breaker_scheduler_task
        nonlocal circuit_breaker_scheduler_stop
        nonlocal ai_care_started
        nonlocal ai_continuous_learning_started
        nonlocal control_plane_reconciler_service
        nonlocal circuit_breaker_scheduler_service
        nonlocal command_delivery_reconciler_service

        if control_plane_reconciler_task is None or control_plane_reconciler_task.done():
            if control_plane_reconciler_service is None:
                control_plane_reconciler_service = ControlPlaneReconcilerService()
                app.state.control_plane_reconciler = control_plane_reconciler_service
            control_plane_reconciler_stop = asyncio.Event()
            control_plane_reconciler_task = asyncio.create_task(
                control_plane_reconciler_service.run_forever(control_plane_reconciler_stop),
                name="control_plane_reconciler",
            )

        if bool(getattr(settings, "CIRCUIT_BREAKER_SCHEDULER_ENABLED", True)) and (
            circuit_breaker_scheduler_task is None or circuit_breaker_scheduler_task.done()
        ):
            if circuit_breaker_scheduler_service is None:
                circuit_breaker_scheduler_service = CircuitBreakerSchedulerService()
                app.state.circuit_breaker_scheduler = circuit_breaker_scheduler_service
            circuit_breaker_scheduler_stop = asyncio.Event()
            circuit_breaker_scheduler_task = asyncio.create_task(
                circuit_breaker_scheduler_service.run_forever(circuit_breaker_scheduler_stop),
                name="circuit_breaker_scheduler",
            )

        if bool(getattr(settings, "COMMAND_DELIVERY_REPLAY_ENABLED", True)) and (
            command_delivery_reconciler_task is None or command_delivery_reconciler_task.done()
        ):
            if command_delivery_reconciler_service is None:
                command_delivery_reconciler_service = CommandDeliveryReconcilerService()
                app.state.command_delivery_reconciler = command_delivery_reconciler_service
            command_delivery_reconciler_stop = asyncio.Event()
            command_delivery_reconciler_task = asyncio.create_task(
                command_delivery_reconciler_service.run_forever(command_delivery_reconciler_stop),
                name="command_delivery_reconciler",
            )

        if bool(getattr(settings, "CONTROL_PLANE_EVENT_CONSUMER_ENABLED", True)) and (
            runner_event_consumer_task is None or runner_event_consumer_task.done()
        ):
            runner_event_consumer_stop = asyncio.Event()
            runner_event_consumer_task = asyncio.create_task(
                RunnerEventConsumerService(
                    group_name=str(getattr(settings, "CONTROL_PLANE_EVENT_CONSUMER_GROUP", "control-plane-event-audit") or "control-plane-event-audit"),
                    block_ms=int(getattr(settings, "CONTROL_PLANE_EVENT_CONSUMER_BLOCK_MS", 5000) or 5000),
                ).run_forever(runner_event_consumer_stop),
                name="runner_event_consumer",
            )

        if not ai_care_started:
            try:
                await start_ai_care_campaign()
                ai_care_started = True
            except Exception as exc:
                log.warning("AI care campaign failed to start (non-fatal): %s", exc)

        if not ai_continuous_learning_started:
            try:
                ai_continuous_learning_started = bool(await start_ai_continuous_learning())
            except Exception as exc:
                log.warning("AI continuous learning failed to start (non-fatal): %s", exc)

        if cleanup_task is None or cleanup_task.done():
            cleanup_task = asyncio.create_task(
                _audit_logs_cleanup_at_3am(),
                name="audit_logs_cleanup_at_3am",
            )

    async def _stop_singleton_background_jobs() -> None:
        nonlocal cleanup_task
        nonlocal control_plane_reconciler_task
        nonlocal control_plane_reconciler_stop
        nonlocal runner_event_consumer_task
        nonlocal runner_event_consumer_stop
        nonlocal command_delivery_reconciler_task
        nonlocal command_delivery_reconciler_stop
        nonlocal circuit_breaker_scheduler_task
        nonlocal circuit_breaker_scheduler_stop
        nonlocal ai_care_started
        nonlocal ai_continuous_learning_started

        await _safe_cancel_task(cleanup_task, "audit_logs_cleanup_at_3am")
        cleanup_task = None

        if control_plane_reconciler_stop is not None:
            control_plane_reconciler_stop.set()
        await _safe_cancel_task(control_plane_reconciler_task, "control_plane_reconciler")
        control_plane_reconciler_task = None
        control_plane_reconciler_stop = None

        if circuit_breaker_scheduler_stop is not None:
            circuit_breaker_scheduler_stop.set()
        await _safe_cancel_task(circuit_breaker_scheduler_task, "circuit_breaker_scheduler")
        circuit_breaker_scheduler_task = None
        circuit_breaker_scheduler_stop = None

        if command_delivery_reconciler_stop is not None:
            command_delivery_reconciler_stop.set()
        await _safe_cancel_task(command_delivery_reconciler_task, "command_delivery_reconciler")
        command_delivery_reconciler_task = None
        command_delivery_reconciler_stop = None

        if runner_event_consumer_stop is not None:
            runner_event_consumer_stop.set()
        await _safe_cancel_task(runner_event_consumer_task, "runner_event_consumer")
        runner_event_consumer_task = None
        runner_event_consumer_stop = None

        if ai_care_started:
            try:
                await stop_ai_care_campaign()
            except Exception as exc:
                log.warning("stop_ai_care_campaign failed: %s", exc)
            ai_care_started = False

        if ai_continuous_learning_started:
            try:
                await stop_ai_continuous_learning()
            except Exception as exc:
                log.warning("stop_ai_continuous_learning failed: %s", exc)
            ai_continuous_learning_started = False

    async def _background_singleton_supervisor() -> None:
        nonlocal background_singleton_owner

        renew_sec = _background_singleton_renew_sec()
        last_wait_log_ts = 0.0
        while True:
            try:
                state = await _background_singleton_status(background_singleton_token)
                if state == "owner":
                    _set_background_singleton_state(app, "owner")
                    if not background_singleton_owner:
                        background_singleton_owner = True
                        log.info(
                            "background_singleton ownership acquired key=%s",
                            _background_singleton_key(),
                        )
                        await _start_singleton_background_jobs()
                elif state == "busy":
                    _set_background_singleton_state(app, "busy")
                    if background_singleton_owner:
                        background_singleton_owner = False
                        log.warning(
                            "background_singleton ownership lost key=%s",
                            _background_singleton_key(),
                        )
                        await _stop_singleton_background_jobs()
                else:
                    _set_background_singleton_state(app, "error")
                    now = time.time()
                    if not background_singleton_owner and (now - last_wait_log_ts) >= 60.0:
                        last_wait_log_ts = now
                        log.debug(
                            "background_singleton waiting for Redis lease key=%s; background jobs stay idle in this worker",
                            _background_singleton_key(),
                        )
                _refresh_health_state(app)

                await asyncio.sleep(renew_sec)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _set_background_singleton_state(app, "error")
                _refresh_health_state(app)
                log.warning("background_singleton supervisor error: %s", exc)
                await asyncio.sleep(renew_sec)

    # --- STARTUP ---
    try:
        async def _run_serialized_startup_tasks() -> None:
            log.info("Running database migration bootstrap...")
            await asyncio.to_thread(_run_schema_migration_or_fail)
            log.info("Database migration bootstrap completed.")

        await _run_startup_once_per_revision(
            _run_serialized_startup_tasks,
            token=startup_singleton_token,
        )

        # 3) Init store/database
        log.info("Initializing database (Mode: %s)...", settings.DB_MODE.upper())
        store = get_process_store()
        app.state.store = store
        store.init()
        app.state.control_plane_metrics = ControlPlaneMetricsService(ControlPlaneRepository(store))
        log.info("Database initialized successfully!")

        # 4) Health state
        app.state.is_service_online = False
        _refresh_health_state(app)
        try:
            await asyncio.to_thread(_refresh_system_maintenance_state, store, app_state=app.state, timeout_sec=60)
        except Exception as exc:
            log.warning("Initial service health refresh skipped: %s", exc)

        try:
            await start_deferred_ai_queue()
            deferred_ai_queue_started = True
        except Exception as exc:
            log.warning("deferred_ai_queue failed to start (non-fatal): %s", exc)

        # 5) Watchdog
        watchdog_task = asyncio.create_task(
            system_watchdog_loop(
                interval_sec=30,
                timeout_sec=90,
                app_state=app.state,
                cleanup_enabled=lambda: (not background_singleton_enabled) or background_singleton_owner,
            ),
            name="system_watchdog_loop",
        )

        if background_singleton_enabled:
            log.info(
                "Background singleton enabled key=%s ttl=%ss renew=%ss",
                _background_singleton_key(),
                _background_singleton_ttl_sec(),
                _background_singleton_renew_sec(),
            )
            background_singleton_task = asyncio.create_task(
                _background_singleton_supervisor(),
                name="background_singleton_supervisor",
            )
        else:
            log.info(
                "Background singleton disabled; this worker will start background jobs locally.",
            )
            _set_background_singleton_state(app, "disabled")
            await _start_singleton_background_jobs()

        _set_startup_state(app, "ready")
        _refresh_health_state(app)
        yield
    except Exception as exc:
        _set_startup_state(app, "failed", error=str(exc))
        _refresh_health_state(app)
        await notify_error_async(
            area="Backend khởi động",
            summary="Backend không khởi động hoàn tất.",
            exc=exc,
            impact="Mini App, runner hoặc API có thể không hoạt động.",
            action="Kiểm tra PM2, Postgres, Redis và log backend.",
            alert_key=f"backend_startup_failed:{type(exc).__name__}",
            cooldown_sec=120,
        )
        raise

    finally:
        # --- SHUTDOWN ---
        log.info("Shutting down CNTx labs Backend")
        _set_startup_state(app, "shutting_down")
        _set_background_singleton_state(app, "stopping")
        _refresh_health_state(app)

        await _safe_cancel_task(background_singleton_task, "background_singleton_supervisor")
        if background_singleton_enabled:
            if background_singleton_owner:
                await _stop_singleton_background_jobs()
                background_singleton_owner = False
            await _release_background_singleton(background_singleton_token)
            _set_background_singleton_state(app, "released")
        else:
            await _stop_singleton_background_jobs()
            _set_background_singleton_state(app, "disabled")

        if deferred_ai_queue_started:
            try:
                await stop_deferred_ai_queue()
            except Exception as exc:
                log.warning("stop_deferred_ai_queue failed: %s", exc)
            deferred_ai_queue_started = False

        await _safe_cancel_task(watchdog_task, "system_watchdog_loop")

        try:
            await close_redis()
        except Exception as exc:
            log.warning("close_redis failed: %s", exc)

        # Đóng shared clients để tránh warning / leak connection
        await _safe_call_async("close_ai_routes_http_client", close_ai_routes_http_client)
        await _safe_call_async("close_shared_ctrader_broker_http_client", close_shared_ctrader_broker_http_client)
        await _safe_call_async(
            "gemini_engine.close",
            getattr(gemini_engine, "close", None) if gemini_engine is not None else None,
        )
        await _safe_call_async(
            "ollama_engine.close",
            getattr(ollama_engine, "close", None) if ollama_engine is not None else None,
        )
        try:
            reset_control_plane_service()
            close_process_store()
            app.state.store = None
        except Exception as exc:
            log.warning("close_process_store failed: %s", exc)
        _refresh_health_state(app)


# Khởi tạo App với lifespan
app = FastAPI(
    title="Trading Automation SaaS Backend",
    version=APP_VERSION,
    lifespan=lifespan,
)
_install_observability_defaults(app)
app.add_exception_handler(ControlPlaneHTTPException, control_plane_http_exception_handler)

# Static Files
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
else:
    log.warning("Static directory not found: %s", STATIC_DIR)

if FRONTEND_V2_NEXT_DIR.exists():
    app.mount("/_next", StaticFiles(directory=str(FRONTEND_V2_NEXT_DIR)), name="frontend_v2_next")
else:
    log.warning("Frontend-v2 _next directory not found: %s", FRONTEND_V2_NEXT_DIR)


# CORS
def _cors_origins_from_settings() -> list[str]:
    value = getattr(settings, "CORS_ORIGINS", None)
    if isinstance(value, list):
        origins = [str(origin).strip() for origin in value if str(origin).strip()]
    elif isinstance(value, str):
        origins = [origin.strip() for origin in value.split(",") if origin.strip()]
    else:
        origins = []

    if not origins and getattr(settings, "APP_ENV", "") == "dev":
        origins = ["http://localhost:3000", "http://127.0.0.1:3000"]
    return origins


cors_origins = _cors_origins_from_settings()

if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# Rate limiter (Redis-based, fail-open). Khoi tao 1 lan cho process.
_rate_limiter = RateLimiter()
app.state.rate_limiter = _rate_limiter


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Rate limit theo per-(user|ip) per-endpoint per-minute. Fail-open neu Redis loi."""
    if not bool(getattr(settings, "RATE_LIMIT_ENABLED", True)):
        return await call_next(request)
    method = request.method
    path = request.url.path or ""
    policy = resolve_policy(method, path)
    if policy is None:
        return await call_next(request)
    overrides = getattr(settings, "RATE_LIMIT_PER_MIN_OVERRIDES", None)
    if not isinstance(overrides, dict):
        overrides = None
    limit = resolve_limit(policy, per_min_overrides=overrides)
    identity = resolve_identity(request)
    endpoint = f"{method.upper()}:{policy}"
    try:
        result = await _rate_limiter.check(
            identity=identity,
            endpoint=endpoint,
            limit=limit,
            window_sec=60,
        )
    except Exception:
        # Final fail-open guard
        return await call_next(request)
    if not result.get("allowed"):
        body = {
            "detail": "rate_limited",
            "error_info": {
                "error": "rate_limited",
                "public_code": "rate_limited",
                "message_vi": "Bạn thao tác quá nhanh. Vui lòng đợi vài giây rồi thử lại.",
                "message_en": "Too many requests. Please wait a few seconds and try again.",
                "action": "retry",
                "retryable": True,
                "group": "system",
                "limit": result.get("limit"),
                "remaining": 0,
                "reset_in": result.get("reset_in"),
            },
        }
        headers = {
            "Retry-After": str(result.get("reset_in") or 1),
            "X-RateLimit-Limit": str(result.get("limit") or 0),
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": str(result.get("reset_in") or 1),
        }
        return JSONResponse(status_code=429, content=body, headers=headers)
    response = await call_next(request)
    try:
        response.headers["X-RateLimit-Limit"] = str(result.get("limit") or 0)
        response.headers["X-RateLimit-Remaining"] = str(result.get("remaining") or 0)
        response.headers["X-RateLimit-Reset"] = str(result.get("reset_in") or 0)
    except Exception:
        pass
    return response


# Middleware bắt lỗi
@app.middleware("http")
async def catch_exceptions_middleware(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as exc:
        request_id = get_request_id() or ""
        schedule_error_alert(
            area="Backend API",
            summary="API backend gặp lỗi khi xử lý yêu cầu.",
            exc=exc,
            path=f"{request.method} {request.url.path}",
            impact="Người dùng có thể thấy thao tác thất bại trong Mini App.",
            action=f"Grep request_id={request_id or '-'} trong logs/backend/api.jsonl để xem stack + context đầy đủ.",
            alert_key=f"backend_api_unhandled:{request.method}:{request.url.path}:{type(exc).__name__}",
            cooldown_sec=180,
        )

        log_agent_failure(
            log,
            "request.unhandled_exception",
            error=exc,
            error_code="backend_unhandled_exception",
            operation="http_request",
            hint=(
                f"A request raised past all handlers. Grep `request_id={request_id or '-'}` in "
                "`logs/backend/api.jsonl` to see the full stack + any structured events emitted "
                "before the crash. Telegram alert dispatched with the same context."
            ),
            http_method=request.method,
            http_path=request.url.path,
        )

        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": "Hệ thống đang xử lý sự cố. Đội ngũ kỹ thuật đã được thông báo.",
                "error_type": type(exc).__name__,
                "request_id": request_id or None,
            },
            headers={"X-Request-ID": request_id} if request_id else None,
        )


@app.middleware("http")
async def reject_embedded_null_byte_path(request: Request, call_next):
    """
    ASGI path with \\0 makes os.path.realpath / os.lstat raise ValueError inside
    Starlette StaticFiles — reject before mounts run to avoid false CRASH alerts.
    """
    path = request.scope.get("path") or ""
    if "\x00" in path:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "Đường dẫn không hợp lệ."},
        )
    raw_path = request.scope.get("raw_path")
    if isinstance(raw_path, (bytes, bytearray)) and b"\x00" in raw_path:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "Đường dẫn không hợp lệ."},
        )
    return await call_next(request)


# Request context + access log middleware. Registered last so it ends up at the
# outermost layer and assigns request_id BEFORE any other middleware runs.
if bool(getattr(settings, "REQUEST_LOG_ENABLED", True)):
    app.add_middleware(RequestContextMiddleware)


# Routers
app.include_router(public_v2_router, prefix="/api/v2")
app.include_router(wallet_v2_router, prefix="/api/v2")
app.include_router(rewards_v2_router, prefix="/api/v2")
app.include_router(accounts_v2_router, prefix="/api/v2")
app.include_router(bots_v2_router, prefix="/api/v2")
app.include_router(deployments_v2_router, prefix="/api/v2")
app.include_router(runners_v2_router, prefix="/api/v2")
app.include_router(miniapp_v2_router, prefix="/api/v2")
app.include_router(mini_v2_router, prefix="/api/v2")
app.include_router(system_v2_router, prefix="/api/v2")
app.include_router(client_events_v2_router, prefix="/api/v2")
app.include_router(me_v2_router, prefix="/api/v2")
app.include_router(public_status_v2_router, prefix="/api/v2")
app.include_router(streams_v2_router, prefix="/api/v2")
app.include_router(admin_v2_router, prefix="/api/v2")
app.include_router(tradingview_webhook_v2_router, prefix="/api/v2")

# Giữ prefix /ai cho hubbot gọi /ai/chat
app.include_router(ai_router, prefix="/ai", tags=["AI Integration"])


@app.get("/", include_in_schema=False)
def root():
    page = FRONTEND_V2_OUT_DIR / "index.html"
    if page.exists():
        return FileResponse(
            str(page),
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )
    _log_frontend_export_missing(
        kind="page",
        section="(root)",
        path=page,
        file_exists=False,
    )
    return _frontend_maintenance_html_response()


@app.get("/index.txt", include_in_schema=False)
def frontend_v2_root_rsc():
    return _frontend_rsc_response()


_FRONTEND_MAINTENANCE_HTML = """\
<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>CNTx labs &middot; Mini App đang cập nhật</title>
<style>
  html,body{margin:0;padding:0;background:#05070b;color:#e6f1ff;
    font-family:system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;}
  body{min-height:100vh;display:flex;align-items:center;justify-content:center;
    padding:24px;text-align:center;}
  .card{max-width:420px;}
  h1{margin:0 0 8px;font-size:22px;font-weight:600;letter-spacing:.2px;}
  p{margin:6px 0;color:#7d8aa3;font-size:14px;line-height:1.5;}
  .code{display:inline-block;margin-top:14px;padding:5px 10px;border-radius:8px;
    border:1px solid rgba(255,255,255,.12);color:#9aa6bd;font-size:12px;
    font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;}
</style>
</head>
<body>
  <div class="card">
    <h1>Mini App đang cập nhật</h1>
    <p>Vui lòng thử lại sau ít phút.</p>
    <span class="code">503 &middot; cntx-labs &middot; frontend_export_missing</span>
  </div>
</body>
</html>
"""


_MAINTENANCE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate",
    "Retry-After": "60",
}


def _log_frontend_export_missing(
    *,
    kind: str,
    section: str,
    path: Path,
    file_exists: bool,
) -> None:
    log.error(
        "frontend-v2 export missing: kind=%s section=%s path=%s out_dir=%s out_dir_exists=%s file_exists=%s",
        kind,
        section,
        path,
        FRONTEND_V2_OUT_DIR,
        FRONTEND_V2_OUT_DIR.exists(),
        file_exists,
    )


def _frontend_maintenance_html_response() -> HTMLResponse:
    return HTMLResponse(
        content=_FRONTEND_MAINTENANCE_HTML,
        status_code=503,
        headers=dict(_MAINTENANCE_HEADERS),
    )


def _frontend_maintenance_json_response(detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "ok": False,
            "error": "frontend_export_missing",
            "detail": detail,
            "message_vi": "Mini App đang cập nhật. Vui lòng thử lại sau ít phút.",
        },
        headers=dict(_MAINTENANCE_HEADERS),
    )


def _frontend_maintenance_plain_response(detail: str) -> PlainTextResponse:
    return PlainTextResponse(
        status_code=503,
        content=(
            "Mini App đang cập nhật. Vui lòng thử lại sau ít phút.\n"
            f"detail: {detail}\n"
        ),
        headers=dict(_MAINTENANCE_HEADERS),
    )


def _frontend_page_response(section: str) -> Response:
    page = (FRONTEND_V2_OUT_DIR / section / "index.html").resolve()
    if not page.exists():
        _log_frontend_export_missing(
            kind="page",
            section=section,
            path=page,
            file_exists=False,
        )
        return _frontend_maintenance_html_response()
    return FileResponse(
        str(page),
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


def _frontend_rsc_response(section: str = "") -> Response:
    asset = (FRONTEND_V2_OUT_DIR / section / "index.txt").resolve()
    if not asset.exists():
        _log_frontend_export_missing(
            kind="rsc",
            section=section or "(root)",
            path=asset,
            file_exists=False,
        )
        return _frontend_maintenance_plain_response(
            detail=f"rsc payload missing for section={section or '(root)'}",
        )
    return FileResponse(
        str(asset),
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


def _frontend_out_asset_response(filename: str, media_type: str) -> Response:
    asset = (FRONTEND_V2_OUT_DIR / filename).resolve()
    if not asset.exists():
        _log_frontend_export_missing(
            kind="asset",
            section=filename,
            path=asset,
            file_exists=False,
        )
        if media_type.startswith("text/") or media_type == "application/json":
            return _frontend_maintenance_plain_response(
                detail=f"asset missing: {filename}",
            )
        # Binary asset (e.g. image) cannot return HTML body usefully — return
        # a small JSON 503 instead so callers (img tags, fetch) get a clean
        # status code rather than an unhandled exception.
        return _frontend_maintenance_json_response(
            detail=f"asset missing: {filename}",
        )
    return FileResponse(
        str(asset),
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=3600"},
    )


_BOT_PAGE_DEEP_LINK_QUERY_PARAMS = frozenset(
    (
        "broker",
        "lane",
        "connected",
        "next_action",
        "trading_account_id",
        "account_env",
    )
)


def _frontend_bot_entry_redirect_target(request: Request) -> Optional[str]:
    params = request.query_params
    if any(params.get(name) for name in _BOT_PAGE_DEEP_LINK_QUERY_PARAMS):
        return None

    if "tg" not in params and "sig" not in params:
        return None

    query = request.url.query
    if query:
        return f"/?{query}"
    return "/"


@app.get("/bot", include_in_schema=False)
def frontend_v2_bot_redirect(request: Request):
    target = _frontend_bot_entry_redirect_target(request)
    if target is not None:
        return RedirectResponse(url=target, status_code=302)

    query = request.url.query
    target = "/bot/"
    if query:
        target = f"{target}?{query}"
    return RedirectResponse(url=target, status_code=302)


@app.get("/bot/", include_in_schema=False)
def frontend_v2_bot(request: Request):
    target = _frontend_bot_entry_redirect_target(request)
    if target is not None:
        return RedirectResponse(url=target, status_code=302)
    return _frontend_page_response("bot")


@app.get("/bot/index.txt", include_in_schema=False)
def frontend_v2_bot_rsc():
    return _frontend_rsc_response("bot")


@app.get("/bot/control", include_in_schema=False)
def frontend_v2_bot_control_redirect():
    return RedirectResponse(url="/bot/control/", status_code=302)


@app.get("/bot/control/", include_in_schema=False)
def frontend_v2_bot_control():
    return _frontend_page_response("bot/control")


@app.get("/bot/control/index.txt", include_in_schema=False)
def frontend_v2_bot_control_rsc():
    return _frontend_rsc_response("bot/control")


@app.get("/bot/ctrader/callback", include_in_schema=False)
def frontend_v2_bot_ctrader_callback_redirect(request: Request):
    query = request.url.query
    target = "/bot/ctrader/callback/"
    if query:
        target = f"{target}?{query}"
    return RedirectResponse(url=target, status_code=302)


@app.get("/bot/ctrader/callback/", include_in_schema=False)
def frontend_v2_bot_ctrader_callback():
    return _frontend_page_response("bot/ctrader/callback")


@app.get("/bot/ctrader/callback/index.txt", include_in_schema=False)
def frontend_v2_bot_ctrader_callback_rsc():
    return _frontend_rsc_response("bot/ctrader/callback")


@app.get("/cntx-labs-logo.svg", include_in_schema=False)
def frontend_v2_cntx_labs_logo():
    return _frontend_out_asset_response("cntx-labs-logo.svg", "image/svg+xml")


@app.get("/wallet", include_in_schema=False)
def frontend_v2_wallet_redirect():
    return RedirectResponse(url="/wallet/", status_code=302)


@app.get("/wallet/", include_in_schema=False)
def frontend_v2_wallet():
    return _frontend_page_response("wallet")


@app.get("/wallet/index.txt", include_in_schema=False)
def frontend_v2_wallet_rsc():
    return _frontend_rsc_response("wallet")


@app.get("/rewards", include_in_schema=False)
def frontend_v2_rewards_redirect():
    return RedirectResponse(url="/rewards/", status_code=302)


@app.get("/rewards/", include_in_schema=False)
def frontend_v2_rewards():
    return _frontend_page_response("rewards")


@app.get("/rewards/index.txt", include_in_schema=False)
def frontend_v2_rewards_rsc():
    return _frontend_rsc_response("rewards")


@app.get("/rankbot", include_in_schema=False)
def frontend_v2_rankbot_redirect():
    return RedirectResponse(url="/rankbot/", status_code=302)


@app.get("/rankbot/", include_in_schema=False)
def frontend_v2_rankbot():
    return _frontend_page_response("rankbot")


@app.get("/rankbot/index.txt", include_in_schema=False)
def frontend_v2_rankbot_rsc():
    return _frontend_rsc_response("rankbot")


@app.get("/live")
async def live() -> JSONResponse:
    payload = {
        "ok": True,
        "live": True,
        "startup_state": str(getattr(app.state, "startup_state", "created") or "created"),
        "uptime_sec": int(time.time() - _STARTED_AT),
        "started_at": int(_STARTED_AT),
        "version": APP_VERSION,
    }
    return JSONResponse(status_code=200, content=payload, headers={"Cache-Control": "no-store"})


@app.get("/health")
async def health(refresh: bool = False) -> JSONResponse:
    snapshot = await asyncio.to_thread(
        lambda: _build_observability_snapshot(app, force_refresh=refresh)
    )
    process = dict(snapshot.get("process") or {})
    body = {
        "ok": str(snapshot.get("status")) != "error",
        "status": snapshot.get("status"),
        "live": bool(snapshot.get("live")),
        "ready": bool(snapshot.get("ready")),
        "service_online": bool((snapshot.get("service_health_status") or {}).get("service_online")),
        "service_health_status": snapshot.get("service_health_status"),
        "runtime": snapshot.get("runtime"),
        "checks": snapshot.get("checks"),
        "critical_failures": snapshot.get("critical_failures"),
        "warning_failures": snapshot.get("warning_failures"),
        "startup": snapshot.get("startup"),
        "background_singleton": snapshot.get("background_singleton"),
        "reconciler": snapshot.get("reconciler"),
        "uptime_sec": process.get("uptime_sec"),
        "started_at": process.get("started_at"),
        "env": process.get("env"),
        "db_mode": process.get("db_mode"),
        "public_base_url": (settings.PUBLIC_BASE_URL or "").rstrip("/"),
        "base_dir": str(BASE_DIR),
        "static_dir": str(STATIC_DIR),
        "static_exists": STATIC_DIR.exists(),
        "img_dir": str(IMG_DIR),
        "img_dir_exists": IMG_DIR.exists(),
        "logo_exists": (IMG_DIR / "cntx-labs-logo.svg").exists(),
        "ai_available": _sync_ai_available(),
        "ai_runtime": get_ai_runtime_status_sync(),
        "version": process.get("version") or APP_VERSION,
    }
    status_code = 200 if str(snapshot.get("status")) != "error" else 503
    return JSONResponse(status_code=status_code, content=body, headers={"Cache-Control": "no-store"})


@app.get("/ready")
async def ready(refresh: bool = False) -> JSONResponse:
    snapshot = await asyncio.to_thread(
        lambda: _build_observability_snapshot(app, force_refresh=refresh)
    )
    body = {
        "ok": bool(snapshot.get("ready")),
        "ready": bool(snapshot.get("ready")),
        "status": snapshot.get("status"),
        "critical_failures": snapshot.get("critical_failures"),
        "warning_failures": snapshot.get("warning_failures"),
        "checks": snapshot.get("checks"),
        "service_online": bool((snapshot.get("service_health_status") or {}).get("service_online")),
        "background_singleton": snapshot.get("background_singleton"),
        "startup": snapshot.get("startup"),
        "runtime_observation": (snapshot.get("runtime") or {}).get("observation"),
        "version": (snapshot.get("process") or {}).get("version") or APP_VERSION,
    }
    status_code = 200 if bool(snapshot.get("ready")) else 503
    return JSONResponse(status_code=status_code, content=body, headers={"Cache-Control": "no-store"})


@app.get("/metrics", include_in_schema=False)
async def metrics(refresh: bool = False) -> PlainTextResponse:
    metrics_service = _get_control_plane_metrics_service(app)
    if metrics_service is None:
        return PlainTextResponse(
            "# metrics collection unavailable\n",
            status_code=503,
            media_type="text/plain; version=0.0.4; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    document = await asyncio.to_thread(
        lambda: metrics_service.render_prometheus(
            app_state=app.state,
            started_at=_STARTED_AT,
            version=APP_VERSION,
            force_refresh=refresh,
        )
    )
    return PlainTextResponse(
        document,
        status_code=200,
        media_type="text/plain; version=0.0.4; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


def main() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.BACKEND_HOST,
        port=settings.BACKEND_PORT,
        reload=(settings.APP_ENV == "dev"),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    import uvicorn

    pm2_managed = bool(os.environ.get("PM2_HOME"))
    workers = 1 if settings.APP_ENV == "dev" or pm2_managed else int(
        os.environ.get("UVICORN_WORKERS")
        or os.environ.get("WEB_CONCURRENCY")
        or "4"
    )
    workers = max(1, min(32, workers))

    limit_concurrency = None
    if workers > 1:
        try:
            limit_concurrency = int(os.environ.get("UVICORN_LIMIT_CONCURRENCY") or "1000")
        except (TypeError, ValueError):
            limit_concurrency = 1000

    uvicorn.run(
        "app.main:app",
        host=settings.BACKEND_HOST,
        port=settings.BACKEND_PORT,
        reload=(settings.APP_ENV == "dev"),
        workers=workers,
        limit_concurrency=limit_concurrency,
    )
