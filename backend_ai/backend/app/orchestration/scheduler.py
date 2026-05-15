from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.risk.orchestration_policy import requires_dedicated_runner

RUNNER_ACTIVE_LIMIT_DEFAULT = 10
RUNNER_NODE_SLOT_LIMIT_DEFAULT = RUNNER_ACTIVE_LIMIT_DEFAULT
IPC_READY_FRESHNESS_SEC_DEFAULT = 3600
RESIDENT_WORKER_FRESHNESS_SEC_DEFAULT = 120

_MAINTENANCE_BOOL_KEYS = {
    "maintenance",
    "maintenance_mode",
    "paused",
    "pause",
    "frozen",
    "freeze",
    "dispatch_paused",
    "login_paused",
    "warm_guard_paused",
    "warm_guard_pause",
    "warm_pool_paused",
    "runner_paused",
    "runner_frozen",
}

_MAINTENANCE_STATE_VALUES = {
    "draining",
    "frozen",
    "maintenance",
    "mt5_runtime_maintenance",
    "paused",
    "pause",
    "login_paused",
    "warm_guard_paused",
}


@dataclass
class SchedulerDecision:
    ok: bool
    runner_id: str = ""
    slot_id: str = ""
    reason: str = ""
    sticky_reused: bool = False


def _decision_payload(decision: SchedulerDecision) -> dict[str, Any]:
    return {
        "ok": bool(decision.ok),
        "runner_id": decision.runner_id,
        "slot_id": decision.slot_id,
        "reason": decision.reason,
        "sticky_reused": bool(decision.sticky_reused),
    }


def _parse_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), timezone.utc)
        except Exception:
            return None
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromtimestamp(float(raw), timezone.utc)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _parse_account_id(value: Any) -> Optional[int]:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw or raw.lower() in {"none", "null", "false"} or raw == "0":
        return None
    try:
        return int(raw)
    except Exception:
        return None


def _slot_metadata(slot: dict[str, Any]) -> dict[str, Any]:
    metadata = slot.get("metadata_json") or slot.get("metadata") or {}
    if isinstance(metadata, dict):
        return metadata
    if isinstance(metadata, str) and metadata.strip():
        try:
            parsed = json.loads(metadata)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _slot_inventory_metadata(slot: dict[str, Any]) -> dict[str, Any]:
    metadata = _slot_metadata(slot)
    slot_id = str(slot.get("slot_id") or "").strip()
    storage_slot_id = str(metadata.get("storage_slot_id") or "").strip()

    def _terminal_path(value: Any) -> str:
        return str(value or "").strip().replace("/", "\\").lower()

    def _is_shadowed_by_slot_metadata(entry: dict[str, Any]) -> bool:
        slot_terminal_path = _terminal_path(metadata.get("terminal_path"))
        entry_terminal_path = _terminal_path(entry.get("terminal_path"))
        return bool(slot_terminal_path and entry_terminal_path and slot_terminal_path != entry_terminal_path)

    direct_entry = metadata.get("slot_inventory_entry")
    if isinstance(direct_entry, dict):
        entry_slot_id = str(direct_entry.get("slot_id") or "").strip()
        entry_storage_id = str(direct_entry.get("storage_slot_id") or "").strip()
        if entry_slot_id == slot_id or (storage_slot_id and entry_storage_id == storage_slot_id):
            if _is_shadowed_by_slot_metadata(direct_entry):
                return {}
            return direct_entry
    inventory = metadata.get("slot_inventory")
    if not isinstance(inventory, list):
        return {}
    for item in inventory:
        if not isinstance(item, dict):
            continue
        item_slot_id = str(item.get("slot_id") or "").strip()
        item_storage_id = str(item.get("storage_slot_id") or "").strip()
        if item_slot_id == slot_id or (storage_slot_id and item_storage_id == storage_slot_id):
            return item
    return {}


def _runner_metadata(slot: dict[str, Any]) -> dict[str, Any]:
    metadata = slot.get("runner_metadata_json") or slot.get("runner_metadata") or {}
    if isinstance(metadata, dict):
        return metadata
    if isinstance(metadata, str) and metadata.strip():
        try:
            parsed = json.loads(metadata)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _runner_capabilities(slot: dict[str, Any]) -> dict[str, Any]:
    capabilities = slot.get("runner_capabilities_json") or slot.get("capabilities_json") or {}
    if isinstance(capabilities, dict):
        return capabilities
    if isinstance(capabilities, str) and capabilities.strip():
        try:
            parsed = json.loads(capabilities)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _slot_reservation_account_ids(slot: dict[str, Any]) -> set[int]:
    metadata = _slot_metadata(slot)
    values = [
        slot.get("reserved_account_id"),
        metadata.get("reserved_account_id"),
    ]
    return {account_id for account_id in (_parse_account_id(value) for value in values) if account_id is not None}


def _slot_reserved_for_other_account(slot: dict[str, Any], *, account_id: int) -> bool:
    return any(reserved_account_id != int(account_id) for reserved_account_id in _slot_reservation_account_ids(slot))


def _parse_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    return None


def _parse_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _slot_sequence_number(value: Any) -> Optional[int]:
    raw = str(value or "").strip()
    if not raw:
        return None
    match = re.search(r"([0-9]+)$", raw)
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _first_int(slot: dict[str, Any], metadata: dict[str, Any], *keys: str) -> Optional[int]:
    for key in keys:
        value = slot.get(key)
        if value is None:
            value = metadata.get(key)
        parsed = _parse_int(value)
        if parsed is not None:
            return parsed
    return None


def _first_int_from_dicts(*items: dict[str, Any], keys: tuple[str, ...]) -> Optional[int]:
    for item in items:
        for key in keys:
            parsed = _parse_int((item or {}).get(key))
            if parsed is not None:
                return parsed
    return None


def _cold_start_slot_can_start(source: dict[str, Any]) -> bool:
    if not isinstance(source, dict):
        return False
    requires_ipc = _parse_bool(source.get("requires_ipc_ready_before_start"))
    if requires_ipc is not False:
        return False
    start_eligible = _parse_bool(source.get("start_eligible"))
    available = _parse_bool(source.get("available_for_new_account"))
    status = str(
        source.get("status")
        or source.get("slot_status")
        or source.get("current_control_plane_state")
        or source.get("control_plane_state")
        or source.get("runner_state")
        or source.get("current_state")
        or ""
    ).strip().lower()
    return bool(start_eligible is True and available is True and status in {"", "ready", "empty", "stopped"})


def _first_bool_from_dicts(*items: dict[str, Any], keys: tuple[str, ...]) -> Optional[bool]:
    for item in items:
        for key in keys:
            source = item or {}
            if key not in source:
                continue
            parsed = _parse_bool(source.get(key))
            if parsed is not None:
                return parsed
    return None


def _first_value_from_dicts(*items: dict[str, Any], keys: tuple[str, ...]) -> Any:
    ipc_check = any("ipc" in str(key or "").lower() for key in keys)
    for item in items:
        source = item or {}
        if ipc_check and _cold_start_slot_can_start(source):
            return True
        for key in keys:
            if key in source and source.get(key) not in (None, ""):
                return source.get(key)
    return None


def _timestamp_is_stale(value: Any, *, now_dt: datetime, ttl_sec: int) -> bool:
    dt = _parse_dt(value)
    if dt is None:
        return False
    return dt < (now_dt - timedelta(seconds=max(30, int(ttl_sec or 0))))


def _timestamp_is_future(value: Any, *, now_dt: datetime) -> bool:
    dt = _parse_dt(value)
    return bool(dt is not None and dt > now_dt)


def _normalized_text_values(value: Any) -> set[str]:
    values: set[str] = set()
    if value is None:
        return values
    if isinstance(value, dict):
        for key in ("bot_id", "bot_code", "bot_name", "name", "code"):
            raw = str(value.get(key) or "").strip().lower()
            if raw:
                values.add(raw)
        for key in ("bots", "bot_codes", "available_bots", "available_bot_names", "supported_bots"):
            values.update(_normalized_text_values(value.get(key)))
        return values
    if isinstance(value, (list, tuple, set)):
        for item in value:
            values.update(_normalized_text_values(item))
        return values
    raw = str(value or "").strip().lower()
    if raw:
        values.add(raw)
    return values


def _runner_supported_bot_values(slot: dict[str, Any]) -> tuple[bool, set[str]]:
    metadata = _runner_metadata(slot)
    capabilities = _runner_capabilities(slot)
    slot_metadata = _slot_metadata(slot)
    supported: set[str] = set()
    has_signal = False

    for source in (slot, slot_metadata, metadata, capabilities):
        for key in ("available_bots", "available_bot_names", "supported_bots"):
            if key in source:
                has_signal = True
                supported.update(_normalized_text_values(source.get(key)))
        if "bot_catalog" in source:
            has_signal = True
            supported.update(_normalized_text_values(source.get("bot_catalog")))

    return has_signal, supported


def _runner_supports_requested_bot(slot: dict[str, Any], bot: dict[str, Any]) -> bool:
    requested = _normalized_text_values(
        {
            "bot_id": bot.get("bot_id"),
            "bot_code": bot.get("bot_code"),
            "bot_name": bot.get("bot_name") or bot.get("name"),
        }
    )
    if not requested:
        return True
    has_signal, supported = _runner_supported_bot_values(slot)
    if not has_signal:
        return True
    return bool(supported.intersection(requested))


def _runner_queue_block_reason(slot: dict[str, Any]) -> Optional[str]:
    metadata = _runner_metadata(slot)
    capabilities = _runner_capabilities(slot)
    if _parse_bool(slot.get("runner_queue_backlog") or metadata.get("runner_queue_backlog") or capabilities.get("runner_queue_backlog")) is True:
        return "runner_queue_backlog"

    threshold = _first_int_from_dicts(
        slot,
        metadata,
        capabilities,
        keys=("runner_queue_backlog_threshold", "queue_backlog_threshold"),
    )
    if threshold is None:
        return None

    for source in (slot, metadata, capabilities):
        depth = _first_int_from_dicts(
            source,
            keys=(
                "runner_command_queue_depth",
                "command_queue_depth",
                "commands_queue_depth",
                "runner_commands_queue_depth",
                "runner_login_slot_queue_depth",
                "login_slot_queue_depth",
            ),
        )
        if depth is not None and depth > max(0, threshold):
            return "runner_queue_backlog"
    return None


def _runner_metadata_marks_maintenance(slot: dict[str, Any]) -> bool:
    metadata = _runner_metadata(slot)
    for key in _MAINTENANCE_BOOL_KEYS:
        if _parse_bool(metadata.get(key) or slot.get(key)) is True:
            return True
    for key in (
        "runner_state",
        "current_runner_state",
        "control_plane_state",
        "capacity_state",
        "operational_status",
        "warm_guard_state",
        "warm_pool_state",
    ):
        state = str(metadata.get(key) or slot.get(key) or "").strip().lower()
        if state in _MAINTENANCE_STATE_VALUES:
            return True
    return False


def _runner_temporary_block_reason(slot: dict[str, Any], *, now_dt: datetime) -> Optional[str]:
    metadata = _runner_metadata(slot)
    for key in ("dispatch_penalty_until", "dispatch_paused_until", "auto_throttle_until", "throttled_until"):
        if _timestamp_is_future(metadata.get(key) or slot.get(key), now_dt=now_dt):
            return "runner_temporarily_throttled"
    return None


def _runner_capacity_block_reason(slot: dict[str, Any]) -> Optional[str]:
    metadata = _runner_metadata(slot)
    capabilities = _runner_capabilities(slot)
    total_slots = _first_int(slot, metadata, "runner_total_slots", "total_slots")
    active_count = _first_int(
        slot,
        metadata,
        "runner_active_count",
        "active_count",
        "active_bots",
        "running_deployments",
    )
    active_limit = _first_int(slot, metadata, "runner_active_limit", "active_limit") or RUNNER_ACTIVE_LIMIT_DEFAULT
    active_limit = max(1, min(active_limit, RUNNER_NODE_SLOT_LIMIT_DEFAULT))
    degraded_count = _first_int(slot, metadata, "runner_degraded_slots", "degraded_slots")
    broken_count = _first_int(slot, metadata, "runner_broken_slots", "broken_slots") or 0
    healthy_slots = _first_int(slot, metadata, "runner_healthy_slots", "healthy_slots")
    accepting_new_accounts = _first_bool_from_dicts(
        slot,
        metadata,
        capabilities,
        keys=(
            "runner_accepting_new_accounts",
            "accepting_new_accounts",
            "allow_new_accounts",
            "allow_start_on_this_runner",
        ),
    )

    if active_count is not None and active_count >= max(1, active_limit):
        return "runner_full"
    if degraded_count is not None and degraded_count > 0 and accepting_new_accounts is not True:
        return "windows_runtime_unhealthy"
    if healthy_slots is not None and healthy_slots <= 0:
        return "windows_runtime_unhealthy"
    if total_slots is not None and broken_count >= max(1, total_slots):
        return "windows_runtime_unhealthy"
    return None


def _slot_within_effective_capacity(slot: dict[str, Any]) -> bool:
    metadata = _runner_metadata(slot)
    capabilities = _runner_capabilities(slot)
    scale_vps = capabilities.get("scale_vps") if isinstance(capabilities.get("scale_vps"), dict) else {}
    slot_number = _slot_sequence_number(slot.get("slot_id"))
    if slot_number is None:
        return True
    runner_total_slots = _first_int(slot, metadata, "runner_total_slots", "total_slots")
    registered_max_slots = _first_int_from_dicts(capabilities, scale_vps, keys=("max_slots", "runner_max_slots", "hard_limit"))
    if registered_max_slots is None:
        registered_max_slots = _first_int(slot, metadata, "max_slots", "runner_max_slots") if runner_total_slots is not None else None
    runtime_effective_slots = _first_int(slot, metadata, "requested_slots", "effective_slots")
    phase10_effective_slots = _parse_int(metadata.get("phase10_effective_slots"))
    limits = [RUNNER_NODE_SLOT_LIMIT_DEFAULT]
    if registered_max_slots is not None and registered_max_slots > 0:
        limits.append(registered_max_slots)
    if phase10_effective_slots is not None and phase10_effective_slots > 0:
        limits.append(phase10_effective_slots)
    if runtime_effective_slots is not None and runtime_effective_slots > 0:
        if registered_max_slots is None or runtime_effective_slots >= registered_max_slots:
            limits.append(runtime_effective_slots)
    runner_max_slots = _first_int(slot, metadata, "max_slots", "runner_max_slots")
    if runner_total_slots is not None and runner_max_slots is not None and runner_total_slots > runner_max_slots:
        limits.append(runner_max_slots)
    positive_limits = [limit for limit in limits if limit is not None and limit > 0]
    if not positive_limits:
        return True
    return slot_number <= min(positive_limits)


def _slot_metadata_marks_unavailable(
    slot: dict[str, Any],
    *,
    current_account_id: Optional[int],
    account_id: int,
    now_dt: datetime,
    same_account_sticky_slot: bool = False,
) -> bool:
    metadata = _slot_metadata(slot)
    inventory = _slot_inventory_metadata(slot)
    for key in ("auto_quarantine_until", "quarantine_until", "blocked_until", "dispatch_blocked_until"):
        if _timestamp_is_future(metadata.get(key) or inventory.get(key) or slot.get(key), now_dt=now_dt):
            return True
    reserved_for_account = int(account_id) in _slot_reservation_account_ids(slot)
    hard_unavailable_states = {
        "broken",
        "degraded",
        "disabled",
        "offline",
        "verifying",
    }
    unavailable_states = {
        "allocated",
        "broken",
        "degraded",
        "disabled",
        "offline",
        "running",
        "starting",
        "stopping",
        "verifying",
    }
    state_keys = (
        "control_plane_state",
        "current_control_plane_state",
        "runner_state",
        "current_runner_state",
        "mt5_liveness_state",
    )
    for key in state_keys:
        state = str(metadata.get(key) or inventory.get(key) or slot.get(key) or "").strip().lower()
        if state in hard_unavailable_states:
            return True
        if (
            state in unavailable_states
            and current_account_id != int(account_id)
            and not reserved_for_account
            and not same_account_sticky_slot
        ):
            return True

    available = _parse_bool(
        metadata.get(
            "available_for_new_account",
            inventory.get("available_for_new_account", slot.get("available_for_new_account")),
        )
    )
    sticky_account_id = _parse_account_id(slot.get("sticky_account_id") or metadata.get("sticky_account_id"))
    active_deployment_account_id = _parse_account_id(slot.get("active_deployment_account_id"))
    sticky_history_only = (
        sticky_account_id is not None
        and current_account_id is None
        and active_deployment_account_id is None
        and not reserved_for_account
    )
    if (
        available is False
        and current_account_id != int(account_id)
        and not reserved_for_account
        and not same_account_sticky_slot
        and not sticky_history_only
    ):
        return True

    login_slot_status = str(
        metadata.get("login_slot_status")
        or inventory.get("login_slot_status")
        or slot.get("login_slot_status")
        or ""
    ).strip().lower()
    login_slot_account_id = _parse_account_id(
        metadata.get("login_slot_account_id")
        or inventory.get("login_slot_account_id")
        or slot.get("login_slot_account_id")
    )
    if login_slot_status in {"pending", "queued", "running", "verifying", "dispatched", "verified"}:
        return login_slot_account_id is None or login_slot_account_id != int(account_id)

    return False


def _slot_start_runtime_block_reason(
    slot: dict[str, Any],
    *,
    now_dt: datetime,
    same_account_sticky_slot: bool = False,
) -> Optional[str]:
    metadata = _slot_metadata(slot)
    inventory = _slot_inventory_metadata(slot)

    start_eligible = _first_bool_from_dicts(
        slot,
        metadata,
        inventory,
        keys=("start_eligible", "can_start", "allow_start", "available_for_start"),
    )
    start_block_reason = str(
        slot.get("start_block_reason")
        or metadata.get("start_block_reason")
        or inventory.get("start_block_reason")
        or ""
    ).strip().lower()
    sticky_reservation_block = "sticky" in start_block_reason or "reserved" in start_block_reason

    if start_eligible is False:
        if sticky_reservation_block and same_account_sticky_slot:
            start_eligible = None
        else:
            if "ipc" in start_block_reason:
                return "slot_not_ipc_ready"
            if "resident" in start_block_reason:
                return "slot_resident_worker_missing"
            return start_block_reason or "slot_unavailable"

    start_available = _first_bool_from_dicts(
        slot,
        metadata,
        inventory,
        keys=("available_for_new_account", "available_for_start"),
    )
    runner_reported_start_ready = bool(
        start_eligible is True
        and start_available is not False
        and not start_block_reason
    )

    requires_ipc_ready = _first_bool_from_dicts(
        slot,
        metadata,
        inventory,
        keys=("requires_ipc_ready_before_start", "require_ipc_ready_for_start"),
    )
    ipc_ready = _first_bool_from_dicts(
        slot,
        metadata,
        inventory,
        keys=("ipc_ready", "python_ipc_ready", "mt5_ipc_ready"),
    )
    # Some Windows runners can cold-start MT5 from an empty slot. In that mode
    # the slot is intentionally not IPC-ready yet, but the runner marks it as
    # start_eligible/available and will create the runtime after START_BOT.
    if requires_ipc_ready is True and ipc_ready is False and not runner_reported_start_ready:
        return "slot_not_ipc_ready"
    if requires_ipc_ready is True and ipc_ready is True:
        ipc_health_at = _first_value_from_dicts(
            metadata,
            inventory,
            slot,
            keys=("last_ipc_healthcheck_at", "ipc_ready_at", "mt5_last_healthy_at"),
        )
        if _timestamp_is_stale(
            ipc_health_at,
            now_dt=now_dt,
            ttl_sec=IPC_READY_FRESHNESS_SEC_DEFAULT,
        ) and not runner_reported_start_ready:
            return "slot_not_ipc_ready"

    resident_required = _first_bool_from_dicts(
        slot,
        metadata,
        inventory,
        keys=(
            "requires_resident_worker_certification",
            "resident_worker_required",
            "require_resident_worker_for_start",
        ),
    )
    if resident_required is True:
        resident_owner = _first_bool_from_dicts(
            slot,
            metadata,
            inventory,
            keys=("resident_worker_owner",),
        )
        resident_pid = _first_int_from_dicts(
            slot,
            metadata,
            inventory,
            keys=("resident_worker_pid",),
        )
        if resident_owner is False or resident_pid is not None and resident_pid <= 0:
            return "slot_resident_worker_missing"

    resident_owner = _first_bool_from_dicts(
        slot,
        metadata,
        inventory,
        keys=("resident_worker_owner",),
    )
    resident_pid = _first_int_from_dicts(
        slot,
        metadata,
        inventory,
        keys=("resident_worker_pid",),
    )
    if resident_required is True or resident_owner is True or resident_pid is not None and resident_pid > 0:
        resident_heartbeat_at = _first_value_from_dicts(
            metadata,
            inventory,
            slot,
            keys=("resident_worker_heartbeat_at", "resident_worker_last_heartbeat_at"),
        )
        if _timestamp_is_stale(
            resident_heartbeat_at,
            now_dt=now_dt,
            ttl_sec=RESIDENT_WORKER_FRESHNESS_SEC_DEFAULT,
        ) and not runner_reported_start_ready:
            return "slot_not_ipc_ready"

    if start_block_reason:
        if sticky_reservation_block and same_account_sticky_slot:
            return None
        if "ipc" in start_block_reason:
            return "slot_not_ipc_ready"
        if "resident" in start_block_reason:
            return "slot_resident_worker_missing"
        return start_block_reason

    return None


def _slot_retry_penalty(slot: dict[str, Any]) -> int:
    metadata = _slot_metadata(slot)
    inventory = _slot_inventory_metadata(slot)
    failure_count = (
        _first_int_from_dicts(metadata, inventory, slot, keys=("login_failure_count", "failure_count"))
        or 0
    )
    login_slot_status = str(
        metadata.get("login_slot_status")
        or inventory.get("login_slot_status")
        or slot.get("login_slot_status")
        or ""
    ).strip().lower()
    last_reason = str(metadata.get("last_reason") or inventory.get("last_reason") or slot.get("last_reason") or "").strip().lower()
    last_error = str(metadata.get("last_error") or inventory.get("last_error") or slot.get("last_error") or "").strip().lower()
    if login_slot_status in {"failed", "timeout"}:
        failure_count = max(1, failure_count)
    if "login_slot_failed" in last_reason or "login" in last_error:
        failure_count = max(1, failure_count)
    return max(0, failure_count)


def _has_sticky_binding(sticky_binding: Optional[dict[str, Any]]) -> bool:
    if not sticky_binding:
        return False
    return bool(str(sticky_binding.get("runner_id") or "").strip() and str(sticky_binding.get("slot_id") or "").strip())


def _slot_matches_sticky_binding(slot: dict[str, Any], sticky_binding: Optional[dict[str, Any]], *, account_id: int) -> bool:
    if not _has_sticky_binding(sticky_binding):
        return False
    binding_account_id = _parse_account_id((sticky_binding or {}).get("account_id"))
    if binding_account_id is not None and binding_account_id != int(account_id):
        return False
    return (
        str(slot.get("runner_id") or "").strip() == str((sticky_binding or {}).get("runner_id") or "").strip()
        and str(slot.get("slot_id") or "").strip() == str((sticky_binding or {}).get("slot_id") or "").strip()
    )


def _slot_sticky_history_penalty(slot: dict[str, Any], *, account_id: int) -> int:
    metadata = _slot_metadata(slot)
    sticky_account_id = _parse_account_id(slot.get("sticky_account_id") or metadata.get("sticky_account_id"))
    if sticky_account_id is None or sticky_account_id == int(account_id):
        return 0
    current_account_id = _parse_account_id(slot.get("current_account_id"))
    active_deployment_account_id = _parse_account_id(slot.get("active_deployment_account_id"))
    if current_account_id is not None or active_deployment_account_id is not None:
        return 0
    return 1


def _slot_block_reason(
    slot: dict[str, Any],
    *,
    account_id: int,
    bot: dict[str, Any],
    requested_profile: str,
    dedicated_required: bool,
    now_dt: datetime,
    heartbeat_ttl_sec: int,
    same_account_sticky_slot: bool = False,
) -> Optional[str]:
    if not _slot_within_effective_capacity(slot):
        return "slot_unavailable"

    def _runner_fresh() -> bool:
        slot_dt = _parse_dt(slot.get("last_heartbeat_at"))
        runner_dt = _parse_dt(slot.get("runner_last_heartbeat_at"))
        dt = max((candidate for candidate in (slot_dt, runner_dt) if candidate is not None), default=None)
        if dt is None:
            return True
        return dt >= (now_dt - timedelta(seconds=max(30, heartbeat_ttl_sec)))

    runner_status = str(slot.get("runner_status") or "").strip().lower()
    temporary_runner_block = _runner_temporary_block_reason(slot, now_dt=now_dt)
    if temporary_runner_block:
        return temporary_runner_block
    if runner_status in {"draining", "maintenance"} or _runner_metadata_marks_maintenance(slot):
        capacity_reason = _runner_capacity_block_reason(slot)
        if capacity_reason == "runner_full":
            return capacity_reason
        return "mt5_runtime_maintenance"
    if runner_status == "degraded":
        return "windows_runtime_unhealthy"
    if runner_status != "online":
        return "runner_offline"
    if not _runner_fresh():
        return "runner_offline"

    capacity_reason = _runner_capacity_block_reason(slot)
    if capacity_reason:
        return capacity_reason
    queue_reason = _runner_queue_block_reason(slot)
    if queue_reason:
        return queue_reason
    if not _runner_supports_requested_bot(slot, bot):
        return "bot_not_available_on_runner"

    allowed = {str(item).strip().lower() for item in (slot.get("allowed_profile_classes") or []) if str(item).strip()}
    if allowed and requested_profile not in allowed:
        return "profile_not_supported"
    supported = {str(item).strip().lower() for item in (slot.get("supported_profiles") or []) if str(item).strip()}
    if supported and requested_profile not in supported:
        return "profile_not_supported"
    slot_status = str(slot.get("status") or "").strip().lower()
    if slot_status not in {"ready", "allocated"}:
        return "slot_not_ready"
    current_account_id = _parse_account_id(slot.get("current_account_id"))
    if current_account_id not in (None, int(account_id)):
        return "slot_busy"
    active_deployment_account_id = _parse_account_id(slot.get("active_deployment_account_id"))
    if active_deployment_account_id not in (None, int(account_id)):
        return "slot_busy"
    if _slot_reserved_for_other_account(slot, account_id=account_id):
        return "slot_reserved_for_other_account"
    if _slot_metadata_marks_unavailable(
        slot,
        current_account_id=current_account_id,
        account_id=int(account_id),
        now_dt=now_dt,
        same_account_sticky_slot=same_account_sticky_slot,
    ):
        return "slot_unavailable"
    start_runtime_reason = _slot_start_runtime_block_reason(
        slot,
        now_dt=now_dt,
        same_account_sticky_slot=same_account_sticky_slot,
    )
    if start_runtime_reason:
        return start_runtime_reason
    tags = {str(item).strip().lower() for item in (slot.get("capability_tags") or []) if str(item).strip()}
    if dedicated_required and not tags.intersection({"isolated", "dca", "heavy", "indicator"}):
        return "dedicated_runner_required"
    if requested_profile == "heavy" and not tags.intersection({"heavy", "isolated"}):
        return "heavy_profile_not_supported"
    return None


def choose_slot_for_account(
    *,
    account_id: int,
    bot: dict[str, Any],
    slots: list[dict[str, Any]],
    sticky_binding: Optional[dict[str, Any]],
    now: Optional[datetime] = None,
    heartbeat_ttl_sec: int = 120,
) -> SchedulerDecision:
    ranked = rank_slots_for_account(
        account_id=account_id,
        bot=bot,
        slots=slots,
        sticky_binding=sticky_binding,
        now=now,
        heartbeat_ttl_sec=heartbeat_ttl_sec,
    )
    if ranked:
        return ranked[0]
    if _has_sticky_binding(sticky_binding):
        sticky_runner = str((sticky_binding or {}).get("runner_id") or "").strip()
        sticky_slot = str((sticky_binding or {}).get("slot_id") or "").strip()
        requested_profile = str(bot.get("profile_class") or "normal").strip().lower() or "normal"
        dedicated_required = requires_dedicated_runner(bot)
        now_dt = now or datetime.now(timezone.utc)
        for slot in slots:
            if str(slot.get("runner_id") or "").strip() == sticky_runner and str(slot.get("slot_id") or "").strip() == sticky_slot:
                reason = _slot_block_reason(
                    slot,
                    account_id=account_id,
                    bot=bot,
                    requested_profile=requested_profile,
                    dedicated_required=dedicated_required,
                    now_dt=now_dt,
                    heartbeat_ttl_sec=heartbeat_ttl_sec,
                    same_account_sticky_slot=_slot_matches_sticky_binding(
                        slot,
                        sticky_binding,
                        account_id=account_id,
                    ),
                )
                if reason in {
                    "mt5_runtime_maintenance",
                    "windows_runtime_unhealthy",
                    "slot_not_ipc_ready",
                    "slot_resident_worker_missing",
                    "runner_full",
                    "runner_queue_backlog",
                    "bot_not_available_on_runner",
                }:
                    return SchedulerDecision(ok=False, reason=reason)
                break
        return SchedulerDecision(ok=False, reason="sticky_slot_unavailable")
    block_reasons = {
        reason
        for slot in slots
        for reason in [
            _slot_block_reason(
                slot,
                account_id=account_id,
                bot=bot,
                requested_profile=str(bot.get("profile_class") or "normal").strip().lower() or "normal",
                dedicated_required=requires_dedicated_runner(bot),
                now_dt=now or datetime.now(timezone.utc),
                heartbeat_ttl_sec=heartbeat_ttl_sec,
            )
        ]
        if reason
    }
    for reason in (
        "mt5_runtime_maintenance",
        "windows_runtime_unhealthy",
        "slot_not_ipc_ready",
        "slot_resident_worker_missing",
        "runner_full",
        "runner_queue_backlog",
        "runner_offline",
        "bot_not_available_on_runner",
    ):
        if reason in block_reasons:
            return SchedulerDecision(ok=False, reason=reason)
    if any(_slot_reserved_for_other_account(slot, account_id=account_id) for slot in slots):
        return SchedulerDecision(ok=False, reason="no_available_unreserved_slot")
    return SchedulerDecision(ok=False, reason="no_healthy_slot_available")


def rank_slots_for_account(
    *,
    account_id: int,
    bot: dict[str, Any],
    slots: list[dict[str, Any]],
    sticky_binding: Optional[dict[str, Any]],
    now: Optional[datetime] = None,
    heartbeat_ttl_sec: int = 120,
) -> list[SchedulerDecision]:
    now_dt = now or datetime.now(timezone.utc)
    requested_profile = str(bot.get("profile_class") or "normal").strip().lower() or "normal"
    dedicated_required = requires_dedicated_runner(bot)

    def _supports(slot: dict[str, Any]) -> bool:
        return _slot_block_reason(
            slot,
            account_id=account_id,
            bot=bot,
            requested_profile=requested_profile,
            dedicated_required=dedicated_required,
            now_dt=now_dt,
            heartbeat_ttl_sec=heartbeat_ttl_sec,
            same_account_sticky_slot=_slot_matches_sticky_binding(
                slot,
                sticky_binding,
                account_id=account_id,
            ),
        ) is None

    ranked: list[SchedulerDecision] = []
    seen: set[tuple[str, str]] = set()

    if sticky_binding:
        sticky_runner = str(sticky_binding.get("runner_id") or "").strip()
        sticky_slot = str(sticky_binding.get("slot_id") or "").strip()
        for slot in slots:
            if str(slot.get("runner_id") or "").strip() == sticky_runner and str(slot.get("slot_id") or "").strip() == sticky_slot:
                if _supports(slot):
                    ranked.append(
                        SchedulerDecision(
                            ok=True,
                            runner_id=sticky_runner,
                            slot_id=sticky_slot,
                            reason="sticky_slot_reused",
                            sticky_reused=True,
                        )
                    )
                    seen.add((sticky_runner, sticky_slot))
                break
        if ranked:
            return ranked

    candidate_slots: list[dict[str, Any]] = []
    for slot in slots:
        if not _supports(slot):
            continue
        candidate_slots.append(slot)

    free_by_runner: dict[str, int] = {}
    for slot in candidate_slots:
        runner_id = str(slot.get("runner_id") or "")
        free_by_runner[runner_id] = free_by_runner.get(runner_id, 0) + 1

    candidates: list[tuple[int, float, int, int, str, str]] = []
    for slot in candidate_slots:
        runner_id = str(slot.get("runner_id") or "")
        total_slots = max(1, min(int(slot.get("max_slots") or 1), RUNNER_NODE_SLOT_LIMIT_DEFAULT))
        active_count = _parse_int(slot.get("runner_active_count"))
        if active_count is None:
            active_count = 1 if slot.get("current_account_id") is not None else 0
        load_score = max(0, active_count) / total_slots
        candidates.append(
            (
                -free_by_runner.get(runner_id, 0),
                load_score,
                _slot_sticky_history_penalty(slot, account_id=account_id),
                _slot_retry_penalty(slot),
                runner_id,
                str(slot.get("slot_id") or ""),
            )
        )

    candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3], item[4], item[5]))
    for _, _, _, _, runner_id, slot_id in candidates:
        key = (runner_id, slot_id)
        if key in seen:
            continue
        ranked.append(
            SchedulerDecision(
                ok=True,
                runner_id=runner_id,
                slot_id=slot_id,
                reason="selected_best_available_slot",
            )
        )
        seen.add(key)
    return ranked


def preview_slots_for_account(
    *,
    account_id: int,
    bot: dict[str, Any],
    slots: list[dict[str, Any]],
    sticky_binding: Optional[dict[str, Any]],
    now: Optional[datetime] = None,
    heartbeat_ttl_sec: int = 120,
) -> dict[str, Any]:
    """Read-only scheduler preview for ops/UI before dispatching work."""
    now_dt = now or datetime.now(timezone.utc)
    requested_profile = str(bot.get("profile_class") or "normal").strip().lower() or "normal"
    dedicated_required = requires_dedicated_runner(bot)
    ranked = rank_slots_for_account(
        account_id=account_id,
        bot=bot,
        slots=slots,
        sticky_binding=sticky_binding,
        now=now_dt,
        heartbeat_ttl_sec=heartbeat_ttl_sec,
    )
    selected = ranked[0] if ranked else choose_slot_for_account(
        account_id=account_id,
        bot=bot,
        slots=slots,
        sticky_binding=sticky_binding,
        now=now_dt,
        heartbeat_ttl_sec=heartbeat_ttl_sec,
    )
    blocked_slots: list[dict[str, Any]] = []
    for slot in slots:
        reason = _slot_block_reason(
            slot,
            account_id=account_id,
            bot=bot,
            requested_profile=requested_profile,
            dedicated_required=dedicated_required,
            now_dt=now_dt,
            heartbeat_ttl_sec=heartbeat_ttl_sec,
            same_account_sticky_slot=_slot_matches_sticky_binding(
                slot,
                sticky_binding,
                account_id=account_id,
            ),
        )
        if reason:
            blocked_slots.append(
                {
                    "runner_id": str(slot.get("runner_id") or ""),
                    "slot_id": str(slot.get("slot_id") or ""),
                    "reason": reason,
                }
            )
    blocked_reasons = sorted({str(item.get("reason") or "") for item in blocked_slots if str(item.get("reason") or "")})
    if not selected.ok and selected.reason and selected.reason not in blocked_reasons:
        blocked_reasons.insert(0, selected.reason)
    return {
        "ok": bool(selected.ok),
        "selected": _decision_payload(selected),
        "candidates": [_decision_payload(item) for item in ranked],
        "blocked_reasons": blocked_reasons,
        "blocked_slots": blocked_slots,
    }
