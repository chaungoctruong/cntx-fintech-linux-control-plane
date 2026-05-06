from __future__ import annotations
import asyncio
import json
import logging
import time
from typing import Any, Callable
from app.ai.status import get_ai_runtime_status_sync, is_ai_available_sync
from app.core.log_hygiene import append_debug_trace
from app.repositories.control_plane_repository import ControlPlaneRepository
from app.services.store_service import get_process_store
from app.settings import settings

try:
    from app.ai.telegram_dev_service import send_dev_alert_once
except Exception:
    async def send_dev_alert_once(*args, **kwargs) -> bool:
        return False

log = logging.getLogger("watchdog")
_PROCESS_STARTED_AT = int(time.time())
SERVICE_HEALTH_STATUS: dict[str, Any] = {
    "service_online": False,
    "service_capacity_available": False,
    "linked_accounts": 0,
    "active_bot_runs": 0,
    "running_bot_runs": 0,
    "waiting_bot_runs": 0,
    "error_bot_runs": 0,
    "stale_heartbeat_runs": 0,
    "recent_event_count": 0,
    "runtime_heartbeat_grace_sec": 0,
    "last_runtime_activity_ts": 0,
    "ai_available": False,
    "ai_runtime": {"provider": "unknown", "available": False, "configured": False, "model": "", "details": {}},
    "updated_at": 0,
}


def _dbg_fc(message: str, data: dict[str, Any], *, hypothesis_id: str) -> None:
    append_debug_trace(
        location="backend_ai/backend/app/services/watchdog.py",
        message=message,
        data=data,
        hypothesis_id=hypothesis_id,
    )
async def _send_watchdog_alert(*, title: str, message: str, alert_key: str, cooldown_sec: int = 300) -> None:
    try:
        await send_dev_alert_once(
            message=message,
            title=title,
            alert_key=alert_key,
            cooldown_sec=cooldown_sec,
        )
    except Exception:
        log.debug("watchdog alert failed key=%s", alert_key, exc_info=True)


def _refresh_system_maintenance_state(store: Any, *, app_state: Any = None, timeout_sec: int = 60) -> bool:
    now = int(time.time())
    heartbeat_grace_sec = max(30, int(timeout_sec))
    service_online = False
    service_capacity_available = False
    linked_accounts = 0
    active_bot_runs = 0
    running_bot_runs = 0
    waiting_bot_runs = 0
    error_bot_runs = 0
    stale_heartbeat_runs = 0
    recent_event_count = 0
    last_runtime_activity_ts = 0
    try:
        repo = ControlPlaneRepository(store)
        summary = repo.get_runtime_health_summary(
            runner_stale_sec=heartbeat_grace_sec,
            deployment_stale_sec=heartbeat_grace_sec,
        )
        runners = dict(summary.get("runners") or {})
        deployments = dict(summary.get("deployments") or {})
        slots = dict(summary.get("slots") or {})
        accounts = dict(summary.get("accounts") or {})
        events = dict(summary.get("events") or {})

        linked_accounts = int(accounts.get("connected_accounts") or 0)
        active_bot_runs = int(deployments.get("desired_running_deployments") or 0)
        running_bot_runs = int(deployments.get("running_deployments") or 0)
        waiting_bot_runs = int(deployments.get("transitional_deployments") or 0)
        error_bot_runs = int(deployments.get("failed_deployments") or 0)
        stale_heartbeat_runs = int(deployments.get("stale_deployments") or 0)
        recent_event_count = int(events.get("recent_event_count") or 0)
        last_runtime_activity_ts = int(events.get("last_runtime_activity_ts") or 0)
        service_online = True
        service_capacity_available = (
            int(slots.get("ready_slots") or 0) > 0
            or (
                int(runners.get("online_runners") or 0) > 0
                and stale_heartbeat_runs == 0
            )
        )
    except Exception:
        service_online = False
        service_capacity_available = False

    SERVICE_HEALTH_STATUS["service_online"] = service_online
    SERVICE_HEALTH_STATUS["service_capacity_available"] = service_capacity_available
    SERVICE_HEALTH_STATUS["linked_accounts"] = linked_accounts
    SERVICE_HEALTH_STATUS["active_bot_runs"] = active_bot_runs
    SERVICE_HEALTH_STATUS["running_bot_runs"] = running_bot_runs
    SERVICE_HEALTH_STATUS["waiting_bot_runs"] = waiting_bot_runs
    SERVICE_HEALTH_STATUS["error_bot_runs"] = error_bot_runs
    SERVICE_HEALTH_STATUS["stale_heartbeat_runs"] = stale_heartbeat_runs
    SERVICE_HEALTH_STATUS["recent_event_count"] = recent_event_count
    SERVICE_HEALTH_STATUS["runtime_heartbeat_grace_sec"] = heartbeat_grace_sec
    SERVICE_HEALTH_STATUS["last_runtime_activity_ts"] = last_runtime_activity_ts
    SERVICE_HEALTH_STATUS["ai_available"] = is_ai_available_sync()
    SERVICE_HEALTH_STATUS["ai_runtime"] = get_ai_runtime_status_sync()
    SERVICE_HEALTH_STATUS["updated_at"] = now

    if app_state is not None:
        try:
            setattr(app_state, "is_service_online", bool(service_online))
            setattr(app_state, "service_health_status", dict(SERVICE_HEALTH_STATUS))
        except Exception:
            pass

    # #region agent log
    _dbg_fc(
        "watchdog.refresh_service_health",
        {
            "service_online": bool(service_online),
            "service_capacity_available": bool(service_capacity_available),
            "linked_accounts": int(linked_accounts),
            "active_bot_runs": int(active_bot_runs),
            "running_bot_runs": int(running_bot_runs),
            "waiting_bot_runs": int(waiting_bot_runs),
            "error_bot_runs": int(error_bot_runs),
            "stale_heartbeat_runs": int(stale_heartbeat_runs),
            "recent_event_count": int(recent_event_count),
            "runtime_heartbeat_grace_sec": int(SERVICE_HEALTH_STATUS["runtime_heartbeat_grace_sec"]),
        },
        hypothesis_id="H1",
    )
    # #endregion

    # Never force global maintenance for AI chat.
    return False


async def _emit_ops_alerts() -> None:
    snapshot = dict(SERVICE_HEALTH_STATUS)
    if int(snapshot.get("stale_heartbeat_runs") or 0) > 0:
        await _send_watchdog_alert(
            title="CONTROL PLANE DEPLOYMENTS STALE",
            message=(
                f"stale_heartbeat_runs={int(snapshot.get('stale_heartbeat_runs') or 0)}\n"
                f"runtime_heartbeat_grace_sec={int(snapshot.get('runtime_heartbeat_grace_sec') or 0)}"
            ),
            alert_key="watchdog.control_plane_deployments_stale",
            cooldown_sec=600,
        )
    if (
        int(snapshot.get("error_bot_runs") or 0) > 0
        and int(snapshot.get("active_bot_runs") or 0) > 0
    ):
        await _send_watchdog_alert(
            title="CONTROL PLANE DEPLOYMENTS FAILED",
            message=(
                f"error_bot_runs={int(snapshot.get('error_bot_runs') or 0)}\n"
                f"waiting_bot_runs={int(snapshot.get('waiting_bot_runs') or 0)}"
            ),
            alert_key="watchdog.control_plane_deployments_failed",
            cooldown_sec=600,
        )


async def system_watchdog_loop(
    interval_sec: int = 30,
    timeout_sec: int = 90,
    app_state: Any = None,
    cleanup_enabled: bool | Callable[[], bool] = True,
) -> None:
    log.info("[Watchdog] Started control-plane runtime monitor (%ss)", interval_sec)
    store = get_process_store()

    while True:
        try:
            await asyncio.sleep(interval_sec)

            _refresh_system_maintenance_state(store, app_state=app_state, timeout_sec=60)
            should_emit = cleanup_enabled() if callable(cleanup_enabled) else bool(cleanup_enabled)
            if should_emit:
                await _emit_ops_alerts()

        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(5)
