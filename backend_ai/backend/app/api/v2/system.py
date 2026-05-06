"""User-facing system health endpoints cho Mini App.

Phai duoc giu o muc dich `badge / banner` UX, KHONG expose internal runner detail.
- Yeu cau Telegram user auth (chong scraping infra cua public).
- Trach nhiem: nhan data tu runner_health_dashboard + reconciler + compute level.
- Khong cong them nghiem trong: compute level la suy luan, FE ra quyet dinh hien thi.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.v2.control_plane_deps import service_dep, user_dep
from app.core.internal_auth import require_backend_api_key
from app.infra.redis_streams import EVENT_STREAM_KEY
from app.repositories.control_plane_repository import ControlPlaneRepository
from app.services.control_plane_service import MT5ControlPlaneService
from app.services.store_service import get_process_store, get_store
from app.settings import settings

router = APIRouter(prefix="/system", tags=["system"])

# Healthz thresholds (sec) — co the override qua settings sau
_HEALTHZ_DB_LATENCY_DEGRADED_MS = 200
_HEALTHZ_REDIS_LATENCY_DEGRADED_MS = 100
_HEALTHZ_SCHEDULER_STALE_DEGRADED_SEC = 300  # 5 min
_HEALTHZ_SCHEDULER_STALE_DOWN_SEC = 1800  # 30 min


# Threshold mac dinh; co the override bang settings sau khi thu nghiem voi user thuc.
_DEFAULT_VERIFICATION_QUEUE_PER_RUNNER_DEGRADED = 10
_DEFAULT_COMMAND_QUEUE_PER_RUNNER_DEGRADED = 20


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return int(default)
        return int(value)
    except Exception:
        return int(default)


def _safe_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _ops_thresholds() -> dict[str, int]:
    return {
        "verification_backlog": max(1, int(getattr(settings, "OPS_VERIFICATION_BACKLOG_THRESHOLD", 20) or 20)),
        "command_backlog": max(1, int(getattr(settings, "OPS_COMMAND_BACKLOG_THRESHOLD", 40) or 40)),
        "event_backlog": max(1, int(getattr(settings, "OPS_EVENT_BACKLOG_THRESHOLD", 100) or 100)),
    }


async def _safe_xpending_count(redis: Any, *, stream_key: str, group_name: str) -> int:
    try:
        pending = await redis.xpending(stream_key, group_name)
    except Exception:
        return 0
    if isinstance(pending, dict):
        return _safe_int(pending.get("pending") or pending.get("count") or 0)
    if isinstance(pending, (list, tuple)) and pending:
        return _safe_int(pending[0])
    return 0


async def _collect_ops_redis_queues(runner_ids: list[str]) -> dict[str, Any]:
    """Read Redis queue depths only. Khong pop/ack/clear queue."""
    ids = sorted({str(item or "").strip() for item in runner_ids if str(item or "").strip()})
    out: dict[str, Any] = {
        "redis_available": False,
        "redis_verification_depth": 0,
        "redis_command_depth": 0,
        "redis_event_pending": 0,
        "redis_event_stream_length": 0,
        "runner_queue_depths": [],
        "error": None,
    }
    try:
        from app.core.redis_client import get_redis_read

        redis = await get_redis_read(decode_responses=True)
        if redis is None:
            out["error"] = "redis_unavailable"
            return out
        out["redis_available"] = True
        for runner_id in ids:
            depths = {
                "runner_id": runner_id,
                "verification": _safe_int(await redis.llen(f"mt5:runner:{runner_id}:verification")),
                "verification_processing": _safe_int(await redis.llen(f"mt5:runner:{runner_id}:verification:processing")),
                "commands": _safe_int(await redis.llen(f"mt5:runner:{runner_id}:commands")),
                "commands_processing": _safe_int(await redis.llen(f"mt5:runner:{runner_id}:commands:processing")),
            }
            out["runner_queue_depths"].append(depths)
            out["redis_verification_depth"] += depths["verification"]
            out["redis_command_depth"] += depths["commands"]
        out["redis_event_stream_length"] = _safe_int(await redis.xlen(EVENT_STREAM_KEY))
        out["redis_event_pending"] = await _safe_xpending_count(
            redis,
            stream_key=EVENT_STREAM_KEY,
            group_name=str(getattr(settings, "CONTROL_PLANE_EVENT_CONSUMER_GROUP", "control-plane-event-audit") or "control-plane-event-audit"),
        )
    except Exception as exc:
        out["redis_available"] = False
        out["error"] = f"{exc.__class__.__name__}:{str(exc)[:120]}"
    return out


def _sum_runner_queue_depths(runner_queue_depths: list[dict[str, Any]], field: str) -> int:
    return sum(_safe_int(item.get(field)) for item in runner_queue_depths if isinstance(item, dict))


def _runner_queue_depths_for(queues: dict[str, Any], runner_id: str) -> dict[str, Any]:
    runner_id_s = str(runner_id or "").strip()
    for item in list(queues.get("runner_queue_depths") or []):
        if isinstance(item, dict) and str(item.get("runner_id") or "").strip() == runner_id_s:
            return {
                "redis_available": bool(queues.get("redis_available")),
                "verification": _safe_int(item.get("verification")),
                "verification_processing": _safe_int(item.get("verification_processing")),
                "commands": _safe_int(item.get("commands")),
                "commands_processing": _safe_int(item.get("commands_processing")),
                "error": queues.get("error"),
            }
    return {
        "redis_available": bool(queues.get("redis_available")),
        "verification": 0,
        "verification_processing": 0,
        "commands": 0,
        "commands_processing": 0,
        "error": queues.get("error"),
    }


def _readiness_nested_dict(source: dict[str, Any], key: str) -> dict[str, Any]:
    value = source.get(key)
    return dict(value) if isinstance(value, dict) else {}


def _readiness_first_int(sources: list[dict[str, Any]], keys: tuple[str, ...]) -> int:
    for source in sources:
        for key in keys:
            if key in source:
                return _safe_int(source.get(key))
    return 0


def _build_session0_readiness(runner_raw: dict[str, Any]) -> dict[str, Any]:
    """Classify MT5 Session 0 terminals for stress/readiness only.

    Runner-owned Session 0 terminals are blockers because they can mean MT5 is
    running in the wrong desktop/session for the Windows runner. Foreign Session
    0 terminals outside the runner root are reported as warnings only.
    """
    sources = [
        runner_raw,
        _readiness_nested_dict(runner_raw, "session0"),
        _readiness_nested_dict(runner_raw, "terminal_sessions"),
        _readiness_nested_dict(runner_raw, "terminals"),
    ]
    runner_owned = _readiness_first_int(
        sources,
        (
            "runner_owned_session0_terminals",
            "runner_owned_session0_terminal_count",
            "runner_owned_session0_count",
            "runner_owned_session0",
        ),
    )
    foreign = _readiness_first_int(
        sources,
        (
            "foreign_session0_terminals",
            "foreign_session0_terminal_count",
            "foreign_session0_count",
            "foreign_session0",
        ),
    )
    classifications: Any = {}
    for source in sources:
        raw = source.get("foreign_session0_classifications") or source.get("foreign_session0_classification")
        if isinstance(raw, dict):
            classifications = dict(raw)
            break
    return {
        "runner_owned_session0_terminals": max(0, runner_owned),
        "foreign_session0_terminals": max(0, foreign),
        "foreign_session0_classifications": classifications if isinstance(classifications, dict) else {},
        "foreign_session0_blocking": False,
    }


def _build_runner_readiness(
    snapshot: dict[str, Any],
    queues: dict[str, Any],
    *,
    expected_bot: Optional[str] = None,
    now: int | None = None,
) -> dict[str, Any]:
    """Build read-only readiness gate cho mot runner VPS.

    Endpoint nay danh cho ops/internal, khong dispatch job va khong expose host/secret.
    """
    runner_id = str(snapshot.get("runner_id") or "").strip()
    registered = bool(snapshot.get("registered"))
    runner_raw = dict(snapshot.get("runner") or {})
    slots_raw = dict(snapshot.get("slots") or {})
    bot_codes = [str(item).strip() for item in (runner_raw.get("bot_codes") or []) if str(item).strip()]
    bot_code_set = {item.lower() for item in bot_codes}
    expected_bot_s = str(expected_bot or "").strip()

    runner = {
        "registered": registered,
        "status": str(runner_raw.get("status") or "unknown"),
        "stale": bool(runner_raw.get("is_stale")),
        "max_slots": _safe_int(runner_raw.get("max_slots")),
        "last_registered_at": runner_raw.get("last_registered_at"),
        "last_heartbeat_at": runner_raw.get("last_heartbeat_at"),
    }
    session0 = _build_session0_readiness(runner_raw)
    slots = {
        "total": _safe_int(slots_raw.get("total")),
        "expected": _safe_int(slots_raw.get("expected")),
        "ready": _safe_int(slots_raw.get("ready")),
        "available": _safe_int(slots_raw.get("available")),
        "ipc_ready": _safe_int(slots_raw.get("ipc_ready")),
        "start_eligible": _safe_int(slots_raw.get("start_eligible")),
        "start_available": _safe_int(
            slots_raw.get("start_available")
            or slots_raw.get("start_available_slots")
            or slots_raw.get("start_eligible")
            or slots_raw.get("start_eligible_slots")
            or slots_raw.get("ready_slots")
            or slots_raw.get("ready")
        ),
        "active": _safe_int(slots_raw.get("active")),
        "verifying": _safe_int(slots_raw.get("verifying")),
        "reserved": _safe_int(slots_raw.get("reserved")),
        "degraded": _safe_int(slots_raw.get("degraded")),
        "broken": _safe_int(slots_raw.get("broken")),
    }
    queue_summary = {
        "redis_available": bool(queues.get("redis_available")),
        "verification": _safe_int(queues.get("verification")),
        "verification_processing": _safe_int(queues.get("verification_processing")),
        "commands": _safe_int(queues.get("commands")),
        "commands_processing": _safe_int(queues.get("commands_processing")),
    }
    bot_catalog = {
        "expected_bot": expected_bot_s or None,
        "available": (not expected_bot_s) or (expected_bot_s.lower() in bot_code_set),
        "bot_codes": sorted(bot_codes),
    }

    blockers: list[str] = []
    warnings: list[str] = []
    verification_queue_depth = queue_summary["verification"] + queue_summary["verification_processing"]
    command_queue_depth = queue_summary["commands"] + queue_summary["commands_processing"]
    queue_thresholds = _ops_thresholds()
    if not registered:
        blockers.append("runner_not_registered")
    elif runner["status"].lower() != "online":
        blockers.append("runner_offline")
    if runner["stale"]:
        blockers.append("runner_stale")
    has_start_capacity = bool(slots["start_available"] > 0 and slots["available"] > 0)
    if slots["expected"] > 0 and slots["total"] < slots["expected"]:
        (warnings if has_start_capacity else blockers).append("slots_missing")
    if slots["ready"] <= 0:
        blockers.append("no_ready_slots")
    if slots["available"] <= 0:
        blockers.append("no_available_slots")
    if slots["ipc_ready"] <= 0 and slots.get("start_available", 0) <= 0:
        blockers.append("no_ipc_ready_slots")
    elif slots["expected"] > 0 and slots["ipc_ready"] < slots["expected"]:
        (warnings if has_start_capacity else blockers).append("ipc_ready_slots_below_expected")
    if slots["start_eligible"] <= 0 or slots["start_available"] <= 0:
        blockers.append("no_start_eligible_slots")
    elif slots["expected"] > 0 and slots["start_available"] < slots["expected"]:
        (warnings if has_start_capacity else blockers).append("start_eligible_slots_below_expected")
    if slots["degraded"] > 0:
        (warnings if has_start_capacity else blockers).append("degraded_slots")
    if slots["broken"] > 0:
        (warnings if has_start_capacity else blockers).append("broken_slots")
    if slots["active"] > 0:
        (warnings if has_start_capacity else blockers).append("active_slots")
    if slots["verifying"] > 0:
        (warnings if has_start_capacity else blockers).append("verifying_slots")
    if not queue_summary["redis_available"]:
        blockers.append("redis_unavailable")
    if verification_queue_depth > _safe_int(queue_thresholds.get("verification_backlog"), 20):
        blockers.append("verification_queue_backlog")
    elif verification_queue_depth > 0:
        (warnings if has_start_capacity else blockers).append("verification_queue_backlog")
    if command_queue_depth > _safe_int(queue_thresholds.get("command_backlog"), 40):
        blockers.append("command_queue_backlog")
    elif command_queue_depth > 0:
        (warnings if has_start_capacity else blockers).append("command_queue_backlog")
    if expected_bot_s and not bot_catalog["available"]:
        blockers.append("bot_not_available_on_runner")
    if session0["runner_owned_session0_terminals"] > 0:
        blockers.append("runner_owned_session0_terminals")
    if session0["foreign_session0_terminals"] > 0:
        warnings.append("foreign_session0_terminals_non_blocking")

    # Stable order, de-dupe khi nhieu dieu kien cung sinh ra mot blocker.
    deduped_blockers = list(dict.fromkeys(blockers))
    deduped_warnings = list(dict.fromkeys(warnings))
    ok = len(deduped_blockers) == 0
    return {
        "ok": ok,
        "safe_to_accept_users": ok,
        "updated_at": int(now or time.time()),
        "runner_id": runner_id,
        "runner": runner,
        "slots": slots,
        "queues": queue_summary,
        "session0": session0,
        "bot_catalog": bot_catalog,
        "blockers": deduped_blockers,
        "warnings": deduped_warnings,
    }


def _build_ops_summary(
    snapshot: dict[str, Any],
    queues: dict[str, Any],
    *,
    thresholds: dict[str, int] | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    thresholds = dict(thresholds or _ops_thresholds())
    runner_queue_depths = list(queues.get("runner_queue_depths") or [])
    runners_raw = dict(snapshot.get("runners") or {})
    slots_raw = dict(snapshot.get("slots") or {})
    verification_raw = dict(snapshot.get("verification") or {})
    commands_raw = dict(snapshot.get("commands") or {})
    deployments_raw = dict(snapshot.get("deployments") or {})
    bindings_raw = dict(snapshot.get("bindings") or {})

    verification_processing = _sum_runner_queue_depths(runner_queue_depths, "verification_processing")
    command_processing_queue = _sum_runner_queue_depths(runner_queue_depths, "commands_processing")

    runners = {
        "total": _safe_int(runners_raw.get("total")),
        "online": _safe_int(runners_raw.get("online")),
        "stale": _safe_int(runners_raw.get("stale")),
        "degraded": _safe_int(runners_raw.get("degraded")),
    }
    slots = {
        "total": _safe_int(slots_raw.get("total")),
        "ready": _safe_int(slots_raw.get("ready")),
        "active": _safe_int(slots_raw.get("active")),
        "verifying": _safe_int(slots_raw.get("verifying")),
        "degraded": _safe_int(slots_raw.get("degraded")),
        "broken": _safe_int(slots_raw.get("broken")),
        "available": _safe_int(slots_raw.get("available")),
    }
    verification = {
        "pending": _safe_int(verification_raw.get("pending")),
        "dispatched": _safe_int(verification_raw.get("dispatched")),
        "processing": verification_processing,
        "failed_recent_1h": _safe_int(verification_raw.get("failed_recent_1h")),
        "p50_ms": _safe_optional_int(verification_raw.get("p50_ms")),
        "p95_ms": _safe_optional_int(verification_raw.get("p95_ms")),
    }
    commands = {
        "pending": _safe_int(commands_raw.get("pending")),
        "processing": max(_safe_int(commands_raw.get("processing")), command_processing_queue),
        "failed_recent_1h": _safe_int(commands_raw.get("failed_recent_1h")),
    }
    deployments = {
        "running": _safe_int(deployments_raw.get("running")),
        "starting": _safe_int(deployments_raw.get("starting")),
        "stopping": _safe_int(deployments_raw.get("stopping")),
        "failed": _safe_int(deployments_raw.get("failed")),
        "stale": _safe_int(deployments_raw.get("stale")),
    }
    queue_summary = {
        "redis_available": bool(queues.get("redis_available")),
        "redis_verification_depth": _safe_int(queues.get("redis_verification_depth")),
        "redis_command_depth": _safe_int(queues.get("redis_command_depth")),
        "redis_event_pending": _safe_int(queues.get("redis_event_pending")),
        "redis_event_stream_length": _safe_int(queues.get("redis_event_stream_length")),
        "runner_queue_depths": runner_queue_depths,
    }
    bindings = {
        "sticky_mismatch": _safe_int(bindings_raw.get("sticky_mismatch")),
    }

    alerts: list[str] = []
    warnings: list[str] = []
    cluster_capacity_available = bool(runners["online"] > 0 and slots["available"] > 0)
    verification_backlog = max(
        verification["pending"] + verification["dispatched"] + verification["processing"],
        queue_summary["redis_verification_depth"] + verification_processing,
    )
    command_backlog = max(
        commands["pending"] + commands["processing"],
        queue_summary["redis_command_depth"] + command_processing_queue,
    )
    if verification_backlog > _safe_int(thresholds.get("verification_backlog"), 20):
        alerts.append("verification_backlog")
    if command_backlog > _safe_int(thresholds.get("command_backlog"), 40):
        alerts.append("command_backlog")
    if any(
        _safe_int(item.get("verification")) > _safe_int(thresholds.get("verification_backlog"), 20)
        or _safe_int(item.get("commands")) > _safe_int(thresholds.get("command_backlog"), 40)
        for item in runner_queue_depths
        if isinstance(item, dict)
    ):
        alerts.append("queue_backlog")
    if queue_summary["redis_event_pending"] > _safe_int(thresholds.get("event_backlog"), 100):
        alerts.append("event_backlog")
    if runners["degraded"] > 0:
        (warnings if cluster_capacity_available else alerts).append("runner_degraded")
    if runners["stale"] > 0:
        (warnings if cluster_capacity_available else alerts).append("stale_runner")
    if slots["broken"] > 0:
        (warnings if cluster_capacity_available else alerts).append("broken_slots")
    if deployments["stale"] > 0:
        (warnings if cluster_capacity_available else alerts).append("stale_deployments")
    if runners["online"] > 0 and slots["total"] > 0 and slots["available"] <= 0:
        alerts.append("no_cluster_capacity")
        if slots["degraded"] <= 0 and slots["broken"] <= 0 and slots["active"] + slots["verifying"] >= slots["total"]:
            alerts.append("runner_full")
    if bindings["sticky_mismatch"] > 0:
        alerts.append("sticky_mismatch")

    return {
        "ok": len(alerts) == 0,
        "updated_at": int(now or time.time()),
        "runners": runners,
        "slots": slots,
        "verification": verification,
        "commands": commands,
        "queues": queue_summary,
        "deployments": deployments,
        "bindings": bindings,
        "alerts": alerts,
        "warnings": warnings,
        "thresholds": {
            **dict(snapshot.get("thresholds") or {}),
            **thresholds,
        },
    }


def _avg_per_runner(total: int, n_runners: int) -> float:
    if n_runners <= 0:
        return 0.0
    return float(total) / float(n_runners)


def _build_health_badge(dashboard: dict[str, Any]) -> dict[str, Any]:
    """Compute level + message tu runner_health_dashboard payload."""
    summary = dict(dashboard.get("summary") or {})
    total_runners = int(summary.get("total_runners") or 0)
    online_runners = int(summary.get("online_runners") or 0)
    ready_runners = int(summary.get("ready_runners") or 0)
    maintenance_runners = int(summary.get("maintenance_runners") or 0)
    degraded_runners = int(summary.get("degraded_runners") or 0)
    full_runners = int(summary.get("full_runners") or 0)
    stale_runners = int(summary.get("stale_runners") or 0)
    verification_q = int(summary.get("verification_queue_depth") or 0)
    command_q = int(summary.get("command_queue_depth") or 0)

    avg_verif = _avg_per_runner(verification_q, max(total_runners, 1))
    avg_cmd = _avg_per_runner(command_q, max(total_runners, 1))

    if total_runners == 0:
        level = "maintenance"
        message_vi = "Hệ thống đang khởi động lại. Vui lòng thử lại sau vài phút."
        message_en = "System is rebooting. Please retry in a few minutes."
        reason = "no_runners_registered"
    elif ready_runners == 0:
        level = "maintenance"
        if maintenance_runners >= total_runners:
            message_vi = "Toàn bộ MT5 runner đang bảo trì. Bot tạm thời không nhận lệnh mới."
            message_en = "All MT5 runners are under maintenance. Bots cannot accept new commands."
            reason = "all_runners_maintenance"
        else:
            message_vi = "Không có runner sẵn sàng nhận lệnh. Đội ngũ vận hành đang xử lý."
            message_en = "No runner is ready to accept commands. Operations team is on it."
            reason = "no_ready_runners"
    elif (
        ready_runners < total_runners
        or stale_runners > 0
        or degraded_runners > 0
        or full_runners > 0
        or avg_verif > _DEFAULT_VERIFICATION_QUEUE_PER_RUNNER_DEGRADED
        or avg_cmd > _DEFAULT_COMMAND_QUEUE_PER_RUNNER_DEGRADED
    ):
        level = "degraded"
        if avg_verif > _DEFAULT_VERIFICATION_QUEUE_PER_RUNNER_DEGRADED:
            message_vi = "Hệ thống đang xử lý nhiều yêu cầu xác thực. Vui lòng đợi một lát."
            message_en = "System is processing many verification requests. Please wait a moment."
            reason = "verification_backlog"
        elif avg_cmd > _DEFAULT_COMMAND_QUEUE_PER_RUNNER_DEGRADED:
            message_vi = "Hàng lệnh đang đông. Bot vẫn chạy nhưng phản hồi có thể chậm hơn bình thường."
            message_en = "Command queue is busy. Bots still run but responses may be slower than usual."
            reason = "command_backlog"
        elif full_runners > 0:
            message_vi = "Một số runner đã đầy slot. Bot mới có thể phải chờ slot."
            message_en = "Some runners are at full capacity. New bots may need to wait for a slot."
            reason = "runner_full"
        elif degraded_runners > 0 or stale_runners > 0:
            message_vi = "Một số runner đang bận hoặc mất kết nối tạm thời."
            message_en = "Some runners are degraded or temporarily disconnected."
            reason = "runner_degraded"
        else:
            message_vi = "Hệ thống đang chạy nhưng một số runner chưa sẵn sàng."
            message_en = "System is running but some runners are not ready yet."
            reason = "runner_not_ready"
    else:
        level = "ok"
        message_vi = "Hệ thống ổn định."
        message_en = "All systems normal."
        reason = "all_green"

    return {
        "level": level,
        "reason": reason,
        "message_vi": message_vi,
        "message_en": message_en,
        "summary": {
            "total_runners": total_runners,
            "online_runners": online_runners,
            "ready_runners": ready_runners,
            "maintenance_runners": maintenance_runners,
            "degraded_runners": degraded_runners,
            "full_runners": full_runners,
            "stale_runners": stale_runners,
            "verification_queue_depth": verification_q,
            "command_queue_depth": command_q,
            "avg_verification_queue_per_runner": round(avg_verif, 2),
            "avg_command_queue_per_runner": round(avg_cmd, 2),
            "capacity_available": bool(summary.get("capacity_available")),
        },
        "thresholds": {
            "verification_queue_per_runner_degraded": _DEFAULT_VERIFICATION_QUEUE_PER_RUNNER_DEGRADED,
            "command_queue_per_runner_degraded": _DEFAULT_COMMAND_QUEUE_PER_RUNNER_DEGRADED,
        },
        "generated_at": int(time.time()),
    }


async def _check_postgres(request: Request | None = None, store: Any | None = None) -> dict[str, Any]:
    """Ping Postgres SELECT 1 + measure latency."""
    started = time.monotonic()
    try:
        store = store or (get_store(request) if request is not None else get_process_store())

        def _ping(con, cur):
            cur.execute("SELECT 1")
            return cur.fetchone()

        await asyncio.to_thread(store._with_retry_read, _ping)
        latency_ms = int((time.monotonic() - started) * 1000)
    except Exception as exc:
        return {
            "status": "down",
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error": f"{exc.__class__.__name__}:{str(exc)[:120]}",
        }
    if latency_ms > _HEALTHZ_DB_LATENCY_DEGRADED_MS:
        return {"status": "degraded", "latency_ms": latency_ms, "error": None}
    return {"status": "ok", "latency_ms": latency_ms, "error": None}


async def _check_redis() -> dict[str, Any]:
    """Ping Redis + measure latency. KHONG raise neu Redis down -> tra status='down'."""
    started = time.monotonic()
    try:
        from app.core.redis_client import get_redis_read

        redis = await get_redis_read(decode_responses=True)
        if redis is None:
            return {
                "status": "down",
                "latency_ms": 0,
                "error": "redis_client_unavailable",
            }
        await redis.ping()
        latency_ms = int((time.monotonic() - started) * 1000)
    except Exception as exc:
        return {
            "status": "down",
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error": f"{exc.__class__.__name__}:{str(exc)[:120]}",
        }
    if latency_ms > _HEALTHZ_REDIS_LATENCY_DEGRADED_MS:
        return {"status": "degraded", "latency_ms": latency_ms, "error": None}
    return {"status": "ok", "latency_ms": latency_ms, "error": None}


def _check_scheduler(
    snapshot: Any,
    name: str,
    *,
    singleton_state: str | None = None,
    singleton_updated_at: int | None = None,
) -> dict[str, Any]:
    """Inspect snapshot 1 background scheduler (reconciler / circuit breaker).

    Snapshot expected: {run_count, last_started_at, last_success_at, last_error}.
    Stale > 5min -> degraded, > 30min -> down. Run_count == 0 va boot < 60s -> warming.
    """
    if snapshot is None and str(singleton_state or "").lower() == "busy":
        now = int(time.time())
        age_sec = None
        if singleton_updated_at:
            age_sec = now - int(singleton_updated_at)
        if age_sec is not None and age_sec > _HEALTHZ_SCHEDULER_STALE_DEGRADED_SEC:
            return {
                "status": "degraded",
                "error": "singleton_lease_stale",
                "name": name,
                "delegated": True,
                "singleton_state": "busy",
                "singleton_age_sec": age_sec,
            }
        return {
            "status": "ok",
            "error": None,
            "name": name,
            "delegated": True,
            "singleton_state": "busy",
            "singleton_age_sec": age_sec,
        }
    if snapshot is None:
        return {"status": "unknown", "error": "scheduler_not_initialized", "name": name}
    snap = snapshot if isinstance(snapshot, dict) else {}
    last_success = int(snap.get("last_success_at") or 0)
    run_count = int(snap.get("run_count") or 0)
    last_error = snap.get("last_error")
    now = int(time.time())
    if run_count == 0:
        # Chua chay lan nao -> chap nhan trong 60s dau (booting)
        return {
            "status": "warming",
            "run_count": 0,
            "last_success_age_sec": None,
            "error": None,
            "name": name,
        }
    age_sec = now - last_success if last_success > 0 else None
    if age_sec is None:
        return {"status": "down", "run_count": run_count, "last_success_age_sec": None, "error": "no_success_yet", "name": name}
    if age_sec > _HEALTHZ_SCHEDULER_STALE_DOWN_SEC:
        return {
            "status": "down",
            "run_count": run_count,
            "last_success_age_sec": age_sec,
            "error": last_error or "scheduler_stale_down",
            "name": name,
        }
    if age_sec > _HEALTHZ_SCHEDULER_STALE_DEGRADED_SEC:
        return {
            "status": "degraded",
            "run_count": run_count,
            "last_success_age_sec": age_sec,
            "error": last_error,
            "name": name,
        }
    return {
        "status": "ok",
        "run_count": run_count,
        "last_success_age_sec": age_sec,
        "error": last_error,
        "name": name,
    }


def _command_delivery_replay_enabled() -> bool:
    return bool(getattr(settings, "COMMAND_DELIVERY_REPLAY_ENABLED", True))


def _command_delivery_replay_stale_sec() -> int:
    interval = max(5, int(getattr(settings, "COMMAND_DELIVERY_REPLAY_INTERVAL_SEC", 15) or 15))
    configured = int(getattr(settings, "COMMAND_DELIVERY_REPLAY_STALE_DEGRADED_SEC", 0) or 0)
    return max(configured, interval * 3, 60)


async def _count_command_delivery_backlog(request: Request | None = None, store: Any | None = None) -> dict[str, Any]:
    try:
        store = store or (get_store(request) if request is not None else get_process_store())
        repo = ControlPlaneRepository(store)
        count = await asyncio.to_thread(repo.count_command_delivery_replay_backlog)
        return {"count": _safe_int(count), "error": None}
    except Exception as exc:
        return {"count": -1, "error": f"{exc.__class__.__name__}:{str(exc)[:120]}"}


def _check_command_delivery_reconciler(
    snapshot: Any,
    *,
    backlog_count: int,
    backlog_error: str | None,
    singleton_state: str | None = None,
    singleton_updated_at: int | None = None,
) -> dict[str, Any]:
    enabled = _command_delivery_replay_enabled()
    now = int(time.time())
    stale_after_sec = _command_delivery_replay_stale_sec()
    base: dict[str, Any] = {
        "name": "command_delivery_reconciler",
        "enabled": enabled,
        "last_run_at": 0,
        "last_success_at": 0,
        "last_error_at": 0,
        "last_error_class": None,
        "last_result": {},
        "lag_seconds": None,
        "backlog_count": int(backlog_count),
        "stale_after_sec": stale_after_sec,
    }
    if not enabled:
        return {**base, "status": "ok", "error": None}
    if snapshot is None and str(singleton_state or "").lower() == "busy":
        age_sec = now - int(singleton_updated_at) if singleton_updated_at else None
        status = "degraded" if age_sec is not None and age_sec > _HEALTHZ_SCHEDULER_STALE_DEGRADED_SEC else "ok"
        return {
            **base,
            "status": status,
            "error": "singleton_lease_stale" if status == "degraded" else None,
            "delegated": True,
            "idle_reason": "background_singleton_delegated",
            "singleton_state": "busy",
            "singleton_age_sec": age_sec,
            "last_result": {"state": "idle_delegated", "backlog_count": int(backlog_count)},
        }
    if snapshot is None:
        return {**base, "status": "degraded", "error": "scheduler_not_initialized"}

    snap = snapshot if isinstance(snapshot, dict) else {}
    last_run_at = _safe_int(snap.get("last_run_at") or snap.get("last_started_at"))
    last_success_at = _safe_int(snap.get("last_success_at"))
    last_error_at = _safe_int(snap.get("last_error_at"))
    last_error_class = snap.get("last_error_class")
    last_result = dict(snap.get("last_result") or {})
    run_count = _safe_int(snap.get("run_count"))
    lag_seconds = now - last_success_at if last_success_at > 0 else None
    out = {
        **base,
        "run_count": run_count,
        "last_run_at": last_run_at,
        "last_success_at": last_success_at,
        "last_error_at": last_error_at,
        "last_error_class": last_error_class,
        "last_result": last_result,
        "lag_seconds": lag_seconds,
        "backlog_count": int(backlog_count),
        "error": None,
    }
    if backlog_error:
        return {**out, "status": "degraded", "error": f"backlog_count_unavailable:{backlog_error}"}
    if run_count <= 0:
        return {**out, "status": "warming"}
    if lag_seconds is None:
        return {**out, "status": "degraded", "error": "no_success_yet"}
    if lag_seconds > stale_after_sec:
        return {**out, "status": "degraded", "error": "command_delivery_reconciler_stale"}
    if _safe_int(last_result.get("replay_failed")) > 0:
        return {**out, "status": "degraded", "error": "command_delivery_replay_failed"}
    return {**out, "status": "ok"}


def _aggregate_healthz_status(checks: dict[str, dict[str, Any]]) -> str:
    """Compute overall: down > degraded > warming > ok. unknown -> degraded."""
    statuses = [str(c.get("status") or "unknown") for c in checks.values()]
    if "down" in statuses:
        return "down"
    if "degraded" in statuses:
        return "degraded"
    if "unknown" in statuses:
        return "degraded"
    return "ok"


@router.get("/healthz")
async def system_healthz(
    request: Request,
    response: Response,
) -> dict[str, Any]:
    """Liveness/readiness check cho ops monitor (UptimeRobot, k8s probe, alert).

    Public (no auth). Tra HTTP 200 neu ok/degraded/warming, 503 neu down.
    Body: {status, version, uptime_sec, checks: {postgres, redis, reconciler, circuit_breaker_scheduler}, generated_at}
    Khong leak runner_id/queue_depth/host (chi expose status + latency_ms cho subcheck).
    """
    started_check = time.monotonic()
    pg_check, redis_check, command_backlog = await asyncio.gather(
        _check_postgres(request),
        _check_redis(),
        _count_command_delivery_backlog(request),
    )
    reconciler_snap = None
    cb_snap = None
    command_delivery_snap = None
    try:
        reconciler = getattr(request.app.state, "control_plane_reconciler", None)
        if reconciler is not None and hasattr(reconciler, "snapshot"):
            reconciler_snap = reconciler.snapshot()
    except Exception:
        reconciler_snap = None
    try:
        cb = getattr(request.app.state, "circuit_breaker_scheduler", None)
        if cb is not None and hasattr(cb, "snapshot"):
            cb_snap = cb.snapshot()
    except Exception:
        cb_snap = None
    try:
        command_delivery = getattr(request.app.state, "command_delivery_reconciler", None)
        if command_delivery is not None and hasattr(command_delivery, "snapshot"):
            command_delivery_snap = command_delivery.snapshot()
    except Exception:
        command_delivery_snap = None
    singleton_state = str(getattr(request.app.state, "background_singleton_state", "") or "")
    singleton_updated_at = int(getattr(request.app.state, "background_singleton_updated_at", 0) or 0)

    checks: dict[str, dict[str, Any]] = {
        "postgres": pg_check,
        "redis": redis_check,
        "reconciler": _check_scheduler(
            reconciler_snap,
            "control_plane_reconciler",
            singleton_state=singleton_state,
            singleton_updated_at=singleton_updated_at,
        ),
        "circuit_breaker_scheduler": _check_scheduler(
            cb_snap,
            "circuit_breaker_scheduler",
            singleton_state=singleton_state,
            singleton_updated_at=singleton_updated_at,
        ),
        "command_delivery_reconciler": _check_command_delivery_reconciler(
            command_delivery_snap,
            backlog_count=_safe_int(command_backlog.get("count"), -1),
            backlog_error=command_backlog.get("error"),
            singleton_state=singleton_state,
            singleton_updated_at=singleton_updated_at,
        ),
    }
    status = _aggregate_healthz_status(checks)
    if status == "down":
        response.status_code = 503

    started_at = float(getattr(request.app.state, "startup_started_at", 0) or 0) or time.time()
    uptime_sec = max(0, int(time.time() - started_at))
    version = str(getattr(request.app.state, "version", "") or "")

    return {
        "status": status,
        "version": version,
        "uptime_sec": uptime_sec,
        "checks": checks,
        "check_duration_ms": int((time.monotonic() - started_check) * 1000),
        "generated_at": int(time.time()),
    }


_ERROR_CATALOG_CACHE_TTL_SEC = 5 * 60
_ERROR_CATALOG_CACHE: dict[str, Any] = {"ts": 0.0, "entries": None}


def _build_error_catalog_entries() -> list[dict[str, Any]]:
    """Build de-duplicated entries list tu error_catalog._ENTRIES.

    Moi public_code chi xuat hien 1 lan; aliases gop vao field "aliases".
    Order: theo thu tu khai bao trong _ENTRIES.
    """
    from app.api.v2.error_catalog import _ENTRIES

    out: list[dict[str, Any]] = []
    for entry in _ENTRIES:
        out.append(
            {
                "public_code": entry.public_code,
                "http_status": int(entry.http_status),
                "message_vi": entry.message_vi,
                "message_en": entry.message_en,
                "action": entry.action,
                "retryable": bool(entry.retryable),
                "group": entry.group,
                "aliases": list(entry.aliases),
            }
        )
    return out


def _reset_error_catalog_cache_for_tests() -> None:
    """Reset cache process-local. Chi dung trong unit test."""
    _ERROR_CATALOG_CACHE["ts"] = 0.0
    _ERROR_CATALOG_CACHE["entries"] = None


@router.get("/error-catalog")
async def system_error_catalog(
    user: dict = Depends(user_dep),  # noqa: ARG001 - require auth
) -> dict[str, Any]:
    """Tra ve full error catalog cho FE Mini App tai xuong i18n dictionary 1 lan.

    Cached process-local 5 phut (catalog static). FE co the dung `version` de
    invalidate cache phia FE khi backend deploy version moi.
    """
    now = time.time()
    cached = _ERROR_CATALOG_CACHE.get("entries")
    if cached is not None and (now - float(_ERROR_CATALOG_CACHE.get("ts") or 0.0)) < _ERROR_CATALOG_CACHE_TTL_SEC:
        entries = cached
    else:
        entries = _build_error_catalog_entries()
        _ERROR_CATALOG_CACHE["entries"] = entries
        _ERROR_CATALOG_CACHE["ts"] = now
    return {
        "version": "1",
        "generated_at": int(now),
        "entries": entries,
    }


@router.get("/ops-summary")
async def system_ops_summary(
    _: dict = Depends(require_backend_api_key),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict[str, Any]:
    """Internal ops snapshot cho runner/queue/deployment health.

    Read-only: chi SELECT Postgres + LLEN/XLEN/XPENDING Redis, khong dispatch command
    va khong expose credentials/user PII.
    """
    snapshot = service.ops_summary_snapshot()
    queues = await _collect_ops_redis_queues(list(snapshot.get("runner_ids") or []))
    return _build_ops_summary(snapshot, queues)


@router.get("/runner-readiness/{runner_id}")
async def system_runner_readiness(
    runner_id: str,
    expected_bot: Optional[str] = Query(default=None, min_length=1),
    _: dict = Depends(require_backend_api_key),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict[str, Any]:
    """Internal readiness gate cho runner VPS moi truoc khi nhan user.

    Read-only: chi SELECT Postgres + LLEN Redis, khong tao job/deployment/command.
    """
    snapshot = service.runner_readiness_snapshot(runner_id=runner_id)
    queues = await _collect_ops_redis_queues([runner_id])
    return _build_runner_readiness(
        snapshot,
        _runner_queue_depths_for(queues, runner_id),
        expected_bot=expected_bot,
    )


@router.get("/health-badge")
async def system_health_badge(
    user: dict = Depends(user_dep),  # noqa: ARG001 - require auth de chong scrape public
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    """Tra payload gon cho FE Mini App render badge trang thai he thong.

    Response:
      level: "ok" | "degraded" | "maintenance"
      reason: ma noi bo, FE co the dung de switch UX nang cao
      message_vi / message_en: chuoi mac dinh, FE co the override
      summary: cac so kha quat (KHONG expose runner_id/slot_id chi tiet)
      thresholds: nguong quy uoc dang dung de tinh level
      generated_at: epoch sec
    """
    try:
        dashboard = service.runner_health_dashboard()
    except Exception:
        # Khong throw -> Mini App khong vo banner. Tra ve maintenance soft.
        return {
            "level": "maintenance",
            "reason": "health_dashboard_unavailable",
            "message_vi": "Không lấy được trạng thái hệ thống. Vui lòng thử lại.",
            "message_en": "Could not retrieve system status. Please retry.",
            "summary": {},
            "thresholds": {
                "verification_queue_per_runner_degraded": _DEFAULT_VERIFICATION_QUEUE_PER_RUNNER_DEGRADED,
                "command_queue_per_runner_degraded": _DEFAULT_COMMAND_QUEUE_PER_RUNNER_DEGRADED,
            },
            "generated_at": int(time.time()),
        }
    return _build_health_badge(dashboard)
