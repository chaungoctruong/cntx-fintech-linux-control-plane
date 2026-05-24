from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from threading import Lock
from typing import Any

from app.core.log_context import bind_log_context
from app.events.command_router import CommandRouterService
from app.events.runner_event_idempotency import stable_runner_event_id
from app.infra.redis_streams import RedisStreamPublisher
from app.models.control_plane import CommandType, DeploymentStatus, EventType
from app.orchestration.bot_runtime_contract import (
    bot_start_runtime_disabled_reason,
    bot_start_runtime_supported,
)
from app.orchestration.deployment_config import TRADING_CONFIG_SCHEMA_VERSION, normalize_deployment_config
from app.orchestration.start_failure_policy import (
    RUNNER_THROTTLE_FAILURE_THRESHOLD,
    RUNNER_THROTTLE_SEC,
    SLOT_QUARANTINE_SEC,
    classify_start_failure,
    start_failure_is_credential_failure,
    start_failure_reason,
)
from app.repositories.control_plane_repository import ControlPlaneRepository
from app.services import login_lease
from app.settings import settings
from runner.schemas.events import RunnerEvent, RunnerEventType
from ops_telegram_alerts import schedule_error_alert


_log = logging.getLogger("runner.event.ingest")

LOGIN_SLOT_FINAL_EVENT_TYPES = {
    EventType.LOGIN_SLOT_VERIFIED.value,
    EventType.LOGIN_SLOT_FAILED.value,
    EventType.LOGIN_SLOT_RELEASED.value,
}

_HEARTBEAT_STATE_KEYS = {
    "account_id",
    "active_account_id",
    "available_bot_names",
    "available_bots",
    "bot_catalog",
    "connection_status",
    "control_plane_state",
    "current_control_plane_state",
    "current_runner_state",
    "current_state",
    "deployment_id",
    "error",
    "error_code",
    "health_status",
    "last_error",
    "login_slot_status",
    "mt5_liveness_reason",
    "mt5_liveness_state",
    "reason",
    "runner_state",
    "slot_inventory",
    "slot_state",
    "status",
}

_HEARTBEAT_VOLATILE_KEYS = {
    "age_sec",
    "created_at",
    "elapsed_ms",
    "event_at",
    "heartbeat_at",
    "heartbeat_received_at",
    "latency_ms",
    "now",
    "observed_at",
    "sequence",
    "seq",
    "time",
    "timestamp",
    "ts",
    "updated_at",
    "uptime",
    "uptime_sec",
}

_CONFIG_HOT_UPDATE_FALLBACK_REASONS = {
    "unsupported_command",
    "no_supported_config_update_fields",
}
_CONFIG_HOT_UPDATE_FALLBACK_REASON_FRAGMENTS = (
    "timeout",
    "timed out",
)


def _canonical_slot_id(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    lowered = raw.lower()
    if lowered.startswith("slot_") or lowered.startswith("slot-"):
        return f"slot-{raw[5:]}"
    return raw


def _normalize_slot_projection_state(payload: dict[str, Any]) -> str | None:
    direct_candidates = (
        payload.get("new_state"),
        payload.get("slot_state"),
        payload.get("to_state"),
        payload.get("current_control_plane_state"),
        payload.get("control_plane_state"),
    )
    for candidate in direct_candidates:
        value = str(candidate or "").strip().lower()
        if value in {"ready", "allocated", "degraded", "broken", "disabled"}:
            return value

    runner_state = str(
        payload.get("current_state")
        or payload.get("current_runner_state")
        or ""
    ).strip().lower()
    if not runner_state:
        return None
    if runner_state in {"ready", "empty", "stopped"}:
        return "ready"
    if runner_state in {"active", "verifying", "preparing", "executor_preparing", "executor_ready", "listening", "stopping"}:
        return "allocated"
    if runner_state == "rebuilding":
        return "degraded"
    if runner_state in {"degraded", "broken", "disabled"}:
        return runner_state
    return None


def _deployment_wants_stopped(deployment: dict[str, Any] | None) -> bool:
    if not deployment:
        return False
    desired_state = str(deployment.get("desired_state") or "").strip().lower()
    status = str(deployment.get("status") or "").strip().lower()
    if desired_state == "stopped":
        return True
    return status in {"stop_requested", "stopped", "failed", "blocked"}


def _event_reason(payload: dict[str, Any]) -> str:
    return str(
        payload.get("reason")
        or payload.get("error_code")
        or payload.get("error")
        or payload.get("error_text")
        or payload.get("message")
        or ""
    ).strip().lower()


def _command_requests_terminal_kill(command: dict[str, Any] | None) -> bool:
    payload = command.get("payload_json") if isinstance(command, dict) else {}
    if not isinstance(payload, dict):
        return False
    command_type = str(command.get("command_type") or payload.get("command_type") or "").strip().upper()
    if command_type != CommandType.STOP_BOT.value:
        return False
    return any(str(payload.get(key, "")).strip().lower() in {"1", "true", "yes", "on"} for key in ("kill_mt5", "terminate_mt5"))


def _payload_confirms_terminal_stopped(payload: dict[str, Any]) -> bool:
    terminal_running_raw = payload.get("terminal_running")
    terminal_running = "" if terminal_running_raw is None else str(terminal_running_raw).strip().lower()
    if terminal_running in {"0", "false", "no", "off"}:
        return True
    terminal_pid = "" if payload.get("terminal_pid") is None else str(payload.get("terminal_pid")).strip().lower()
    return bool(terminal_pid) and terminal_pid in {"0", "none", "null"}


_RECOVERY_EVENT_ALIASES = {
    "RECOVERY_STARTED": EventType.RECOVERY_STARTED.value,
    "MT5_RECOVERY_STARTED": EventType.RECOVERY_STARTED.value,
    "AUTO_RECOVERY_STARTED": EventType.RECOVERY_STARTED.value,
    "RECOVERY_COMPLETED": EventType.RECOVERY_COMPLETED.value,
    "MT5_RECOVERY_COMPLETED": EventType.RECOVERY_COMPLETED.value,
    "AUTO_RECOVERY_COMPLETED": EventType.RECOVERY_COMPLETED.value,
    "RECOVERY_FAILED": EventType.RECOVERY_FAILED.value,
    "MT5_RECOVERY_FAILED": EventType.RECOVERY_FAILED.value,
    "AUTO_RECOVERY_FAILED": EventType.RECOVERY_FAILED.value,
    "RECOVERY_BLOCKED": EventType.RECOVERY_BLOCKED.value,
    "MT5_RECOVERY_BLOCKED": EventType.RECOVERY_BLOCKED.value,
    "AUTO_RECOVERY_BLOCKED": EventType.RECOVERY_BLOCKED.value,
    "MT5_RECOVERY_BUDGET_EXHAUSTED": EventType.MT5_RECOVERY_BUDGET_EXHAUSTED.value,
    "RECOVERY_BUDGET_EXHAUSTED": EventType.MT5_RECOVERY_BUDGET_EXHAUSTED.value,
    "BUDGET_EXHAUSTED": EventType.MT5_RECOVERY_BUDGET_EXHAUSTED.value,
}
_RECOVERY_EVENT_TYPES = set(_RECOVERY_EVENT_ALIASES.values())
_BACKEND_RECOVERY_STATUS = "mt5_recovery_deferred_to_backend"
_BACKEND_RECOVERY_REASONS = {
    "active_job_missing_for_target",
    "ea_bridge_heartbeat_stale",
    "execution_snapshot_hung",
    "mt5_terminal_not_running",
    "worker_process_missing",
}
_BACKEND_RECOVERY_HEALTH_STATUSES = {
    "active",
    "allocated",
    "executor_ready",
    "healthy",
    "listening",
    "ready",
    "running",
}
_BACKEND_RECOVERY_BROKEN_STATUSES = {"broken", "degraded", "error", "failed", "hung", "stale"}
_BACKEND_RECOVERY_SLOT_RUNTIME_STARTED_STATUSES = {"active", "executor_ready", "healthy", "listening", "running"}
_BACKEND_RECOVERY_EVENT_SUPPRESS_ACTIONS = {
    "budget_exhausted",
    "ignored_account_guard",
    "ignored_bot_guard",
    "ignored_deployment_not_found_after_claim",
    "ignored_guard_blocked",
    "ignored_missing_identity",
    "ignored_newer_active_deployment",
    "ignored_not_running",
    "ignored_not_running_after_claim",
    "suppressed_cooldown",
    "suppressed_in_flight",
}


def _payload_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _payload_falsey(value: Any) -> bool:
    return str(value).strip().lower() in {"0", "false", "no", "off", "disabled"}


def _payload_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _backend_recovery_reason(payload: dict[str, Any], report: dict[str, Any] | None = None) -> str:
    report_map = report or {}
    return (
        _payload_text(
            report_map.get("reason"),
            payload.get("reason"),
            payload.get("mt5_recovery_backend_required_reason"),
            payload.get("error_code"),
            payload.get("error"),
            payload.get("message"),
        ).lower()
        or _BACKEND_RECOVERY_STATUS
    )


def _backend_recovery_status_text(payload: dict[str, Any], report: dict[str, Any]) -> str:
    return _payload_text(
        payload.get("status"),
        payload.get("runner_event_type"),
        payload.get("event"),
        payload.get("event_type"),
        report.get("status"),
    ).lower()


def _backend_recovery_state_text(payload: dict[str, Any], report: dict[str, Any], metadata: dict[str, Any]) -> str:
    return _payload_text(
        report.get("state"),
        payload.get("state"),
        payload.get("slot_state"),
        payload.get("current_state"),
        payload.get("current_runner_state"),
        payload.get("runner_state"),
        payload.get("control_plane_state"),
        metadata.get("state"),
        metadata.get("slot_state"),
        metadata.get("current_runner_state"),
        metadata.get("mt5_liveness_state"),
    ).lower()


def _backend_recovery_metadata(payload: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    metadata = {}
    for source in (
        payload.get("metadata_json"),
        payload.get("metadata"),
        report.get("metadata_json"),
        report.get("metadata"),
    ):
        if isinstance(source, dict):
            metadata.update(source)
    return metadata


def _looks_backend_controlled_recovery(payload: dict[str, Any]) -> bool:
    report = _payload_dict(payload.get("report"))
    metadata = _backend_recovery_metadata(payload, report)
    recycle = _payload_dict(report.get("recycle") or payload.get("recycle"))
    status_text = _backend_recovery_status_text(payload, report)
    state_text = _backend_recovery_state_text(payload, report, metadata)
    reason_text = _backend_recovery_reason(payload, report)
    recovery_status = _payload_text(
        payload.get("mt5_recovery_status"),
        metadata.get("mt5_recovery_status"),
        report.get("mt5_recovery_status"),
    ).lower()
    last_recovery_error = _payload_text(
        payload.get("last_mt5_recovery_error"),
        metadata.get("last_mt5_recovery_error"),
        report.get("last_mt5_recovery_error"),
    ).lower()
    backend_required = (
        _payload_truthy(payload.get("backend_restart_required"))
        or _payload_truthy(report.get("backend_restart_required"))
        or _payload_truthy(recycle.get("required"))
    )
    auto_recovery_disabled = (
        _payload_falsey(payload.get("auto_recovery_enabled"))
        or _payload_falsey(report.get("auto_recovery_enabled"))
        or last_recovery_error == "auto_recovery_disabled"
    )
    backend_controlled = recovery_status == "backend_controlled" or auto_recovery_disabled
    broken = state_text in _BACKEND_RECOVERY_BROKEN_STATUSES
    return (
        status_text == _BACKEND_RECOVERY_STATUS
        or (backend_required and (broken or backend_controlled))
        or (backend_controlled and broken)
        or (reason_text in _BACKEND_RECOVERY_REASONS and broken)
    )


def _backend_recovery_current_slot_broken(payload: dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    report = _payload_dict(payload.get("report"))
    metadata = _backend_recovery_metadata(payload, report)
    state_text = _backend_recovery_state_text(payload, report, metadata)
    status_text = _backend_recovery_status_text(payload, report)
    reason_text = _backend_recovery_reason(payload, report)
    return (
        state_text in _BACKEND_RECOVERY_BROKEN_STATUSES
        or status_text == _BACKEND_RECOVERY_STATUS
        or reason_text in _BACKEND_RECOVERY_REASONS
    )


def _command_payload(command: Any) -> dict[str, Any]:
    if not isinstance(command, dict):
        return {}
    payload = command.get("payload_json")
    return dict(payload) if isinstance(payload, dict) else {}


def _is_backend_runner_recovery_command(command: Any) -> bool:
    payload = _command_payload(command)
    return (
        str(payload.get("control_flow") or "").strip() == "backend_runner_recovery"
        or _payload_truthy(payload.get("backend_runner_recovery"))
    )


def _backend_recovery_request_from_payload(
    payload: dict[str, Any],
    *,
    runner_id: str,
    slot_id: str | None,
    account_id: int | None,
    deployment_id: int | None,
) -> dict[str, Any] | None:
    if not isinstance(payload, dict) or not _looks_backend_controlled_recovery(payload):
        return None
    report = _payload_dict(payload.get("report"))
    request_deployment_id = _payload_int(
        report.get("deployment_id")
        or payload.get("deployment_id")
        or deployment_id
    )
    request_account_id = _payload_int(
        report.get("account_id")
        or payload.get("account_id")
        or account_id
    )
    request_slot_id = _canonical_slot_id(
        report.get("slot_id")
        or report.get("storage_slot_id")
        or payload.get("slot_id")
        or payload.get("storage_slot_id")
        or slot_id
    )
    request_runner_id = _payload_text(report.get("runner_id"), payload.get("runner_id"), runner_id)
    return {
        "deployment_id": request_deployment_id,
        "account_id": request_account_id,
        "runner_id": request_runner_id,
        "slot_id": request_slot_id,
        "reason": _backend_recovery_reason(payload, report),
        "report": report,
        "payload": payload,
        "current_slot_broken": _backend_recovery_current_slot_broken(payload),
    }


def _slot_inventory_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for key in ("slot_inventory", "slots", "slot_states", "runtime_slots"):
        raw = payload.get(key)
        if isinstance(raw, list):
            items.extend([dict(item) for item in raw if isinstance(item, dict)])
        elif isinstance(raw, dict):
            for slot_key, value in raw.items():
                if isinstance(value, dict):
                    item = dict(value)
                    item.setdefault("slot_id", slot_key)
                    items.append(item)
    return items


def _backend_recovery_requests_from_heartbeat(
    payload: dict[str, Any],
    *,
    runner_id: str,
    slot_id: str | None,
    account_id: int | None,
    deployment_id: int | None,
) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    direct = _backend_recovery_request_from_payload(
        payload,
        runner_id=runner_id,
        slot_id=slot_id,
        account_id=account_id,
        deployment_id=deployment_id,
    )
    if direct:
        requests.append(direct)
    for item in _slot_inventory_items(payload):
        metadata = _backend_recovery_metadata(item, _payload_dict(item.get("report")))
        merged = {**metadata, **item}
        request = _backend_recovery_request_from_payload(
            merged,
            runner_id=runner_id,
            slot_id=_canonical_slot_id(item.get("slot_id") or item.get("storage_slot_id")),
            account_id=_payload_int(item.get("account_id") or item.get("active_account_id")),
            deployment_id=_payload_int(item.get("deployment_id") or item.get("active_deployment_id")),
        )
        if request:
            requests.append(request)
    return requests


def _payload_confirms_runtime_healthy(payload: dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    report = _payload_dict(payload.get("report"))
    metadata = _backend_recovery_metadata(payload, report)
    states = {
        str(value or "").strip().lower()
        for value in (
            payload.get("status"),
            payload.get("state"),
            payload.get("slot_state"),
            payload.get("current_state"),
            payload.get("current_runner_state"),
            payload.get("runner_state"),
            payload.get("control_plane_state"),
            payload.get("health_status"),
            metadata.get("current_runner_state"),
            metadata.get("mt5_liveness_state"),
        )
        if str(value or "").strip()
    }
    if states.intersection(_BACKEND_RECOVERY_BROKEN_STATUSES):
        return False
    terminal_running = payload.get("terminal_running")
    worker_alive = payload.get("worker_alive")
    if worker_alive is None:
        for key in ("worker_pid", "runtime_worker_pid", "resident_worker_pid"):
            worker_pid = _payload_int(payload.get(key) or metadata.get(key))
            if worker_pid and worker_pid > 0:
                worker_alive = True
                break
    if terminal_running is None:
        terminal_pid = _payload_int(payload.get("terminal_pid") or metadata.get("terminal_pid"))
        if terminal_pid and terminal_pid > 0:
            terminal_running = True
    if terminal_running is None or worker_alive is None:
        return False
    liveness_good = _payload_truthy(terminal_running) and _payload_truthy(worker_alive)
    return liveness_good and bool(states.intersection(_BACKEND_RECOVERY_HEALTH_STATUSES))


def _slot_state_event_confirms_runtime_started(payload: dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    report = _payload_dict(payload.get("report"))
    metadata = _backend_recovery_metadata(payload, report)
    states = {
        str(value or "").strip().lower()
        for value in (
            payload.get("new_state"),
            payload.get("slot_state"),
            payload.get("to_state"),
            payload.get("current_state"),
            payload.get("current_runner_state"),
            payload.get("runner_state"),
            payload.get("status"),
            metadata.get("current_runner_state"),
            metadata.get("mt5_liveness_state"),
        )
        if str(value or "").strip()
    }
    if states.intersection(_BACKEND_RECOVERY_BROKEN_STATUSES):
        return False
    return bool(states.intersection(_BACKEND_RECOVERY_SLOT_RUNTIME_STARTED_STATUSES))


def _normalize_recovery_event(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    normalized = raw.replace("-", "_").replace(" ", "_").upper()
    return _RECOVERY_EVENT_ALIASES.get(normalized)


def _payload_recovery_event_type(*, event_type: str, payload: dict[str, Any], runtime_message: str = "") -> str | None:
    direct = _normalize_recovery_event(event_type)
    if direct:
        return direct
    for key in (
        "recovery_event",
        "recovery_status",
        "recovery_state",
        "recovery_phase",
        "runner_event_type",
        "event",
        "phase",
        "status",
        "reason",
    ):
        candidate = _normalize_recovery_event(payload.get(key))
        if candidate:
            return candidate
    text = f"{runtime_message} {_event_reason(payload)} {payload.get('message') or ''}".lower()
    if "mt5_recovery_budget_exhausted" in text or "recovery_budget_exhausted" in text:
        return EventType.MT5_RECOVERY_BUDGET_EXHAUSTED.value
    if "recovery_blocked" in text:
        return EventType.RECOVERY_BLOCKED.value
    if "recovery_completed" in text:
        return EventType.RECOVERY_COMPLETED.value
    if "recovery_started" in text:
        return EventType.RECOVERY_STARTED.value
    if "recovery_failed" in text:
        return EventType.RECOVERY_FAILED.value
    return None


def _runtime_log_terminal_event_type(payload: dict[str, Any], runtime_message: str) -> str | None:
    raw = str(
        payload.get("runner_event_type")
        or payload.get("runner_event_message")
        or runtime_message
        or ""
    ).strip().upper()
    if raw in {
        EventType.SLOT_TERMINAL_KILL_BEGIN.value,
        EventType.SLOT_TERMINAL_KILL_DONE.value,
    }:
        return raw
    return None


def _should_fallback_config_hot_update(reason: str) -> bool:
    normalized = str(reason or "").strip().lower()
    if not normalized:
        return False
    if normalized in _CONFIG_HOT_UPDATE_FALLBACK_REASONS:
        return True
    return any(fragment in normalized for fragment in _CONFIG_HOT_UPDATE_FALLBACK_REASON_FRAGMENTS)


def _payload_command_id(payload: dict[str, Any]) -> str | None:
    value = str(payload.get("command_id") or "").strip()
    return value or None


def _payload_truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "ok", "ready", "healthy", "verified"}


def _payload_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _payload_positive_int(value: Any, default: int = 300) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def apply_login_slot_final_event(
    repo: ControlPlaneRepository,
    *,
    event_type_value: str,
    account_id: int | None,
    command_id: str | None,
    runner_id: str,
    slot_id: str | None,
    payload_map: dict[str, Any],
) -> dict[str, Any]:
    """Project a final login-slot event into DB state.

    Used by both HTTP runner event ingest and Redis stream consumer so
    LOGIN_SLOT_* behavior stays identical across transports.
    """
    event_type_s = str(event_type_value or "").strip().upper()
    if event_type_s not in LOGIN_SLOT_FINAL_EVENT_TYPES:
        return {"handled": False}

    reservation_id = _payload_int(payload_map.get("login_reservation_id") or payload_map.get("reservation_id"))
    error_text = _event_reason(payload_map)

    if event_type_s == EventType.LOGIN_SLOT_RELEASED.value:
        released_count = 0
        if reservation_id is not None and account_id is not None and hasattr(repo, "release_login_reservation_by_id"):
            released_count = int(
                repo.release_login_reservation_by_id(
                    reservation_id=reservation_id,
                    account_id=int(account_id),
                    reason=error_text or "runner_login_slot_released",
                )
                or 0
            )
        elif account_id is not None:
            released_count = int(
                repo.release_login_reservation(
                    account_id=int(account_id),
                    reason=error_text or "runner_login_slot_released",
                )
                or 0
            )
        if command_id and released_count > 0:
            repo.update_execution_command_delivery(
                command_id=command_id,
                status="acknowledged",
                error_text=None,
                payload={"last_event_type": event_type_s, "runner_id": runner_id, "slot_id": slot_id},
            )
        return {
            "handled": True,
            "login_slot_released": released_count > 0,
            "released_count": released_count,
            "stale_ignored": released_count == 0,
        }

    ok = event_type_s == EventType.LOGIN_SLOT_VERIFIED.value
    result = repo.complete_login_reservation(
        reservation_id=reservation_id,
        command_id=command_id,
        ok=ok,
        runner_id=runner_id,
        slot_id=slot_id,
        error_text=None if ok else (error_text or "login_slot_failed"),
        payload=payload_map,
        ttl_sec=_payload_positive_int(
            payload_map.get("login_slot_ttl_sec") or payload_map.get("slot_ttl_sec") or payload_map.get("ttl_sec"),
            300,
        ),
    )
    if command_id:
        repo.update_execution_command_delivery(
            command_id=command_id,
            status="acknowledged" if ok else "failed",
            error_text=None if ok else (error_text or "login_slot_failed"),
            payload={"last_event_type": event_type_s, "runner_id": runner_id, "slot_id": slot_id},
        )
    return {"handled": True, "login_reservation": result, "ok": ok}


def _payload_login_slot_command_type(payload: dict[str, Any]) -> str:
    return str(
        payload.get("requested_cmd_type")
        or payload.get("command_type")
        or payload.get("cmd_type")
        or ""
    ).strip().upper()


def _start_bootstrap_failure_reason(payload: dict[str, Any]) -> str | None:
    candidates = (
        payload.get("exact_exception"),
        payload.get("message"),
        payload.get("log_message"),
        payload.get("reason"),
        payload.get("error"),
        payload.get("error_text"),
    )
    for candidate in candidates:
        value = str(candidate or "").strip()
        lowered = value.lower()
        if lowered.startswith("slot_bootstrap_failed:fatal_"):
            return value[:200]
        if lowered == "start_bot_command_bootstrap_failed":
            return str(payload.get("exact_exception") or payload.get("message") or value)[:200]
    return None


def _json_signature(value: Any) -> str:
    try:
        rendered = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        rendered = repr(value)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _heartbeat_state_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in payload.items():
        key_s = str(key or "").strip()
        if not key_s or key_s in _HEARTBEAT_VOLATILE_KEYS:
            continue
        if key_s in _HEARTBEAT_STATE_KEYS:
            out[key_s] = value
    return out


class RunnerEventIngestService:
    def __init__(self, repo: ControlPlaneRepository) -> None:
        self._repo = repo
        self._publisher = RedisStreamPublisher()
        self._command_router = CommandRouterService(repo)
        self._heartbeat_write_cache: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self._heartbeat_write_lock = Lock()
        self._backend_recovery_event_suppress_until: dict[tuple[str, str, str, str, str], float] = {}
        self._backend_recovery_event_suppress_lock = Lock()
        self._heartbeat_write_throttle_sec = max(
            0.0,
            float(getattr(settings, "RUNNER_HEARTBEAT_WRITE_THROTTLE_SEC", 5.0) or 5.0),
        )
        self._last_publish_warning_at = 0.0

    def _backend_recovery_event_suppression_ttl_sec(self) -> float:
        return max(
            0.0,
            float(getattr(settings, "RUNNER_BACKEND_RECOVERY_NOOP_EVENT_TTL_SEC", 120) or 120),
        )

    def _backend_recovery_event_key(self, recovery: dict[str, Any]) -> tuple[str, str, str, str, str]:
        return (
            str(recovery.get("runner_id") or "").strip(),
            str(_canonical_slot_id(recovery.get("slot_id")) or "").strip(),
            str(recovery.get("deployment_id") or "").strip(),
            str(recovery.get("account_id") or "").strip(),
            str(recovery.get("reason") or "").strip().lower(),
        )

    def _backend_recovery_event_is_suppressed(self, recovery: dict[str, Any]) -> bool:
        if self._backend_recovery_event_suppression_ttl_sec() <= 0:
            return False
        key = self._backend_recovery_event_key(recovery)
        now = time.monotonic()
        with self._backend_recovery_event_suppress_lock:
            until = float(self._backend_recovery_event_suppress_until.get(key) or 0.0)
            if until > now:
                return True
            if until:
                self._backend_recovery_event_suppress_until.pop(key, None)
            return False

    def _suppress_backend_recovery_event_if_noop(self, recovery: dict[str, Any], action: str | None) -> None:
        action_s = str(action or "").strip()
        if action_s not in _BACKEND_RECOVERY_EVENT_SUPPRESS_ACTIONS:
            return
        ttl_sec = self._backend_recovery_event_suppression_ttl_sec()
        if ttl_sec <= 0:
            return
        now = time.monotonic()
        key = self._backend_recovery_event_key(recovery)
        with self._backend_recovery_event_suppress_lock:
            if len(self._backend_recovery_event_suppress_until) > 1024:
                expired_keys = [
                    item_key
                    for item_key, until in self._backend_recovery_event_suppress_until.items()
                    if until <= now
                ]
                for item_key in expired_keys:
                    self._backend_recovery_event_suppress_until.pop(item_key, None)
            self._backend_recovery_event_suppress_until[key] = now + ttl_sec

    async def _publish_event_best_effort(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Publish to Redis stream without failing durable event ingest.

        Runner state is already stored in PostgreSQL before this call. Redis
        streams power live observers/replay telemetry, so a short Redis blip
        should not make Windows receive HTTP 503 and mark the runner unhealthy.
        """

        try:
            stream_id = await self._publisher.publish_event(payload)
            return {"published": True, "stream_id": stream_id}
        except Exception as exc:
            now = time.monotonic()
            if now - self._last_publish_warning_at >= 60.0:
                self._last_publish_warning_at = now
                _log.warning(
                    "runner_event.redis_publish_skipped event_type=%s runner=%s error=%s",
                    payload.get("event_type"),
                    payload.get("runner_id"),
                    exc,
                )
            return {
                "published": False,
                "stream_id": "",
                "warning": f"redis_publish_skipped:{exc.__class__.__name__}",
            }

    def _fail_start_deployment_after_bootstrap_failure(
        self,
        *,
        deployment_id: int | None,
        account_id: int | None,
        command_id: str | None,
        runner_id: str,
        slot_id: str | None,
        reason: str,
        trace_id: str | None,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        if deployment_id is None:
            return False

        deployment = self._repo.get_deployment(deployment_id=deployment_id)
        if _deployment_wants_stopped(deployment):
            return False

        command = self._repo.get_execution_command(command_id=command_id or "") if command_id else None
        command_type = str((command or {}).get("command_type") or "").strip().upper()
        deployment_status = str((deployment or {}).get("status") or "").strip().lower()
        start_context = command_type == "START_BOT" or deployment_status in {"start_requested", "starting"}
        if not start_context:
            return False

        if command_id and command_type in {"", "START_BOT"}:
            self._repo.update_execution_command_delivery(
                command_id=command_id,
                status="failed",
                error_text=reason,
                payload={
                    "last_event_type": event_type,
                    "runner_id": runner_id,
                    "slot_id": slot_id,
                    "failure_reason": reason,
                },
            )
        failed_account_id = account_id or (command or {}).get("account_id")
        if failed_account_id is not None and start_failure_is_credential_failure(reason=reason, payload=payload):
            self._repo.mark_account_runtime_login_result(
                account_id=int(failed_account_id),
                ok=False,
                error_text=reason,
            )

        self._repo.update_deployment_status(
            deployment_id=deployment_id,
            status=DeploymentStatus.FAILED.value,
            desired_state="stopped",
            is_active=False,
            health_status="bootstrap_failed",
            last_error=reason,
            stopped=True,
            runner_id=runner_id,
            slot_id=slot_id,
        )
        self._repo.release_deployment_slot(deployment_id=deployment_id, keep_sticky=False)
        insert_audit = getattr(self._repo, "insert_deployment_audit", None)
        if callable(insert_audit):
            insert_audit(
                deployment_id=deployment_id,
                action="deployment.start_failed",
                payload={
                    "deployment_id": deployment_id,
                    "account_id": account_id,
                    "command_id": command_id,
                    "runner_id": runner_id,
                    "slot_id": slot_id,
                    "reason": reason,
                },
                result="bootstrap_failed",
                trace_id=trace_id,
            )
        schedule_error_alert(
            area="Windows runner",
            summary="Bot không khởi động được trên runner.",
            severity="critical",
            account_id=account_id,
            deployment_id=deployment_id,
            runner_id=runner_id,
            slot_id=slot_id,
            impact="User có thể không bật được bot.",
            action="Kiểm tra log Windows runner, slot MT5 và command START_BOT.",
            detail={"reason": reason, "command_id": command_id, "event_type": event_type},
            alert_key=f"runner_start_bootstrap_failed:{deployment_id}:{runner_id}:{slot_id}",
            cooldown_sec=180,
        )
        return True

    def _heartbeat_cache_key(
        self,
        *,
        runner_id: str,
        slot_id: str | None,
        account_id: int | None,
        deployment_id: int | None,
    ) -> tuple[str, str, str, str]:
        return (
            str(runner_id or "").strip(),
            str(slot_id or "").strip(),
            str(account_id or ""),
            str(deployment_id or ""),
        )

    def _should_write_heartbeat(
        self,
        *,
        runner_id: str,
        slot_id: str | None,
        account_id: int | None,
        deployment_id: int | None,
        payload: dict[str, Any],
    ) -> bool:
        if self._heartbeat_write_throttle_sec <= 0:
            return True
        key = self._heartbeat_cache_key(
            runner_id=runner_id,
            slot_id=slot_id,
            account_id=account_id,
            deployment_id=deployment_id,
        )
        state_signature = _json_signature(
            {
                "runner_id": key[0],
                "slot_id": key[1],
                "account_id": key[2],
                "deployment_id": key[3],
                "state": _heartbeat_state_payload(payload),
            }
        )
        now = time.monotonic()
        with self._heartbeat_write_lock:
            previous = self._heartbeat_write_cache.get(key)
            if (
                previous
                and previous.get("state_signature") == state_signature
                and now - float(previous.get("written_at") or 0.0) < self._heartbeat_write_throttle_sec
            ):
                return False
            self._heartbeat_write_cache[key] = {
                "state_signature": state_signature,
                "written_at": now,
            }
            return True

    def _reconcile_runtime_slot(
        self,
        *,
        deployment_id: int | None,
        account_id: int | None,
        runner_id: str,
        slot_id: str | None,
    ) -> None:
        if deployment_id is None or not slot_id:
            return
        reconcile = getattr(self._repo, "reconcile_deployment_runtime_slot", None)
        if not callable(reconcile):
            return
        reconcile(
            deployment_id=deployment_id,
            account_id=account_id,
            runner_id=runner_id,
            slot_id=slot_id,
        )

    async def _finalize_stopped_deployment(
        self,
        *,
        event_model: RunnerEvent,
        command_id: str | None,
    ) -> None:
        if event_model.account_id is not None and login_lease.is_enabled():
            try:
                await login_lease.release_for_account(
                    account_id=int(event_model.account_id),
                    runner_id=event_model.runner_id,
                )
            except Exception:
                pass
        self._repo.update_deployment_status(
            deployment_id=event_model.deployment_id,
            status=DeploymentStatus.STOPPED.value,
            desired_state="stopped",
            is_active=False,
            health_status="stopped",
            stopped=True,
            runner_id=event_model.runner_id,
            slot_id=event_model.slot_id,
        )
        self._repo.release_deployment_slot(deployment_id=event_model.deployment_id, keep_sticky=False)
        self._repo.fail_pending_start_commands_for_deployment(
            deployment_id=event_model.deployment_id,
            reason="start_command_superseded_by_bot_stopped",
        )
        self._repo.reconcile_terminal_bot_control_commands(deployment_id=event_model.deployment_id)
        await self._restart_after_config_stop(
            deployment_id=event_model.deployment_id,
            command_id=command_id,
        )
        await self._start_queued_replacement_after_stop(
            previous_deployment_id=event_model.deployment_id,
            command_id=command_id,
        )

    async def _apply_terminal_kill_event(
        self,
        *,
        event_model: RunnerEvent,
        command_id: str | None,
        terminal_event_type: str,
    ) -> None:
        if event_model.deployment_id is None:
            return
        deployment = self._repo.get_deployment(deployment_id=event_model.deployment_id)
        if not _deployment_wants_stopped(deployment):
            return
        status = str((deployment or {}).get("status") or "").strip().lower()
        health_status = str((deployment or {}).get("health_status") or "").strip().lower()
        if status == DeploymentStatus.STOPPED.value and health_status == "stopped":
            return
        if terminal_event_type == EventType.SLOT_TERMINAL_KILL_DONE.value:
            if health_status == "terminal_cleanup_pending":
                await self._finalize_stopped_deployment(
                    event_model=event_model,
                    command_id=command_id,
                )
            else:
                self._repo.update_deployment_status(
                    deployment_id=event_model.deployment_id,
                    status=DeploymentStatus.STOP_REQUESTED.value,
                    desired_state="stopped",
                    is_active=True,
                    health_status="terminal_cleanup_done",
                    runner_id=event_model.runner_id,
                    slot_id=event_model.slot_id,
                )
        elif (
            terminal_event_type == EventType.SLOT_TERMINAL_KILL_BEGIN.value
            and health_status not in {"terminal_cleanup_pending", "terminal_cleanup_done"}
        ):
            self._repo.update_deployment_status(
                deployment_id=event_model.deployment_id,
                status=DeploymentStatus.STOP_REQUESTED.value,
                desired_state="stopped",
                is_active=True,
                health_status="terminal_cleanup_started",
                runner_id=event_model.runner_id,
                slot_id=event_model.slot_id,
            )

    def _apply_recovery_event(
        self,
        *,
        event_model: RunnerEvent,
        recovery_event_type: str,
        payload: dict[str, Any],
    ) -> None:
        if event_model.deployment_id is None:
            return
        deployment = self._repo.get_deployment(deployment_id=event_model.deployment_id)
        if not deployment:
            return
        wants_stopped = _deployment_wants_stopped(deployment)
        reason = _event_reason(payload) or recovery_event_type.lower()

        if recovery_event_type == EventType.RECOVERY_STARTED.value:
            if wants_stopped:
                return
            self._repo.update_deployment_status(
                deployment_id=event_model.deployment_id,
                status=DeploymentStatus.RUNNING.value,
                desired_state="running",
                is_active=True,
                health_status="recovering",
                last_error=reason,
                runner_id=event_model.runner_id,
                slot_id=event_model.slot_id,
            )
            if event_model.slot_id:
                self._repo.update_runner_slot_state(
                    runner_id=event_model.runner_id,
                    slot_id=event_model.slot_id,
                    status="allocated",
                    metadata={**payload, "recovery_status": "started", "available_for_new_account": False},
                )
            return

        if recovery_event_type == EventType.RECOVERY_COMPLETED.value:
            if wants_stopped:
                return
            self._repo.update_deployment_status(
                deployment_id=event_model.deployment_id,
                status=DeploymentStatus.RUNNING.value,
                desired_state="running",
                is_active=True,
                health_status="running",
                last_error=None,
                runner_id=event_model.runner_id,
                slot_id=event_model.slot_id,
            )
            if event_model.slot_id:
                self._repo.update_runner_slot_state(
                    runner_id=event_model.runner_id,
                    slot_id=event_model.slot_id,
                    status="allocated",
                    metadata={**payload, "recovery_status": "completed", "available_for_new_account": False},
                )
            return

        if recovery_event_type == EventType.RECOVERY_FAILED.value:
            if wants_stopped:
                return
            self._repo.update_deployment_status(
                deployment_id=event_model.deployment_id,
                status=DeploymentStatus.RUNNING.value,
                desired_state="running",
                is_active=True,
                health_status="recovery_failed",
                last_error=reason,
                runner_id=event_model.runner_id,
                slot_id=event_model.slot_id,
            )
            return

        if recovery_event_type == EventType.RECOVERY_BLOCKED.value:
            if wants_stopped:
                return
            self._repo.update_deployment_status(
                deployment_id=event_model.deployment_id,
                status=DeploymentStatus.FAILED.value,
                desired_state="stopped",
                is_active=False,
                health_status="recovery_blocked",
                last_error=reason,
                stopped=True,
                runner_id=event_model.runner_id,
                slot_id=event_model.slot_id,
            )
            return

        if recovery_event_type == EventType.MT5_RECOVERY_BUDGET_EXHAUSTED.value:
            self._repo.update_deployment_status(
                deployment_id=event_model.deployment_id,
                status=DeploymentStatus.FAILED.value,
                desired_state="stopped",
                is_active=False,
                health_status="mt5_recovery_budget_exhausted",
                last_error=reason,
                stopped=True,
                runner_id=event_model.runner_id,
                slot_id=event_model.slot_id,
            )
            if event_model.slot_id:
                self._repo.update_runner_slot_state(
                    runner_id=event_model.runner_id,
                    slot_id=event_model.slot_id,
                    status="degraded",
                    metadata={**payload, "recovery_status": "budget_exhausted", "last_error": reason},
                )
            schedule_error_alert(
                area="Windows runner recovery",
                summary="MT5 auto-recovery đã hết số lần thử.",
                severity="critical",
                account_id=event_model.account_id,
                deployment_id=event_model.deployment_id,
                runner_id=event_model.runner_id,
                slot_id=event_model.slot_id,
                impact="Bot đã dừng rõ trạng thái, cần operator hoặc user bật lại sau khi kiểm tra slot.",
                action="Kiểm tra Windows runner, terminal slot và log recovery.",
                detail={"reason": reason, "payload": payload},
                alert_key=f"mt5_recovery_budget_exhausted:{event_model.deployment_id}:{event_model.runner_id}:{event_model.slot_id}",
                cooldown_sec=300,
            )

    async def _fallback_config_hot_update_restart(
        self,
        *,
        deployment_id: int,
        command: dict[str, Any],
        reason: str,
        trace_id: str | None,
    ) -> None:
        if not _should_fallback_config_hot_update(reason):
            return
        deployment = self._repo.get_deployment(deployment_id=deployment_id)
        if not deployment:
            return
        if _deployment_wants_stopped(deployment):
            self._repo.insert_deployment_audit(
                deployment_id=deployment_id,
                action="deployment.config.hot_update_failed",
                payload={
                    "deployment_id": deployment_id,
                    "command_id": command.get("command_id"),
                    "reason": reason,
                },
                result="deployment_stopping_skip_restart",
                trace_id=trace_id,
            )
            return
        if str(deployment.get("status") or "").strip().lower() != "running":
            self._repo.insert_deployment_audit(
                deployment_id=deployment_id,
                action="deployment.config.hot_update_failed",
                payload={
                    "deployment_id": deployment_id,
                    "command_id": command.get("command_id"),
                    "reason": reason,
                    "deployment_status": deployment.get("status"),
                },
                result="restart_required_not_running",
                trace_id=trace_id,
            )
            return

        pending = self._repo.get_pending_account_start_stop_command(account_id=int(deployment["account_id"]))
        if pending:
            self._repo.insert_deployment_audit(
                deployment_id=deployment_id,
                action="deployment.config.hot_update_failed",
                payload={
                    "deployment_id": deployment_id,
                    "command_id": command.get("command_id"),
                    "pending_command_id": pending.get("command_id"),
                    "reason": reason,
                },
                result="restart_required_start_stop_pending",
                trace_id=trace_id,
            )
            return

        from app.orchestration.deployment_manager import DeploymentManagerService

        restart_trace_id = f"{trace_id or command.get('trace_id') or uuid.uuid4().hex}:restart"
        manager = DeploymentManagerService(self._repo, command_router=self._command_router)
        try:
            restart_result = await manager.request_config_restart(
                deployment=deployment,
                trace_id=restart_trace_id,
            )
            restart_command = restart_result.get("command") or {}
            self._repo.insert_deployment_audit(
                deployment_id=deployment_id,
                action="deployment.config.hot_update_fallback_restart_requested",
                payload={
                    "deployment_id": deployment_id,
                    "account_id": deployment.get("account_id"),
                    "failed_command_id": command.get("command_id"),
                    "restart_command_id": restart_command.get("command_id"),
                    "reason": reason,
                    "coalesced": bool(restart_result.get("coalesced")),
                },
                result="coalesced" if restart_result.get("coalesced") else "stop_queued",
                trace_id=restart_command.get("trace_id") or restart_trace_id,
            )
        except Exception as exc:
            self._repo.insert_deployment_audit(
                deployment_id=deployment_id,
                action="deployment.config.hot_update_fallback_restart_failed",
                payload={
                    "deployment_id": deployment_id,
                    "account_id": deployment.get("account_id"),
                    "failed_command_id": command.get("command_id"),
                    "reason": reason,
                    "restart_error": str(exc)[:200],
                },
                result="enqueue_failed",
                trace_id=restart_trace_id,
            )
            schedule_error_alert(
                area="Cập nhật cấu hình bot",
                summary="Không tự restart được bot sau khi hot-update config lỗi.",
                exc=exc,
                deployment_id=deployment_id,
                account_id=deployment.get("account_id"),
                impact="Thay đổi cấu hình có thể chưa có hiệu lực.",
                action="Kiểm tra lệnh UPDATE_BOT_CONFIG và trạng thái deployment.",
                detail={"reason": reason, "failed_command_id": command.get("command_id")},
                alert_key=f"config_hot_update_fallback_restart_failed:{deployment_id}:{exc.__class__.__name__}",
                cooldown_sec=180,
            )

    def _build_restart_start_payload(
        self,
        *,
        deployment: dict[str, Any],
        bot: dict[str, Any],
        stop_command: dict[str, Any],
    ) -> dict[str, Any]:
        deployment_config = normalize_deployment_config(
            bot=bot,
            config=deployment.get("config_json") or {},
        )
        return {
            "account_id": int(deployment["account_id"]),
            "mode": str(deployment.get("mode") or "live").strip().lower() or "live",
            "broker": deployment.get("broker"),
            "server": deployment.get("server"),
            "login": deployment.get("login"),
            "bot_name": bot.get("bot_name") or deployment.get("bot_name"),
            "bot_version": bot.get("version") or "",
            "runtime_entry": bot.get("runtime_entry") or "",
            "profile_class": bot.get("profile_class") or deployment.get("profile_class") or "",
            "resource_hints": bot.get("resource_hints") or {},
            "config_contract_version": TRADING_CONFIG_SCHEMA_VERSION,
            "config": deployment_config,
            "sticky_reused": True,
            "control_flow": "deployment_config_restart",
            "restart_policy": "stop_then_start_same_deployment",
            "config_restart_stop_command_id": stop_command.get("command_id"),
            "config_update_trace_id": (stop_command.get("payload_json") or {}).get("config_update_trace_id"),
        }

    def _build_backend_recovery_start_payload(
        self,
        *,
        deployment: dict[str, Any],
        bot: dict[str, Any],
        recovery: dict[str, Any],
        attempt_count: int,
        recovery_key: str,
    ) -> dict[str, Any]:
        deployment_config = normalize_deployment_config(
            bot=bot,
            config=deployment.get("config_json") or {},
        )
        reason = str(recovery.get("reason") or _BACKEND_RECOVERY_STATUS).strip().lower()
        return {
            "account_id": int(deployment["account_id"]),
            "mode": str(deployment.get("mode") or "live").strip().lower() or "live",
            "broker": deployment.get("broker"),
            "server": deployment.get("server"),
            "login": deployment.get("login"),
            "bot_code": bot.get("bot_code") or bot.get("bot_id") or deployment.get("bot_code"),
            "bot_name": bot.get("bot_name") or deployment.get("bot_name"),
            "bot_version": bot.get("version") or "",
            "runtime_entry": bot.get("runtime_entry") or "",
            "profile_class": bot.get("profile_class") or deployment.get("profile_class") or "",
            "resource_hints": bot.get("resource_hints") or {},
            "config_contract_version": TRADING_CONFIG_SCHEMA_VERSION,
            "config": deployment_config,
            "sticky_reused": True,
            "control_flow": "backend_runner_recovery",
            "restart_policy": "backend_controlled_same_deployment",
            "backend_runner_recovery": True,
            "backend_recovery_key": recovery_key,
            "backend_recovery_reason": reason,
            "backend_recovery_attempt": int(attempt_count or 1),
            "backend_recovery_from_runner_id": recovery.get("runner_id"),
            "backend_recovery_from_slot_id": recovery.get("slot_id"),
            "backend_recovery_report": recovery.get("report") or {},
            "intent_seq": int(deployment.get("intent_seq") or 0),
        }

    def _backend_recovery_cooldown_sec(self) -> int:
        return max(30, int(getattr(settings, "RUNNER_BACKEND_RECOVERY_COOLDOWN_SEC", 120) or 120))

    def _backend_recovery_budget_count(self) -> int:
        return max(1, int(getattr(settings, "RUNNER_BACKEND_RECOVERY_BUDGET_COUNT", 3) or 3))

    def _backend_recovery_budget_window_sec(self) -> int:
        return max(
            self._backend_recovery_cooldown_sec(),
            int(getattr(settings, "RUNNER_BACKEND_RECOVERY_BUDGET_WINDOW_SEC", 900) or 900),
        )

    def _maybe_reassign_backend_recovery_slot(
        self,
        *,
        deployment: dict[str, Any],
        runner_id: str,
        slot_id: str,
        reason: str,
        trace_id: str | None,
        force_current_broken: bool = False,
    ) -> dict[str, Any]:
        list_slots = getattr(self._repo, "list_slots", None)
        allocate_slot = getattr(self._repo, "allocate_slot_binding", None)
        if not callable(list_slots) or not callable(allocate_slot):
            return {"runner_id": runner_id, "slot_id": slot_id, "changed": False, "reason": "slot_reassign_unsupported"}
        try:
            slots = list_slots()
        except Exception as exc:
            return {
                "runner_id": runner_id,
                "slot_id": slot_id,
                "changed": False,
                "reason": f"slot_list_failed:{exc.__class__.__name__}",
            }

        runner_id_s = str(runner_id or "").strip()
        slot_id_s = _canonical_slot_id(slot_id)
        account_id = int(deployment.get("account_id") or 0)
        deployment_id = int(deployment.get("id") or 0)
        current_slot: dict[str, Any] | None = None
        candidates: list[dict[str, Any]] = []
        for raw in slots or []:
            if not isinstance(raw, dict):
                continue
            raw_runner_id = str(raw.get("runner_id") or "").strip()
            raw_slot_id = _canonical_slot_id(raw.get("slot_id"))
            if raw_runner_id != runner_id_s:
                continue
            if raw_slot_id == slot_id_s:
                current_slot = raw
                continue
            raw_status = str(raw.get("status") or "").strip().lower()
            runner_status = str(raw.get("runner_status") or "").strip().lower()
            current_account_id = raw.get("current_account_id")
            active_deployment_id = raw.get("active_deployment_id")
            current_account_matches = (
                current_account_id in (None, "")
                or (account_id > 0 and str(current_account_id) == str(account_id))
            )
            active_deployment_clear = (
                active_deployment_id in (None, "")
                or (deployment_id > 0 and str(active_deployment_id) == str(deployment_id))
            )
            if (
                raw_status in {"ready", "allocated"}
                and runner_status in {"", "online"}
                and current_account_matches
                and active_deployment_clear
            ):
                candidates.append(raw)

        current_status = str((current_slot or {}).get("status") or "").strip().lower()
        current_metadata = _payload_dict((current_slot or {}).get("metadata_json") or (current_slot or {}).get("metadata"))
        current_runner_state = str(
            current_metadata.get("current_runner_state")
            or current_metadata.get("runner_state")
            or ""
        ).strip().lower()
        current_mt5_state = str(current_metadata.get("mt5_liveness_state") or "").strip().lower()
        current_is_broken = bool(force_current_broken) or (
            current_status in {"broken", "degraded"}
            or current_runner_state in {"broken", "degraded"}
            or current_mt5_state in {"broken", "dead", "failed", "offline", "stale"}
        )
        if not current_is_broken or not candidates:
            return {
                "runner_id": runner_id_s,
                "slot_id": slot_id_s,
                "changed": False,
                "reason": "current_slot_reusable" if not current_is_broken else "no_ready_slot",
            }

        if account_id <= 0 or deployment_id <= 0:
            return {"runner_id": runner_id_s, "slot_id": slot_id_s, "changed": False, "reason": "identity_missing"}
        last_error = "no_ready_slot"
        for candidate in sorted(
            candidates,
            key=lambda item: (
                0 if str(item.get("status") or "").strip().lower() == "ready" else 1,
                _canonical_slot_id(item.get("slot_id")) or "",
            ),
        ):
            next_slot_id = _canonical_slot_id(candidate.get("slot_id"))
            if not next_slot_id:
                continue
            try:
                binding = allocate_slot(
                    account_id=account_id,
                    runner_id=runner_id_s,
                    slot_id=next_slot_id,
                    sticky=True,
                )
                self._repo.update_deployment_status(
                    deployment_id=deployment_id,
                    status=DeploymentStatus.RUNNING.value,
                    desired_state="running",
                    is_active=True,
                    health_status="runner_recovery_pending",
                    runner_id=runner_id_s,
                    slot_id=next_slot_id,
                )
                self._repo.insert_deployment_audit(
                    deployment_id=deployment_id,
                    action="deployment.backend_runner_recovery_slot_reassigned",
                    payload={
                        "deployment_id": deployment_id,
                        "account_id": account_id,
                        "from_runner_id": runner_id_s,
                        "from_slot_id": slot_id_s,
                        "to_runner_id": runner_id_s,
                        "to_slot_id": next_slot_id,
                        "reason": reason,
                        "binding": binding or {},
                    },
                    result="slot_reassigned",
                    trace_id=trace_id,
                )
                return {
                    "runner_id": runner_id_s,
                    "slot_id": next_slot_id,
                    "changed": True,
                    "reason": "current_slot_broken",
                    "from_slot_id": slot_id_s,
                }
            except Exception as exc:
                last_error = f"slot_reassign_failed:{exc.__class__.__name__}"
                continue
        return {
            "runner_id": runner_id_s,
            "slot_id": slot_id_s,
            "changed": False,
            "reason": last_error,
        }

    def _log_backend_recovery_decision(
        self,
        *,
        recovery: dict[str, Any],
        action: str,
        claim: dict[str, Any] | None = None,
        command_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        claim_map = claim or {}
        action_s = str(action or "ignored").strip()
        _log.info(
            "runner.backend_recovery.decision deployment=%s account=%s runner=%s slot=%s reason=%s action=%s command_id=%s attempt=%s cooldown_until=%s",
            recovery.get("deployment_id"),
            recovery.get("account_id"),
            recovery.get("runner_id"),
            recovery.get("slot_id"),
            recovery.get("reason"),
            action_s,
            command_id or "",
            claim_map.get("attempt_count"),
            claim_map.get("cooldown_until") or claim_map.get("previous_cooldown_until"),
            extra={
                "event": "runner.backend_recovery.decision",
                "deployment_id": recovery.get("deployment_id"),
                "account_id": recovery.get("account_id"),
                "runner_id": recovery.get("runner_id"),
                "slot_id": recovery.get("slot_id"),
                "reason": recovery.get("reason"),
                "action": action_s,
                "command_id": command_id,
                "attempt_count": claim_map.get("attempt_count"),
                "cooldown_until": claim_map.get("cooldown_until") or claim_map.get("previous_cooldown_until"),
                "desired_state": claim_map.get("desired_state"),
                "deployment_status": claim_map.get("status"),
                "runtime_status": claim_map.get("health_status"),
                "latest_active_deployment_id": claim_map.get("latest_active_deployment_id"),
                "detail": detail or {},
            },
        )

    async def _handle_backend_runner_recovery_request(
        self,
        *,
        recovery: dict[str, Any],
        source: str,
        trace_id: str | None,
    ) -> dict[str, Any]:
        deployment_id = recovery.get("deployment_id")
        account_id = recovery.get("account_id")
        runner_id = str(recovery.get("runner_id") or "").strip()
        slot_id = _canonical_slot_id(recovery.get("slot_id"))
        reason = str(recovery.get("reason") or _BACKEND_RECOVERY_STATUS).strip().lower()
        recovery = {
            **recovery,
            "deployment_id": deployment_id,
            "account_id": account_id,
            "runner_id": runner_id,
            "slot_id": slot_id,
            "reason": reason,
        }
        if deployment_id is None or account_id is None or not runner_id or not slot_id:
            self._log_backend_recovery_decision(
                recovery=recovery,
                action="ignored_missing_identity",
                detail={"source": source},
            )
            return {"handled": False, "action": "ignored_missing_identity"}

        claim_fn = getattr(self._repo, "claim_backend_runner_recovery", None)
        if not callable(claim_fn):
            self._log_backend_recovery_decision(
                recovery=recovery,
                action="ignored_repo_unsupported",
                detail={"source": source},
            )
            return {"handled": False, "action": "ignored_repo_unsupported"}

        claim = claim_fn(
            deployment_id=int(deployment_id),
            account_id=int(account_id),
            runner_id=runner_id,
            slot_id=slot_id,
            reason=reason,
            cooldown_sec=self._backend_recovery_cooldown_sec(),
            budget_count=self._backend_recovery_budget_count(),
            budget_window_sec=self._backend_recovery_budget_window_sec(),
        )
        claim_action = str((claim or {}).get("action") or "ignored").strip()
        if claim_action == "budget_exhausted":
            deployment = self._repo.get_deployment(deployment_id=int(deployment_id))
            if deployment and not _deployment_wants_stopped(deployment):
                slot_reassign = self._maybe_reassign_backend_recovery_slot(
                    deployment=deployment,
                    runner_id=runner_id,
                    slot_id=slot_id,
                    reason=reason,
                    trace_id=trace_id,
                    force_current_broken=True,
                )
                if bool(slot_reassign.get("changed")):
                    runner_id = str(slot_reassign.get("runner_id") or runner_id).strip()
                    slot_id = _canonical_slot_id(slot_reassign.get("slot_id") or slot_id)
                    recovery = {
                        **recovery,
                        "runner_id": runner_id,
                        "slot_id": slot_id,
                        "slot_reassign": slot_reassign,
                    }
                    clear_fn = getattr(self._repo, "clear_backend_runner_recovery", None)
                    if callable(clear_fn):
                        clear_fn(
                            deployment_id=int(deployment_id),
                            runner_id=runner_id,
                            slot_id=slot_id,
                            reason="slot_reassigned_after_budget_exhausted",
                        )
                    claim = claim_fn(
                        deployment_id=int(deployment_id),
                        account_id=int(account_id),
                        runner_id=runner_id,
                        slot_id=slot_id,
                        reason=reason,
                        cooldown_sec=self._backend_recovery_cooldown_sec(),
                        budget_count=self._backend_recovery_budget_count(),
                        budget_window_sec=self._backend_recovery_budget_window_sec(),
                    )
                    claim_action = str((claim or {}).get("action") or "ignored").strip()
        if claim_action != "claim":
            log_action = {
                "cooldown": "suppressed_cooldown",
                "recovery_in_flight": "suppressed_in_flight",
                "start_stop_in_flight": "suppressed_in_flight",
                "budget_exhausted": "budget_exhausted",
                "deployment_not_running": "ignored_not_running",
                "newer_active_deployment": "ignored_newer_active_deployment",
                "guard_blocked": "ignored_guard_blocked",
            }.get(claim_action, f"ignored_{claim_action}")
            self._log_backend_recovery_decision(
                recovery=recovery,
                action=log_action,
                claim=claim,
                detail={"source": source},
            )
            if claim_action == "budget_exhausted":
                schedule_error_alert(
                    area="Windows runner recovery",
                    summary="Backend recovery đã hết retry budget.",
                    severity="critical",
                    account_id=int(account_id),
                    deployment_id=int(deployment_id),
                    runner_id=runner_id,
                    slot_id=slot_id,
                    impact="Bot vẫn được giữ desired_state=running nhưng cần operator kiểm tra runner/MT5.",
                    action="Kiểm tra Windows slot, terminal, worker và command START_BOT gần nhất.",
                    detail={"reason": reason, "source": source, "claim": claim},
                    alert_key=f"backend_runner_recovery_budget:{deployment_id}:{runner_id}:{slot_id}:{reason}",
                    cooldown_sec=300,
                )
            return {"handled": True, "action": log_action, "claim": claim}

        deployment = self._repo.get_deployment(deployment_id=int(deployment_id))
        if not deployment:
            self._log_backend_recovery_decision(
                recovery=recovery,
                action="ignored_deployment_not_found_after_claim",
                claim=claim,
                detail={"source": source},
            )
            return {"handled": True, "action": "ignored_deployment_not_found_after_claim", "claim": claim}
        if _deployment_wants_stopped(deployment):
            mark_failed = getattr(self._repo, "mark_backend_runner_recovery_dispatch_failed", None)
            if callable(mark_failed):
                mark_failed(deployment_id=int(deployment_id), reason="deployment_stopped_after_claim")
            self._log_backend_recovery_decision(
                recovery=recovery,
                action="ignored_not_running_after_claim",
                claim=claim,
                detail={"source": source},
            )
            return {"handled": True, "action": "ignored_not_running_after_claim", "claim": claim}

        package_fn = getattr(self._repo, "get_runner_deployment_package", None)
        if callable(package_fn):
            package = package_fn(deployment_id=int(deployment_id))
            account_status = str((package or {}).get("account_status") or "").strip().lower()
            if account_status and account_status != "connected":
                mark_failed = getattr(self._repo, "mark_backend_runner_recovery_dispatch_failed", None)
                if callable(mark_failed):
                    mark_failed(deployment_id=int(deployment_id), reason=f"account_not_ready:{account_status}")
                self._log_backend_recovery_decision(
                    recovery=recovery,
                    action="ignored_account_guard",
                    claim=claim,
                    detail={"source": source, "account_status": account_status},
                )
                return {"handled": True, "action": "ignored_account_guard", "claim": claim}

        bot_name = str(deployment.get("bot_code") or deployment.get("bot_name") or "").strip()
        bot = self._repo.get_bot_by_name(bot_name=bot_name) if bot_name and hasattr(self._repo, "get_bot_by_name") else None
        bot = bot or {
            "bot_code": deployment.get("bot_code"),
            "bot_name": deployment.get("bot_name"),
            "profile_class": deployment.get("profile_class"),
            "resource_hints": {},
        }
        if bot.get("enabled") is False:
            mark_failed = getattr(self._repo, "mark_backend_runner_recovery_dispatch_failed", None)
            if callable(mark_failed):
                mark_failed(deployment_id=int(deployment_id), reason="bot_disabled")
            self._log_backend_recovery_decision(
                recovery=recovery,
                action="ignored_bot_guard",
                claim=claim,
                detail={"source": source, "bot_code": bot.get("bot_code") or bot_name},
            )
            return {"handled": True, "action": "ignored_bot_guard", "claim": claim}
        if not bot_start_runtime_supported(bot, settings_obj=settings):
            disabled_reason = bot_start_runtime_disabled_reason(bot, settings_obj=settings) or "bot_runtime_not_supported"
            mark_failed = getattr(self._repo, "mark_backend_runner_recovery_dispatch_failed", None)
            if callable(mark_failed):
                mark_failed(deployment_id=int(deployment_id), reason=disabled_reason)
            self._log_backend_recovery_decision(
                recovery=recovery,
                action="ignored_bot_runtime_guard",
                claim=claim,
                detail={"source": source, "bot_code": bot.get("bot_code") or bot_name, "reason": disabled_reason},
            )
            return {"handled": True, "action": "ignored_bot_runtime_guard", "claim": claim}
        slot_reassign = self._maybe_reassign_backend_recovery_slot(
            deployment=deployment,
            runner_id=runner_id,
            slot_id=slot_id,
            reason=reason,
            trace_id=trace_id,
            force_current_broken=bool(recovery.get("current_slot_broken")),
        )
        runner_id = str(slot_reassign.get("runner_id") or runner_id).strip()
        slot_id = _canonical_slot_id(slot_reassign.get("slot_id") or slot_id)
        recovery = {
            **recovery,
            "runner_id": runner_id,
            "slot_id": slot_id,
            "slot_reassign": slot_reassign,
        }
        attempt_count = int((claim or {}).get("attempt_count") or 1)
        recovery_key = f"mt5_recovery:{deployment_id}:{runner_id}:{slot_id}:{reason}"
        start_trace_id = f"{recovery_key}:{attempt_count}:{uuid.uuid4().hex[:8]}"
        if trace_id:
            start_trace_id = f"{trace_id}:{start_trace_id}"
        payload = self._build_backend_recovery_start_payload(
            deployment=deployment,
            bot=bot,
            recovery=recovery,
            attempt_count=attempt_count,
            recovery_key=recovery_key,
        )
        try:
            command = await self._command_router.dispatch(
                command_type=CommandType.START_BOT,
                account_id=int(account_id),
                deployment_id=int(deployment_id),
                bot_id=str(bot.get("bot_code") or bot.get("bot_id") or deployment.get("bot_code") or ""),
                runner_id=runner_id,
                slot_id=slot_id,
                priority=95,
                payload=payload,
                trace_id=start_trace_id,
            )
        except Exception as exc:
            mark_failed = getattr(self._repo, "mark_backend_runner_recovery_dispatch_failed", None)
            if callable(mark_failed):
                mark_failed(deployment_id=int(deployment_id), reason=f"dispatch_failed:{exc.__class__.__name__}")
            self._log_backend_recovery_decision(
                recovery=recovery,
                action="dispatch_failed",
                claim=claim,
                detail={"source": source, "error": str(exc)[:200], "slot_reassign": slot_reassign},
            )
            schedule_error_alert(
                area="Windows runner recovery",
                summary="Backend không enqueue được START_BOT để recovery.",
                exc=exc,
                severity="critical",
                account_id=int(account_id),
                deployment_id=int(deployment_id),
                runner_id=runner_id,
                slot_id=slot_id,
                impact="Bot có thể đang đứng ở trạng thái recovery pending.",
                action="Kiểm tra Redis command queue và CommandRouterService.",
                detail={"reason": reason, "source": source},
                alert_key=f"backend_runner_recovery_dispatch_failed:{deployment_id}:{exc.__class__.__name__}",
                cooldown_sec=180,
            )
            return {"handled": True, "action": "dispatch_failed", "claim": claim}

        command_id = str(command.get("command_id") or "").strip()
        if str(command.get("delivery_status") or "").strip().lower() == "failed":
            mark_failed = getattr(self._repo, "mark_backend_runner_recovery_dispatch_failed", None)
            if callable(mark_failed):
                mark_failed(
                    deployment_id=int(deployment_id),
                    reason=str(command.get("drop_reason") or "start_bot_dispatch_dropped")[:200],
                )
            self._log_backend_recovery_decision(
                recovery=recovery,
                action="dispatch_dropped",
                claim=claim,
                command_id=command_id,
                detail={"source": source, "drop_reason": command.get("drop_reason"), "slot_reassign": slot_reassign},
            )
            return {"handled": True, "action": "dispatch_dropped", "claim": claim, "command": command}

        mark_command = getattr(self._repo, "mark_backend_runner_recovery_command", None)
        if callable(mark_command) and command_id:
            mark_command(deployment_id=int(deployment_id), command_id=command_id)
        insert_audit = getattr(self._repo, "insert_deployment_audit", None)
        if callable(insert_audit):
            insert_audit(
                deployment_id=int(deployment_id),
                action="deployment.backend_runner_recovery_requested",
                payload={
                    "deployment_id": int(deployment_id),
                    "account_id": int(account_id),
                    "runner_id": runner_id,
                    "slot_id": slot_id,
                    "reason": reason,
                    "source": source,
                    "command_id": command_id,
                    "attempt_count": attempt_count,
                    "recovery_key": recovery_key,
                },
                result="start_queued",
                trace_id=command.get("trace_id") or start_trace_id,
            )
        self._log_backend_recovery_decision(
            recovery=recovery,
            action="queued_restart",
            claim=claim,
            command_id=command_id,
            detail={"source": source, "slot_reassign": slot_reassign},
        )
        return {
            "handled": True,
            "action": "queued_restart",
            "claim": claim,
            "command": command,
            "command_id": command_id,
        }

    def _clear_backend_runner_recovery_if_healthy(
        self,
        *,
        deployment_id: int | None,
        runner_id: str,
        slot_id: str | None,
        payload: dict[str, Any],
        source: str,
    ) -> None:
        if deployment_id is None or not _payload_confirms_runtime_healthy(payload):
            return
        clear_fn = getattr(self._repo, "clear_backend_runner_recovery", None)
        if not callable(clear_fn):
            return
        cleared = clear_fn(
            deployment_id=int(deployment_id),
            runner_id=runner_id,
            slot_id=slot_id,
            reason=f"{source}_healthy",
        )
        if cleared:
            _log.info(
                "runner.backend_recovery.cleared deployment=%s runner=%s slot=%s source=%s",
                deployment_id,
                runner_id,
                slot_id or "",
                source,
                extra={
                    "event": "runner.backend_recovery.cleared",
                    "deployment_id": deployment_id,
                    "runner_id": runner_id,
                    "slot_id": slot_id,
                    "source": source,
                },
            )

    async def _restart_after_config_stop(
        self,
        *,
        deployment_id: int,
        command_id: str | None,
    ) -> None:
        get_stop_command = getattr(self._repo, "get_config_restart_stop_command_for_start", None)
        if not callable(get_stop_command):
            return
        stop_command = get_stop_command(
            deployment_id=deployment_id,
            command_id=command_id,
        )
        if not stop_command:
            return
        deployment = self._repo.get_deployment(deployment_id=deployment_id)
        if not deployment:
            self._repo.insert_deployment_audit(
                deployment_id=deployment_id,
                action="deployment.config.restart_failed",
                payload={
                    "deployment_id": deployment_id,
                    "command_id": stop_command.get("command_id"),
                    "reason": "deployment_not_found_after_stop",
                },
                result="deployment_not_found",
                trace_id=stop_command.get("trace_id"),
            )
            return

        runner_id = str(deployment.get("runner_id") or stop_command.get("runner_id") or "").strip()
        slot_id = _canonical_slot_id(deployment.get("slot_id") or stop_command.get("slot_id"))
        if not runner_id or not slot_id:
            self._repo.insert_deployment_audit(
                deployment_id=deployment_id,
                action="deployment.config.restart_failed",
                payload={
                    "deployment_id": deployment_id,
                    "command_id": stop_command.get("command_id"),
                    "reason": "runtime_binding_missing_after_stop",
                },
                result="runtime_binding_missing",
                trace_id=stop_command.get("trace_id"),
            )
            return

        try:
            binding = self._repo.allocate_slot_binding(
                account_id=int(deployment["account_id"]),
                runner_id=runner_id,
                slot_id=slot_id,
                sticky=True,
            )
            start_trace_id = f"{stop_command.get('trace_id') or uuid.uuid4().hex}:start"
            updated = self._repo.update_deployment_status(
                deployment_id=deployment_id,
                status=DeploymentStatus.START_REQUESTED.value,
                desired_state="running",
                is_active=True,
                health_status="starting",
                runner_id=runner_id,
                slot_id=slot_id,
            )
            restart_deployment = {**deployment, **(updated or {})}
            bot_name = str(deployment.get("bot_code") or deployment.get("bot_name") or "").strip()
            bot = self._repo.get_bot_by_name(bot_name=bot_name) or {
                "bot_code": deployment.get("bot_code"),
                "bot_name": deployment.get("bot_name"),
                "profile_class": deployment.get("profile_class"),
                "resource_hints": {},
            }
            payload = self._build_restart_start_payload(
                deployment=restart_deployment,
                bot=bot,
                stop_command=stop_command,
            )
            command = await self._command_router.dispatch(
                command_type=CommandType.START_BOT,
                account_id=int(deployment["account_id"]),
                deployment_id=deployment_id,
                bot_id=str(bot.get("bot_code") or bot.get("bot_id") or deployment.get("bot_code") or ""),
                runner_id=runner_id,
                slot_id=slot_id,
                priority=95,
                payload=payload,
                trace_id=start_trace_id,
            )
            self._repo.insert_deployment_audit(
                deployment_id=deployment_id,
                action="deployment.config.restart_requested",
                payload={
                    "deployment_id": deployment_id,
                    "account_id": deployment.get("account_id"),
                    "stop_command_id": stop_command.get("command_id"),
                    "start_command_id": command.get("command_id"),
                    "trace_id": command.get("trace_id") or start_trace_id,
                    "runner_id": runner_id,
                    "slot_id": slot_id,
                    "binding_id": binding.get("id") if isinstance(binding, dict) else None,
                },
                result="start_queued",
                trace_id=command.get("trace_id") or start_trace_id,
            )
        except Exception as exc:
            try:
                self._repo.update_deployment_status(
                    deployment_id=deployment_id,
                    status=DeploymentStatus.FAILED.value,
                    desired_state="stopped",
                    is_active=False,
                    health_status="config_restart_failed",
                    last_error="config_restart_start_enqueue_failed",
                    stopped=True,
                    runner_id=runner_id,
                    slot_id=slot_id,
                )
            except Exception:
                pass
            self._repo.insert_deployment_audit(
                deployment_id=deployment_id,
                action="deployment.config.restart_failed",
                payload={
                    "deployment_id": deployment_id,
                    "command_id": stop_command.get("command_id"),
                    "reason": str(exc)[:200],
                },
                result="start_enqueue_failed",
                trace_id=stop_command.get("trace_id"),
            )
            schedule_error_alert(
                area="Restart bot",
                summary="Backend không gửi được lệnh START sau khi STOP để restart config.",
                exc=exc,
                deployment_id=deployment_id,
                account_id=deployment.get("account_id"),
                runner_id=runner_id,
                slot_id=slot_id,
                impact="Bot có thể đang dừng sau khi đổi cấu hình.",
                action="Kiểm tra command queue và trạng thái deployment.",
                detail={"stop_command_id": stop_command.get("command_id")},
                alert_key=f"config_restart_start_enqueue_failed:{deployment_id}:{exc.__class__.__name__}",
                cooldown_sec=180,
            )
            return

    async def _start_queued_replacement_after_stop(
        self,
        *,
        previous_deployment_id: int,
        command_id: str | None,
    ) -> None:
        get_stop_command = getattr(self._repo, "get_replacement_stop_command_for_start", None)
        if not callable(get_stop_command):
            return
        stop_command = get_stop_command(
            previous_deployment_id=previous_deployment_id,
            command_id=command_id,
        )
        if not stop_command:
            return

        try:
            replacement_deployment_id = int((stop_command.get("payload_json") or {}).get("replacement_deployment_id") or 0)
        except Exception:
            replacement_deployment_id = 0
        if replacement_deployment_id <= 0:
            self._repo.insert_deployment_audit(
                deployment_id=previous_deployment_id,
                action="deployment.replacement_start_failed",
                payload={
                    "previous_deployment_id": previous_deployment_id,
                    "command_id": stop_command.get("command_id"),
                    "reason": "replacement_deployment_id_missing",
                },
                result="invalid_stop_payload",
                trace_id=stop_command.get("trace_id"),
            )
            return

        # Latest-intent guard: the queued replacement may have been cancelled
        # by a later user OFF press. Re-read state and skip the START_BOT if
        # the user is no longer asking for a running bot on this account.
        intent_state = self._repo.get_deployment_intent_state(deployment_id=replacement_deployment_id)
        replacement_desired = str((intent_state or {}).get("desired_state") or "").strip().lower()
        replacement_status = str((intent_state or {}).get("status") or "").strip().lower()
        if not intent_state or replacement_desired != "running" or replacement_status != "queued":
            drop_reason = (
                "replacement_missing"
                if not intent_state
                else "desired_state_stopped"
                if replacement_desired != "running"
                else "replacement_not_queued"
            )
            self._repo.insert_deployment_audit(
                deployment_id=replacement_deployment_id,
                action="deployment.replacement_start_dropped",
                payload={
                    "deployment_id": replacement_deployment_id,
                    "previous_deployment_id": previous_deployment_id,
                    "stop_command_id": stop_command.get("command_id"),
                    "drop_reason": drop_reason,
                    "desired_state": replacement_desired or None,
                    "deployment_status": replacement_status or None,
                },
                result="dropped",
                trace_id=stop_command.get("trace_id"),
            )
            _log.info(
                "runner.command.dispatch.dropped previous_deployment_id=%s replacement_deployment_id=%s drop_reason=%s desired_state=%s status=%s stop_command_id=%s",
                previous_deployment_id,
                replacement_deployment_id,
                drop_reason,
                replacement_desired or "",
                replacement_status or "",
                stop_command.get("command_id"),
                extra={
                    "event": "runner.command.dispatch.dropped",
                    "dispatch_decision": "dropped",
                    "drop_reason": drop_reason,
                    "command_type": "START_BOT",
                    "account_id": stop_command.get("account_id"),
                    "deployment_id": replacement_deployment_id,
                    "previous_deployment_id": previous_deployment_id,
                    "trace_id": stop_command.get("trace_id"),
                    "desired_state": replacement_desired or None,
                    "deployment_status": replacement_status or None,
                },
            )
            return

        previous = self._repo.get_deployment(deployment_id=previous_deployment_id) or {
            "id": previous_deployment_id,
            "account_id": stop_command.get("account_id"),
        }
        from app.orchestration.deployment_manager import DeploymentManagerService

        manager = DeploymentManagerService(self._repo, command_router=self._command_router)
        try:
            await manager.start_queued_replacement_deployment(
                replacement_deployment_id=replacement_deployment_id,
                previous_deployment=previous,
                stop_command=stop_command,
            )
        except Exception as exc:
            self._repo.update_deployment_status(
                deployment_id=replacement_deployment_id,
                status=DeploymentStatus.FAILED.value,
                desired_state="stopped",
                is_active=False,
                health_status="replacement_start_failed",
                last_error=str(exc)[:200],
                stopped=True,
            )
            self._repo.insert_deployment_audit(
                deployment_id=replacement_deployment_id,
                action="deployment.replacement_start_failed",
                payload={
                    "deployment_id": replacement_deployment_id,
                    "previous_deployment_id": previous_deployment_id,
                    "stop_command_id": stop_command.get("command_id"),
                    "reason": str(exc)[:200],
                },
                result="start_enqueue_failed",
                trace_id=stop_command.get("trace_id"),
            )

    async def _auto_reroute_start_after_slot_failure(
        self,
        *,
        deployment_id: int,
        command: dict[str, Any],
        runner_id: str,
        slot_id: str | None,
        payload: dict[str, Any],
        trace_id: str | None,
    ) -> dict[str, Any]:
        command_payload = command.get("payload_json") if isinstance(command, dict) else {}
        if not isinstance(command_payload, dict):
            command_payload = {}
        control_flow = str(command_payload.get("control_flow") or "").strip()
        if control_flow == "deployment_config_restart":
            return {"retry_queued": False, "reason": "control_flow_not_auto_rerouted"}

        reason = start_failure_reason(payload, fallback="command_rejected")
        action = classify_start_failure(reason=reason, command_payload=command_payload)
        quarantine_result: dict[str, Any] | None = None
        if action.slot_runtime_failure and slot_id:
            quarantine_result = self._repo.quarantine_runner_slot_for_start_failure(
                runner_id=runner_id,
                slot_id=slot_id,
                account_id=int(command.get("account_id") or 0),
                deployment_id=deployment_id,
                command_id=str(command.get("command_id") or ""),
                reason=reason,
                quarantine_sec=action.quarantine_sec,
                runner_failure_window_sec=600,
                runner_throttle_threshold=RUNNER_THROTTLE_FAILURE_THRESHOLD,
                runner_throttle_sec=RUNNER_THROTTLE_SEC,
            )
            self._repo.insert_deployment_audit(
                deployment_id=deployment_id,
                action="deployment.start_slot_quarantined",
                payload={
                    "deployment_id": deployment_id,
                    "command_id": command.get("command_id"),
                    "runner_id": runner_id,
                    "slot_id": slot_id,
                    "reason": reason,
                    "quarantine_sec": SLOT_QUARANTINE_SEC,
                    "quarantine": quarantine_result,
                },
                result="slot_quarantined",
                trace_id=trace_id,
            )
        if not action.auto_reroute:
            return {
                "retry_queued": False,
                "reason": "not_auto_rerouteable",
                "slot_runtime_failure": action.slot_runtime_failure,
                "quarantine": quarantine_result,
            }

        deployment = self._repo.get_deployment(deployment_id=deployment_id)
        if not deployment:
            return {"retry_queued": False, "reason": "deployment_not_found", "quarantine": quarantine_result}
        try:
            user_id = int(deployment.get("user_id") or 0)
            account_id = int(deployment.get("account_id") or command.get("account_id") or 0)
        except Exception:
            return {"retry_queued": False, "reason": "deployment_identity_invalid", "quarantine": quarantine_result}
        if user_id <= 0 or account_id <= 0:
            return {"retry_queued": False, "reason": "deployment_identity_missing", "quarantine": quarantine_result}

        account = self._repo.get_account(account_id=account_id, user_id=user_id)
        if not account:
            return {"retry_queued": False, "reason": "account_not_found", "quarantine": quarantine_result}
        bot_name = str(deployment.get("bot_code") or deployment.get("bot_name") or command.get("bot_id") or "").strip()
        if not bot_name:
            return {"retry_queued": False, "reason": "bot_name_missing", "quarantine": quarantine_result}

        from app.orchestration.deployment_manager import DeploymentManagerService

        manager = DeploymentManagerService(self._repo, command_router=self._command_router)
        retry_extra = {
            "control_flow": "auto_reroute_start",
            "auto_reroute": True,
            "auto_reroute_attempt": action.next_attempt,
            "auto_reroute_from_deployment_id": deployment_id,
            "auto_reroute_from_command_id": command.get("command_id"),
            "auto_reroute_from_runner_id": runner_id,
            "auto_reroute_from_slot_id": slot_id,
            "auto_reroute_reason": reason,
        }
        result = await manager.start_deployment(
            user_id=user_id,
            account=account,
            bot_name=bot_name,
            bot_config_overrides=deployment.get("config_json") or {},
            mode=str(deployment.get("mode") or "live").strip().lower() or "live",
            start_payload_extra=retry_extra,
        )
        retry_deployment = result.get("deployment") or {}
        retry_command = result.get("command") or {}
        self._repo.insert_deployment_audit(
            deployment_id=deployment_id,
            action="deployment.start_auto_rerouted",
            payload={
                "deployment_id": deployment_id,
                "retry_deployment_id": retry_deployment.get("id"),
                "failed_command_id": command.get("command_id"),
                "retry_command_id": retry_command.get("command_id"),
                "from_runner_id": runner_id,
                "from_slot_id": slot_id,
                "to_runner_id": retry_command.get("runner_id"),
                "to_slot_id": retry_command.get("slot_id"),
                "attempt": action.next_attempt,
                "reason": reason,
                "quarantine": quarantine_result,
            },
            result="retry_queued",
            trace_id=trace_id,
        )
        return {
            "retry_queued": True,
            "retry_deployment_id": retry_deployment.get("id"),
            "retry_command_id": retry_command.get("command_id"),
            "runner_id": retry_command.get("runner_id"),
            "slot_id": retry_command.get("slot_id"),
            "attempt": action.next_attempt,
            "quarantine": quarantine_result,
        }

    async def ingest_heartbeat(
        self,
        *,
        runner_id: str,
        slot_id: str | None,
        account_id: int | None,
        deployment_id: int | None,
        payload: dict[str, Any],
        trace_id: str | None,
    ) -> dict[str, Any]:
        event_id = uuid.uuid4().hex
        slot_id_value = _canonical_slot_id(slot_id)
        bind_log_context(
            account_id=account_id,
            deployment_id=deployment_id,
            runner_id=runner_id,
            trace_id=trace_id,
        )
        _log.debug(
            "runner_event.heartbeat runner=%s slot=%s deployment=%s",
            runner_id, slot_id_value, deployment_id,
            extra={
                "event_kind": "heartbeat",
                "runner_event_id": event_id,
                "slot_id": slot_id_value,
            },
        )
        event_model = RunnerEvent.model_validate(
            {
                "event_id": event_id,
                "event_type": RunnerEventType.HEARTBEAT.value,
                "account_id": account_id,
                "deployment_id": deployment_id,
                "bot_id": None,
                "runner_id": runner_id,
                "slot_id": slot_id_value,
                "severity": "info",
                "payload": payload or {},
                "trace_id": trace_id,
            }
        )
        if not self._should_write_heartbeat(
            runner_id=runner_id,
            slot_id=slot_id_value,
            account_id=account_id,
            deployment_id=deployment_id,
            payload=payload or {},
        ):
            return {
                "event_id": event_model.event_id,
                "event_type": event_model.event_type.value,
                "account_id": event_model.account_id,
                "deployment_id": event_model.deployment_id,
                "runner_id": event_model.runner_id,
                "slot_id": event_model.slot_id,
                "throttled": True,
                "skipped_db_write": True,
            }
        self._repo.touch_runner_heartbeat(runner_id=runner_id, slot_id=slot_id_value, payload=payload)
        self._reconcile_runtime_slot(
            deployment_id=deployment_id,
            account_id=account_id,
            runner_id=runner_id,
            slot_id=slot_id_value,
        )
        self._repo.touch_deployment_heartbeat(
            deployment_id=deployment_id,
            account_id=account_id,
            runner_id=runner_id,
            slot_id=slot_id_value,
            payload=payload,
        )
        # Spec §2.3 — renew login lease on each heartbeat. Cheap (1 Redis GET +
        # EXPIRE) and keyed by account_id via the reverse index. Disabled by
        # default unless LOGIN_LEASE_ENABLED=True.
        if account_id is not None and login_lease.is_enabled():
            try:
                await login_lease.renew_for_account(account_id=int(account_id), runner_id=runner_id)
            except Exception:
                pass
        for recovery_request in _backend_recovery_requests_from_heartbeat(
            payload or {},
            runner_id=runner_id,
            slot_id=slot_id_value,
            account_id=account_id,
            deployment_id=deployment_id,
        ):
            await self._handle_backend_runner_recovery_request(
                recovery=recovery_request,
                source="heartbeat",
                trace_id=trace_id,
            )
        self._clear_backend_runner_recovery_if_healthy(
            deployment_id=deployment_id,
            runner_id=runner_id,
            slot_id=slot_id_value,
            payload=payload or {},
            source="heartbeat",
        )
        for item in _slot_inventory_items(payload or {}):
            metadata = _backend_recovery_metadata(item, _payload_dict(item.get("report")))
            merged = {**metadata, **item}
            self._clear_backend_runner_recovery_if_healthy(
                deployment_id=_payload_int(item.get("deployment_id") or item.get("active_deployment_id")),
                runner_id=runner_id,
                slot_id=_canonical_slot_id(item.get("slot_id") or item.get("storage_slot_id")),
                payload=merged,
                source="heartbeat_slot_inventory",
            )
        event = self._repo.insert_execution_event(
            event_id=event_model.event_id,
            event_type=event_model.event_type.value,
            account_id=event_model.account_id,
            deployment_id=event_model.deployment_id,
            bot_id=event_model.bot_id,
            runner_id=event_model.runner_id,
            slot_id=event_model.slot_id,
            command_id=event_model.command_id,
            severity=event_model.severity.value,
            payload=event_model.payload,
            trace_id=event_model.trace_id,
        )
        publish_result = await self._publish_event_best_effort(
            {
                "event_id": event_model.event_id,
                "event_type": event_model.event_type.value,
                "account_id": event_model.account_id,
                "deployment_id": event_model.deployment_id,
                "bot_id": "",
                "runner_id": event_model.runner_id,
                "slot_id": event_model.slot_id or "",
                "command_id": event_model.command_id or "",
                "severity": "info",
                "payload": event_model.payload,
                "trace_id": event_model.trace_id or "",
            }
        )
        if not bool(publish_result.get("published")):
            event = {**dict(event or {}), "redis_publish_warning": publish_result.get("warning")}
        return event

    async def ingest_event(
        self,
        *,
        event_id: str,
        event_type: str,
        account_id: int | None,
        deployment_id: int | None,
        bot_id: str | None,
        runner_id: str,
        slot_id: str | None,
        severity: str,
        payload: dict[str, Any],
        trace_id: str | None,
        command_id: str | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        slot_id_value = _canonical_slot_id(slot_id)
        bind_log_context(
            account_id=account_id,
            deployment_id=deployment_id,
            runner_id=runner_id,
            trace_id=trace_id,
        )
        event_model = RunnerEvent.model_validate(
            {
                "event_id": event_id or uuid.uuid4().hex,
                "event_type": event_type,
                "account_id": account_id,
                "deployment_id": deployment_id,
                "bot_id": bot_id,
                "runner_id": runner_id,
                "slot_id": slot_id_value,
                "severity": severity,
                "payload": payload or {},
                "trace_id": trace_id,
                "command_id": command_id,
                "created_at": created_at,
            }
        )
        event_type_value = event_model.event_type.value
        payload_map = dict(event_model.payload or {})
        event_command_id = event_model.command_id or _payload_command_id(payload_map)
        normalized_event_id = stable_runner_event_id(
            event_id=event_model.event_id,
            event_type=event_type_value,
            command_id=event_command_id or "",
            payload={
                **payload_map,
                "deployment_id": event_model.deployment_id,
                "account_id": event_model.account_id,
            },
        )
        if normalized_event_id and normalized_event_id != event_model.event_id:
            payload_map.setdefault("runner_event_id_original", event_model.event_id)
        if event_model.created_at and not payload_map.get("event_at"):
            payload_map["event_at"] = event_model.created_at
        severity_value = event_model.severity.value
        if event_type_value == EventType.HEARTBEAT.value and not self._should_write_heartbeat(
            runner_id=event_model.runner_id,
            slot_id=event_model.slot_id,
            account_id=event_model.account_id,
            deployment_id=event_model.deployment_id,
            payload=payload_map,
        ):
            return {
                "event_id": normalized_event_id,
                "event_type": event_type_value,
                "account_id": event_model.account_id,
                "deployment_id": event_model.deployment_id,
                "runner_id": event_model.runner_id,
                "slot_id": event_model.slot_id,
                "throttled": True,
                "skipped_db_write": True,
            }

        backend_recovery_result: dict[str, Any] | None = None
        backend_recovery_request = None
        if event_type_value == EventType.RUNTIME_LOG.value:
            backend_recovery_request = _backend_recovery_request_from_payload(
                payload_map,
                runner_id=event_model.runner_id,
                slot_id=event_model.slot_id,
                account_id=event_model.account_id,
                deployment_id=event_model.deployment_id,
            )
            if backend_recovery_request and self._backend_recovery_event_is_suppressed(backend_recovery_request):
                return {
                    "event_id": normalized_event_id,
                    "event_type": event_type_value,
                    "account_id": event_model.account_id,
                    "deployment_id": event_model.deployment_id,
                    "runner_id": event_model.runner_id,
                    "slot_id": event_model.slot_id,
                    "backend_recovery": {
                        "handled": True,
                        "action": "suppressed_repeated_noop_event",
                    },
                    "throttled": True,
                    "skipped_db_write": True,
                }

        if event_type_value != EventType.HEARTBEAT.value:
            severity_norm = str(severity_value or "info").strip().lower()
            log_level = logging.WARNING if severity_norm in {"warning", "warn"} else (
                logging.ERROR if severity_norm in {"error", "critical", "fatal"} else logging.INFO
            )
            _log.log(
                log_level,
                "runner_event.ingest type=%s runner=%s slot=%s deployment=%s severity=%s",
                event_type_value, event_model.runner_id, event_model.slot_id, event_model.deployment_id, severity_value,
                extra={
                    "event_kind": "runner_event",
                    "runner_event_id": normalized_event_id,
                    "runner_event_type": event_type_value,
                    "runner_event_severity": severity_value,
                    "slot_id": event_model.slot_id,
                    "command_id": event_command_id,
                    "bot_id": event_model.bot_id,
                },
            )

        self._repo.touch_runner_heartbeat(runner_id=runner_id, slot_id=slot_id_value, payload=payload_map)
        if event_type_value == EventType.HEARTBEAT.value:
            self._reconcile_runtime_slot(
                deployment_id=event_model.deployment_id,
                account_id=event_model.account_id,
                runner_id=event_model.runner_id,
                slot_id=event_model.slot_id,
            )
            self._repo.touch_deployment_heartbeat(
                deployment_id=event_model.deployment_id,
                account_id=event_model.account_id,
                runner_id=event_model.runner_id,
                slot_id=event_model.slot_id,
                payload=payload_map,
            )
            if event_model.deployment_id is not None and _payload_confirms_terminal_stopped(payload_map):
                deployment = self._repo.get_deployment(deployment_id=event_model.deployment_id)
                if str((deployment or {}).get("health_status") or "").strip().lower() == "terminal_cleanup_pending":
                    await self._finalize_stopped_deployment(
                        event_model=event_model,
                        command_id=event_command_id,
                    )
            for recovery_request in _backend_recovery_requests_from_heartbeat(
                payload_map,
                runner_id=event_model.runner_id,
                slot_id=event_model.slot_id,
                account_id=event_model.account_id,
                deployment_id=event_model.deployment_id,
            ):
                await self._handle_backend_runner_recovery_request(
                    recovery=recovery_request,
                    source="event_heartbeat",
                    trace_id=event_model.trace_id,
                )
            self._clear_backend_runner_recovery_if_healthy(
                deployment_id=event_model.deployment_id,
                runner_id=event_model.runner_id,
                slot_id=event_model.slot_id,
                payload=payload_map,
                source="event_heartbeat",
            )
            for item in _slot_inventory_items(payload_map):
                metadata = _backend_recovery_metadata(item, _payload_dict(item.get("report")))
                merged = {**metadata, **item}
                self._clear_backend_runner_recovery_if_healthy(
                    deployment_id=_payload_int(item.get("deployment_id") or item.get("active_deployment_id")),
                    runner_id=event_model.runner_id,
                    slot_id=_canonical_slot_id(item.get("slot_id") or item.get("storage_slot_id")),
                    payload=merged,
                    source="event_heartbeat_slot_inventory",
                )

        event = self._repo.insert_execution_event(
            event_id=normalized_event_id,
            event_type=event_type_value,
            account_id=event_model.account_id,
            deployment_id=event_model.deployment_id,
            bot_id=event_model.bot_id,
            runner_id=event_model.runner_id,
            slot_id=event_model.slot_id,
            command_id=event_command_id,
            severity=severity_value,
            payload=payload_map,
            trace_id=event_model.trace_id,
        )

        if (
            event_type_value == EventType.RUNTIME_LOG.value
            and _payload_login_slot_command_type(payload_map) == CommandType.RESERVE_OR_LOGIN_SLOT.value
            and event_command_id
        ):
            runtime_status = str(payload_map.get("status") or "").strip().lower()
            prepared = _payload_truthy(payload_map.get("prepared"))
            if prepared or runtime_status in {"healthy", "verified", "ready"}:
                result = self._repo.complete_login_reservation(
                    command_id=event_command_id,
                    ok=True,
                    runner_id=event_model.runner_id,
                    slot_id=event_model.slot_id or _canonical_slot_id(payload_map.get("slot_id")),
                    error_text=None,
                    payload={**payload_map, "compat_event_type": "RUNTIME_LOG_PREPARED"},
                    ttl_sec=int(payload_map.get("login_slot_ttl_sec") or 300),
                )
                self._repo.update_execution_command_delivery(
                    command_id=event_command_id,
                    status="acknowledged",
                    error_text=None,
                    payload={
                        "last_event_type": "RUNTIME_LOG_PREPARED",
                        "runner_id": event_model.runner_id,
                        "slot_id": event_model.slot_id,
                    },
                )
                return {"event": event, "login_reservation": result, "ok": True, "compat": "runtime_log_prepared"}
            if runtime_status in {"failed", "error", "broken"}:
                result = self._repo.complete_login_reservation(
                    command_id=event_command_id,
                    ok=False,
                    runner_id=event_model.runner_id,
                    slot_id=event_model.slot_id or _canonical_slot_id(payload_map.get("slot_id")),
                    error_text=_event_reason(payload_map) or "runtime_log_login_slot_failed",
                    payload={**payload_map, "compat_event_type": "RUNTIME_LOG_FAILED"},
                )
                self._repo.update_execution_command_delivery(
                    command_id=event_command_id,
                    status="failed",
                    error_text=_event_reason(payload_map) or "runtime_log_login_slot_failed",
                    payload={
                        "last_event_type": "RUNTIME_LOG_FAILED",
                        "runner_id": event_model.runner_id,
                        "slot_id": event_model.slot_id,
                    },
                )
                return {"event": event, "login_reservation": result, "ok": False, "compat": "runtime_log_failed"}

        if event_type_value in LOGIN_SLOT_FINAL_EVENT_TYPES:
            result = apply_login_slot_final_event(
                self._repo,
                event_type_value=event_type_value,
                account_id=event_model.account_id,
                command_id=event_command_id,
                runner_id=event_model.runner_id,
                slot_id=event_model.slot_id,
                payload_map=payload_map,
            )
            return {"event": event, **result}

        recovery_event_type = _payload_recovery_event_type(event_type=event_type_value, payload=payload_map)
        if recovery_event_type and event_type_value != EventType.RUNTIME_LOG.value:
            self._apply_recovery_event(
                event_model=event_model,
                recovery_event_type=recovery_event_type,
                payload=payload_map,
            )

        if event_type_value != EventType.HEARTBEAT.value:
            if backend_recovery_request is None:
                backend_recovery_request = _backend_recovery_request_from_payload(
                    payload_map,
                    runner_id=event_model.runner_id,
                    slot_id=event_model.slot_id,
                    account_id=event_model.account_id,
                    deployment_id=event_model.deployment_id,
                )
            if backend_recovery_request:
                backend_recovery_result = await self._handle_backend_runner_recovery_request(
                    recovery=backend_recovery_request,
                    source=event_type_value.lower(),
                    trace_id=event_model.trace_id,
                )
                self._suppress_backend_recovery_event_if_noop(
                    backend_recovery_request,
                    str((backend_recovery_result or {}).get("action") or ""),
                )

        if event_command_id and event_type_value in {
            EventType.BOT_STARTED.value,
            EventType.BOT_STOPPED.value,
            EventType.SIGNAL_EXECUTOR_READY.value,
            EventType.SIGNAL_EXECUTOR_STOPPED.value,
            EventType.BOT_LISTENING.value,
            EventType.ORDER_SENT.value,
            EventType.ORDER_FILLED.value,
            EventType.POSITION_UPDATED.value,
        }:
            self._repo.update_execution_command_delivery(
                command_id=event_command_id,
                status="acknowledged",
                error_text=None,
                payload={"last_event_type": event_type_value, "runner_id": event_model.runner_id, "slot_id": event_model.slot_id},
            )
        elif event_command_id and event_type_value in {EventType.ORDER_REJECTED.value, EventType.COMMAND_REJECTED.value}:
            self._repo.update_execution_command_delivery(
                command_id=event_command_id,
                status="failed",
                error_text=str(payload_map.get("reason") or payload_map.get("message") or event_type_value.lower()),
                payload={"last_event_type": event_type_value, "runner_id": event_model.runner_id, "slot_id": event_model.slot_id},
            )
            if event_type_value == EventType.ORDER_REJECTED.value and event_model.deployment_id is not None:
                command = self._repo.get_execution_command(command_id=event_command_id or "")
                command_type = str((command or {}).get("command_type") or "").strip().upper()
                if command_type in {"PLACE_ORDER", "MODIFY_ORDER", "CLOSE_ORDER", "SYNC_STATE"}:
                    self._repo.update_deployment_status(
                        deployment_id=event_model.deployment_id,
                        status=DeploymentStatus.RUNNING.value,
                        desired_state="running",
                        is_active=True,
                        health_status="running",
                        runner_id=event_model.runner_id,
                        slot_id=event_model.slot_id,
                    )

        if event_type_value in {EventType.BOT_STARTED.value, EventType.BOT_LISTENING.value} and event_model.deployment_id is not None:
            deployment = self._repo.get_deployment(deployment_id=event_model.deployment_id)
            if not _deployment_wants_stopped(deployment):
                self._reconcile_runtime_slot(
                    deployment_id=event_model.deployment_id,
                    account_id=event_model.account_id,
                    runner_id=event_model.runner_id,
                    slot_id=event_model.slot_id,
                )
                self._repo.update_deployment_status(
                    deployment_id=event_model.deployment_id,
                    status=DeploymentStatus.RUNNING.value,
                    desired_state="running",
                    is_active=True,
                    health_status="running",
                    started=True,
                    runner_id=event_model.runner_id,
                    slot_id=event_model.slot_id,
                )
                started_account_id = event_model.account_id or (deployment or {}).get("account_id")
                if started_account_id is not None:
                    self._repo.mark_account_runtime_login_result(
                        account_id=int(started_account_id),
                        ok=True,
                    )
                self._clear_backend_runner_recovery_if_healthy(
                    deployment_id=event_model.deployment_id,
                    runner_id=event_model.runner_id,
                    slot_id=event_model.slot_id,
                    payload={**payload_map, "status": "running", "terminal_running": True, "worker_alive": True},
                    source=event_type_value.lower(),
                )
                if event_command_id:
                    command = self._repo.get_execution_command(command_id=event_command_id)
                    command_payload = command.get("payload_json") if isinstance(command, dict) else {}
                    if isinstance(command_payload, dict) and command_payload.get("control_flow") == "deployment_config_restart":
                        self._repo.insert_deployment_audit(
                            deployment_id=event_model.deployment_id,
                            action="deployment.config.restarted",
                            payload={
                                "deployment_id": event_model.deployment_id,
                                "account_id": event_model.account_id,
                                "command_id": event_command_id,
                                "runner_id": event_model.runner_id,
                                "slot_id": event_model.slot_id,
                            },
                            result="bot_started",
                            trace_id=event_model.trace_id,
                        )
        elif event_type_value == EventType.SIGNAL_EXECUTOR_READY.value and event_model.deployment_id is not None:
            deployment = self._repo.get_deployment(deployment_id=event_model.deployment_id)
            if not _deployment_wants_stopped(deployment) and str((deployment or {}).get("status") or "").strip().lower() != DeploymentStatus.RUNNING.value:
                self._repo.update_deployment_status(
                    deployment_id=event_model.deployment_id,
                    status=DeploymentStatus.STARTING.value,
                    desired_state="running",
                    is_active=True,
                    health_status="executor_ready",
                    runner_id=event_model.runner_id,
                    slot_id=event_model.slot_id,
                )
        elif event_type_value == EventType.SIGNAL_EXECUTOR_PREPARING.value and event_model.deployment_id is not None:
            deployment = self._repo.get_deployment(deployment_id=event_model.deployment_id)
            if not _deployment_wants_stopped(deployment) and str((deployment or {}).get("status") or "").strip().lower() != DeploymentStatus.RUNNING.value:
                self._repo.update_deployment_status(
                    deployment_id=event_model.deployment_id,
                    status=DeploymentStatus.STARTING.value,
                    desired_state="running",
                    is_active=True,
                    health_status="executor_preparing",
                    runner_id=event_model.runner_id,
                    slot_id=event_model.slot_id,
                )
        elif event_type_value == EventType.SIGNAL_EXECUTOR_STOPPING.value and event_model.deployment_id is not None:
            self._repo.update_deployment_status(
                deployment_id=event_model.deployment_id,
                status=DeploymentStatus.STOP_REQUESTED.value,
                desired_state="stopped",
                is_active=True,
                health_status="executor_stopping",
                runner_id=event_model.runner_id,
                slot_id=event_model.slot_id,
            )
        elif event_type_value in {EventType.BOT_STOPPED.value, EventType.SIGNAL_EXECUTOR_STOPPED.value} and event_model.deployment_id is not None:
            # STOP events must be idempotent.  Do not call the runtime-slot
            # reconcile path here: that path marks a binding active/current and
            # can collide with a newer deployment for the same account.
            command = self._repo.get_execution_command(command_id=event_command_id or "")
            deployment = self._repo.get_deployment(deployment_id=event_model.deployment_id)
            cleanup_done = str((deployment or {}).get("health_status") or "").strip().lower() == "terminal_cleanup_done"
            stop_is_idempotent = _payload_truthy(payload_map.get("idempotent"))
            stop_reason = str(payload_map.get("reason") or payload_map.get("error") or "").strip().lower()
            terminal_already_stopped = (
                _payload_confirms_terminal_stopped(payload_map)
                or stop_is_idempotent
                or stop_reason in {"account_not_active", "deployment_not_active", "slot_not_active"}
            )
            if _command_requests_terminal_kill(command) and not cleanup_done and not terminal_already_stopped:
                self._repo.update_deployment_status(
                    deployment_id=event_model.deployment_id,
                    status=DeploymentStatus.STOP_REQUESTED.value,
                    desired_state="stopped",
                    is_active=True,
                    health_status="terminal_cleanup_pending",
                    runner_id=event_model.runner_id,
                    slot_id=event_model.slot_id,
                )
            else:
                await self._finalize_stopped_deployment(
                    event_model=event_model,
                    command_id=event_command_id,
                )
        elif event_type_value in {
            EventType.SLOT_TERMINAL_KILL_BEGIN.value,
            EventType.SLOT_TERMINAL_KILL_DONE.value,
        }:
            await self._apply_terminal_kill_event(
                event_model=event_model,
                command_id=event_command_id,
                terminal_event_type=event_type_value,
            )
        elif event_type_value == EventType.SLOT_BROKEN.value:
            if event_model.slot_id:
                self._repo.update_runner_slot_state(
                    runner_id=event_model.runner_id,
                    slot_id=event_model.slot_id,
                    status="broken",
                    metadata=payload_map,
                )
            backend_recovery_handled = bool(backend_recovery_request)
            if event_model.deployment_id is not None and not backend_recovery_handled:
                deployment = self._repo.get_deployment(deployment_id=event_model.deployment_id)
                if not _deployment_wants_stopped(deployment):
                    self._repo.update_deployment_status(
                        deployment_id=event_model.deployment_id,
                        status=DeploymentStatus.FAILED.value,
                        desired_state="stopped",
                        is_active=False,
                        health_status="broken",
                        last_error=str(payload_map.get("reason") or "slot_broken"),
                        stopped=True,
                    )
            schedule_error_alert(
                area="Windows runner",
                summary="Một slot MT5 bị lỗi, backend đã nhận tín hiệu recovery." if backend_recovery_handled else "Một slot MT5 bị lỗi.",
                severity="warning" if backend_recovery_handled else "critical",
                account_id=event_model.account_id,
                deployment_id=event_model.deployment_id,
                runner_id=event_model.runner_id,
                slot_id=event_model.slot_id,
                impact=(
                    "Backend sẽ restart deployment nếu intent vẫn là running."
                    if backend_recovery_handled
                    else "Bot trên slot này có thể đã dừng hoặc không nhận lệnh."
                ),
                action="Theo dõi recovery decision và START_BOT command." if backend_recovery_handled else "Kiểm tra MT5 terminal, worker slot và log Windows.",
                detail={
                    "reason": _event_reason(payload_map) or "slot_broken",
                    "backend_recovery": backend_recovery_result or {},
                },
                alert_key=f"slot_broken:{event_model.runner_id}:{event_model.slot_id}",
                cooldown_sec=180,
            )
        elif event_type_value == EventType.SLOT_DEGRADED.value:
            if event_model.slot_id:
                self._repo.update_runner_slot_state(
                    runner_id=event_model.runner_id,
                    slot_id=event_model.slot_id,
                    status="degraded",
                    metadata=payload_map,
                )
            if event_model.deployment_id is not None:
                self._repo.update_deployment_status(
                    deployment_id=event_model.deployment_id,
                    status=DeploymentStatus.RUNNING.value,
                    desired_state="running",
                    is_active=True,
                    health_status="degraded",
                )
        elif event_type_value == EventType.SLOT_STATE_CHANGED.value:
            slot_state = _normalize_slot_projection_state(payload_map)
            if event_model.slot_id and slot_state:
                self._repo.update_runner_slot_state(
                    runner_id=event_model.runner_id,
                    slot_id=event_model.slot_id,
                    status=slot_state,
                    metadata=payload_map,
                )
            if event_model.deployment_id is not None and _slot_state_event_confirms_runtime_started(payload_map):
                self._clear_backend_runner_recovery_if_healthy(
                    deployment_id=event_model.deployment_id,
                    runner_id=event_model.runner_id,
                    slot_id=event_model.slot_id,
                    payload={
                        **payload_map,
                        "status": payload_map.get("status") or payload_map.get("current_state") or "active",
                        "terminal_running": payload_map.get("terminal_running")
                        if payload_map.get("terminal_running") is not None
                        else bool(payload_map.get("terminal_path") or payload_map.get("terminal_pid")),
                    },
                    source="slot_state_changed",
                )
        elif event_type_value == EventType.COMMAND_REJECTED.value:
            command = self._repo.get_execution_command(command_id=event_command_id or "")
            if command and str(command.get("command_type") or "").strip().upper() == CommandType.RESERVE_OR_LOGIN_SLOT.value:
                self._repo.complete_login_reservation(
                    command_id=event_command_id,
                    ok=False,
                    runner_id=event_model.runner_id,
                    slot_id=event_model.slot_id,
                    error_text=_event_reason(payload_map) or "login_slot_command_rejected",
                    payload=payload_map,
                )
            if command and event_model.deployment_id is not None:
                command_type = str(command.get("command_type") or "").strip().upper()
                command_payload = command.get("payload_json") if isinstance(command, dict) else {}
                if isinstance(command_payload, dict) and command_payload.get("control_flow") == "deployment_config_restart":
                    self._repo.insert_deployment_audit(
                        deployment_id=event_model.deployment_id,
                        action="deployment.config.restart_failed",
                        payload={
                            "deployment_id": event_model.deployment_id,
                            "account_id": event_model.account_id,
                            "command_id": event_command_id,
                            "command_type": command_type,
                            "reason": _event_reason(payload_map) or "command_rejected",
                        },
                        result="command_rejected",
                        trace_id=event_model.trace_id,
                    )
                if isinstance(command_payload, dict) and command_payload.get("control_flow") == "deployment_replacement_start":
                    try:
                        replacement_deployment_id = int(command_payload.get("replacement_deployment_id") or 0)
                    except Exception:
                        replacement_deployment_id = 0
                    reason = _event_reason(payload_map) or "command_rejected"
                    if replacement_deployment_id > 0:
                        self._repo.update_deployment_status(
                            deployment_id=replacement_deployment_id,
                            status=DeploymentStatus.FAILED.value,
                            desired_state="stopped",
                            is_active=False,
                            health_status="replacement_stop_rejected",
                            last_error=reason,
                            stopped=True,
                        )
                        self._repo.insert_deployment_audit(
                            deployment_id=replacement_deployment_id,
                            action="deployment.replacement_start_failed",
                            payload={
                                "deployment_id": replacement_deployment_id,
                                "previous_deployment_id": event_model.deployment_id,
                                "command_id": event_command_id,
                                "reason": reason,
                            },
                            result="previous_stop_rejected",
                            trace_id=event_model.trace_id,
                        )
                if command_type == "UPDATE_BOT_CONFIG":
                    schedule_error_alert(
                        area="Cập nhật cấu hình bot",
                        summary="Runner từ chối cập nhật cấu hình khi bot đang chạy.",
                        severity="warning",
                        account_id=event_model.account_id,
                        deployment_id=event_model.deployment_id,
                        runner_id=event_model.runner_id,
                        slot_id=event_model.slot_id,
                        impact="Thay đổi như DCA có thể chưa có hiệu lực ngay.",
                        action="Kiểm tra reason từ runner và fallback restart policy.",
                        detail={"reason": _event_reason(payload_map) or "command_rejected"},
                        alert_key=f"update_bot_config_rejected:{event_model.deployment_id}:{_event_reason(payload_map)}",
                        cooldown_sec=180,
                    )
                    await self._fallback_config_hot_update_restart(
                        deployment_id=event_model.deployment_id,
                        command=command,
                        reason=_event_reason(payload_map) or "command_rejected",
                        trace_id=event_model.trace_id,
                    )
                if command_type == "START_BOT":
                    reason = start_failure_reason(payload_map, fallback="command_rejected")
                    if _is_backend_runner_recovery_command(command):
                        mark_failed = getattr(self._repo, "mark_backend_runner_recovery_dispatch_failed", None)
                        if callable(mark_failed):
                            mark_failed(
                                deployment_id=event_model.deployment_id,
                                reason=f"command_rejected:{reason}",
                            )
                        self._repo.update_deployment_status(
                            deployment_id=event_model.deployment_id,
                            status=DeploymentStatus.RUNNING.value,
                            desired_state="running",
                            is_active=True,
                            health_status="runner_recovery_pending",
                            last_error=reason,
                            runner_id=event_model.runner_id,
                            slot_id=event_model.slot_id,
                        )
                        self._repo.insert_deployment_audit(
                            deployment_id=event_model.deployment_id,
                            action="deployment.backend_runner_recovery_rejected",
                            payload={
                                "deployment_id": event_model.deployment_id,
                                "account_id": event_model.account_id,
                                "command_id": event_command_id,
                                "runner_id": event_model.runner_id,
                                "slot_id": event_model.slot_id,
                                "reason": reason,
                            },
                            result="will_retry",
                            trace_id=event_model.trace_id,
                        )
                        schedule_error_alert(
                            area="Windows runner recovery",
                            summary="Runner từ chối START_BOT recovery, backend vẫn giữ bot ở trạng thái cần phục hồi.",
                            severity="warning",
                            account_id=event_model.account_id,
                            deployment_id=event_model.deployment_id,
                            runner_id=event_model.runner_id,
                            slot_id=event_model.slot_id,
                            impact="Backend không tắt intent của user; recovery sẽ thử lại theo cooldown/budget.",
                            action="Kiểm tra runner có cho phép clean restart trên slot BROKEN hay chưa.",
                            detail={"reason": reason, "command_id": event_command_id},
                            alert_key=f"backend_runner_recovery_rejected:{event_model.deployment_id}:{reason}",
                            cooldown_sec=180,
                        )
                        return {"event": event, "backend_recovery": "command_rejected_kept_running", "reason": reason}
                    failed_account_id = event_model.account_id or command.get("account_id")
                    if failed_account_id is not None and start_failure_is_credential_failure(
                        reason=reason,
                        payload=payload_map,
                    ):
                        self._repo.mark_account_runtime_login_result(
                            account_id=int(failed_account_id),
                            ok=False,
                            error_text=reason,
                        )
                    self._repo.update_deployment_status(
                        deployment_id=event_model.deployment_id,
                        status=DeploymentStatus.FAILED.value,
                        desired_state="stopped",
                        is_active=False,
                        health_status="rejected",
                        last_error=reason,
                        stopped=True,
                    )
                    self._repo.release_deployment_slot(
                        deployment_id=event_model.deployment_id,
                        keep_sticky=False,
                    )
                    reroute_result: dict[str, Any] = {}
                    try:
                        reroute_result = await self._auto_reroute_start_after_slot_failure(
                            deployment_id=event_model.deployment_id,
                            command=command,
                            runner_id=event_model.runner_id,
                            slot_id=event_model.slot_id,
                            payload=payload_map,
                            trace_id=event_model.trace_id,
                        )
                    except Exception as exc:
                        reroute_result = {"retry_queued": False, "reason": f"auto_reroute_failed:{exc.__class__.__name__}"}
                        schedule_error_alert(
                            area="Tự chuyển slot",
                            summary="Backend chưa tự chuyển được bot sang slot khác.",
                            severity="warning",
                            account_id=event_model.account_id,
                            deployment_id=event_model.deployment_id,
                            runner_id=event_model.runner_id,
                            slot_id=event_model.slot_id,
                            impact="User có thể phải bật lại bot.",
                            action="Kiểm tra slot còn trống và lỗi START gần nhất.",
                            detail={"reason": reason, "error": str(exc)[:200]},
                            alert_key=f"start_auto_reroute_failed:{event_model.deployment_id}:{exc.__class__.__name__}",
                            cooldown_sec=180,
                        )
                    if reroute_result.get("retry_queued"):
                        schedule_error_alert(
                            area="Windows runner",
                            summary="Một slot lỗi, backend đã tự chuyển bot sang slot khác.",
                            severity="warning",
                            account_id=event_model.account_id,
                            deployment_id=reroute_result.get("retry_deployment_id") or event_model.deployment_id,
                            runner_id=reroute_result.get("runner_id") or event_model.runner_id,
                            slot_id=reroute_result.get("slot_id") or event_model.slot_id,
                            impact="User không cần thao tác lại nếu slot mới chạy thành công.",
                            action="Theo dõi command START mới và slot vừa được chuyển.",
                            detail={
                                "failed_deployment_id": event_model.deployment_id,
                                "failed_runner_id": event_model.runner_id,
                                "failed_slot_id": event_model.slot_id,
                                "reason": reason,
                                "retry": reroute_result,
                            },
                            alert_key=f"start_bot_auto_rerouted:{event_model.deployment_id}:{reroute_result.get('retry_deployment_id')}",
                            cooldown_sec=180,
                        )
                    else:
                        schedule_error_alert(
                            area="Windows runner",
                            summary="Runner từ chối lệnh bật bot.",
                            severity="critical",
                            account_id=event_model.account_id,
                            deployment_id=event_model.deployment_id,
                            runner_id=event_model.runner_id,
                            slot_id=event_model.slot_id,
                            impact="User có thể không bật được bot.",
                            action="Kiểm tra reason từ runner, slot và command START_BOT.",
                            detail={"reason": reason, "auto_reroute": reroute_result},
                            alert_key=f"start_bot_rejected:{event_model.deployment_id}:{reason}",
                            cooldown_sec=180,
                        )
                elif command_type == "STOP_BOT" and _event_reason(payload_map) == "account_not_active":
                    deployment = self._repo.get_deployment(deployment_id=event_model.deployment_id)
                    if _deployment_wants_stopped(deployment):
                        if event_command_id:
                            self._repo.update_execution_command_delivery(
                                command_id=event_command_id,
                                status="acknowledged",
                                error_text=None,
                                payload={
                                    "last_event_type": event_type_value,
                                    "runner_id": event_model.runner_id,
                                    "slot_id": event_model.slot_id,
                                    "idempotent_stop": True,
                                    "runner_reason": "account_not_active",
                                },
                            )
                        self._repo.update_deployment_status(
                            deployment_id=event_model.deployment_id,
                            status=DeploymentStatus.STOPPED.value,
                            desired_state="stopped",
                            is_active=False,
                            health_status="stopped",
                            stopped=True,
                            runner_id=event_model.runner_id,
                            slot_id=event_model.slot_id,
                        )
                        self._repo.release_deployment_slot(deployment_id=event_model.deployment_id, keep_sticky=False)
                        self._repo.fail_pending_start_commands_for_deployment(
                            deployment_id=event_model.deployment_id,
                            reason="stop_rejected_account_not_active",
                        )
                        await self._restart_after_config_stop(
                            deployment_id=event_model.deployment_id,
                            command_id=event_command_id,
                        )
                        await self._start_queued_replacement_after_stop(
                            previous_deployment_id=event_model.deployment_id,
                            command_id=event_command_id,
                        )
                elif command_type in {"PLACE_ORDER", "MODIFY_ORDER", "CLOSE_ORDER", "SYNC_STATE"}:
                    self._repo.update_deployment_status(
                        deployment_id=event_model.deployment_id,
                        status=DeploymentStatus.RUNNING.value,
                        desired_state="running",
                        is_active=True,
                        health_status="running",
                        runner_id=event_model.runner_id,
                        slot_id=event_model.slot_id,
                    )
        elif event_type_value == EventType.RUNTIME_LOG.value:
            runtime_message = str(payload_map.get("message") or payload_map.get("log_message") or "runtime_log")
            self._repo.insert_runtime_log(
                account_id=event_model.account_id,
                deployment_id=event_model.deployment_id,
                runner_id=event_model.runner_id,
                slot_id=event_model.slot_id,
                level=str(payload_map.get("level") or severity_value or "info"),
                message=runtime_message,
                payload=payload_map,
                trace_id=event_model.trace_id,
            )
            terminal_event_type = _runtime_log_terminal_event_type(payload_map, runtime_message)
            if terminal_event_type:
                await self._apply_terminal_kill_event(
                    event_model=event_model,
                    command_id=event_command_id,
                    terminal_event_type=terminal_event_type,
                )
            recovery_event_type = _payload_recovery_event_type(
                event_type=event_type_value,
                payload=payload_map,
                runtime_message=runtime_message,
            )
            if recovery_event_type:
                self._apply_recovery_event(
                    event_model=event_model,
                    recovery_event_type=recovery_event_type,
                    payload=payload_map,
                )
            runtime_text = f"{runtime_message} {_event_reason(payload_map)}".lower()
            if "backend_state" in runtime_text or "store_candle_skipped" in runtime_text:
                schedule_error_alert(
                    area="GsAlgo state bridge",
                    summary="Bot báo lỗi khi gửi state/candle về backend.",
                    severity="warning",
                    account_id=event_model.account_id,
                    deployment_id=event_model.deployment_id,
                    runner_id=event_model.runner_id,
                    slot_id=event_model.slot_id,
                    impact="Dashboard, audit hoặc recovery có thể thiếu dữ liệu.",
                    action="Kiểm tra endpoint bot-state, nginx và dữ liệu state mới nhất.",
                    detail={
                        "message": runtime_message,
                        "reason": _event_reason(payload_map),
                        "status_code": payload_map.get("status_code"),
                    },
                    alert_key=f"runtime_backend_state:{event_model.deployment_id}:{payload_map.get('status_code') or _event_reason(payload_map)}",
                    cooldown_sec=180,
                )
            bootstrap_failure_reason = _start_bootstrap_failure_reason(payload_map)
            if bootstrap_failure_reason:
                failed_start = self._fail_start_deployment_after_bootstrap_failure(
                    deployment_id=event_model.deployment_id,
                    account_id=event_model.account_id,
                    command_id=event_command_id,
                    runner_id=event_model.runner_id,
                    slot_id=event_model.slot_id,
                    reason=bootstrap_failure_reason,
                    trace_id=event_model.trace_id,
                    event_type=event_type_value,
                    payload=payload_map,
                )
                if failed_start and event_command_id:
                    command = self._repo.get_execution_command(command_id=event_command_id)
                    if command:
                        try:
                            await self._auto_reroute_start_after_slot_failure(
                                deployment_id=event_model.deployment_id,
                                command=command,
                                runner_id=event_model.runner_id,
                                slot_id=event_model.slot_id,
                                payload=payload_map,
                                trace_id=event_model.trace_id,
                            )
                        except Exception as exc:
                            schedule_error_alert(
                                area="Tự chuyển slot",
                                summary="Backend chưa tự chuyển được bot sau lỗi bootstrap.",
                                severity="warning",
                                account_id=event_model.account_id,
                                deployment_id=event_model.deployment_id,
                                runner_id=event_model.runner_id,
                                slot_id=event_model.slot_id,
                                impact="User có thể phải bật lại bot.",
                                action="Kiểm tra slot còn trống và lỗi START gần nhất.",
                                detail={"reason": bootstrap_failure_reason, "error": str(exc)[:200]},
                                alert_key=f"start_bootstrap_auto_reroute_failed:{event_model.deployment_id}:{exc.__class__.__name__}",
                                cooldown_sec=180,
                            )
        if event_model.account_id is not None and payload_map:
            if any(key in payload_map for key in ("pnl", "balance", "equity", "free_margin", "connection_status")):
                self._repo.upsert_account_state_snapshot(
                    account_id=event_model.account_id,
                    deployment_id=event_model.deployment_id,
                    runner_id=event_model.runner_id,
                    slot_id=event_model.slot_id,
                    connection_status=str(payload_map.get("connection_status") or "connected"),
                    pnl=float(payload_map["pnl"]) if payload_map.get("pnl") is not None else None,
                    balance=float(payload_map["balance"]) if payload_map.get("balance") is not None else None,
                    equity=float(payload_map["equity"]) if payload_map.get("equity") is not None else None,
                    free_margin=float(payload_map["free_margin"]) if payload_map.get("free_margin") is not None else None,
                    payload=payload_map,
                )
            positions = payload_map.get("positions")
            if isinstance(positions, list):
                for index, position in enumerate(positions):
                    if not isinstance(position, dict):
                        continue
                    self._repo.upsert_position_snapshot(
                        account_id=event_model.account_id,
                        deployment_id=event_model.deployment_id,
                        position_key=str(position.get("position_key") or position.get("id") or f"pos-{index}"),
                        symbol=str(position.get("symbol") or ""),
                        side=str(position.get("side") or ""),
                        volume=float(position["volume"]) if position.get("volume") is not None else None,
                        entry_price=float(position["entry_price"]) if position.get("entry_price") is not None else None,
                        mark_price=float(position["mark_price"]) if position.get("mark_price") is not None else None,
                        pnl=float(position["pnl"]) if position.get("pnl") is not None else None,
                        payload=position,
                    )

        self._repo.upsert_execution_audit(
            event_id=normalized_event_id,
            command_id=event_command_id,
            trace_id=event_model.trace_id,
            account_id=event_model.account_id,
            deployment_id=event_model.deployment_id,
            runner_id=event_model.runner_id,
            slot_id=event_model.slot_id,
            event_type=event_type_value,
            severity=severity_value,
            audit_status="recorded",
            payload=payload_map,
            source_stream_id=None,
        )

        publish_result = await self._publish_event_best_effort(
            {
                "event_id": normalized_event_id,
                "event_type": event_type_value,
                "account_id": event_model.account_id,
                "deployment_id": event_model.deployment_id,
                "bot_id": event_model.bot_id or "",
                "runner_id": event_model.runner_id,
                "slot_id": event_model.slot_id or "",
                "command_id": event_command_id or "",
                "severity": severity_value,
                "payload": payload_map,
                "trace_id": event_model.trace_id or "",
            }
        )
        if not bool(publish_result.get("published")):
            event = {**dict(event or {}), "redis_publish_warning": publish_result.get("warning")}
        return event
