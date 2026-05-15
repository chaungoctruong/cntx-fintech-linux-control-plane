from __future__ import annotations

"""Shared helpers and constants for control-plane persistence."""

import json
import re
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from app.models.control_plane import ACTIVE_DEPLOYMENT_STATUSES

RUNNER_NODE_SLOT_LIMIT_DEFAULT = 10
RUNNER_ACTIVE_LIMIT_DEFAULT = RUNNER_NODE_SLOT_LIMIT_DEFAULT
RUNNER_MIN_HEALTHY_SLOTS_DEFAULT = RUNNER_NODE_SLOT_LIMIT_DEFAULT

_ACTIVE_RUNNER_DEPLOYMENT_STATUSES = ("start_requested", "starting", "running", "stop_requested")
_TERMINAL_DEPLOYMENT_STATUSES = ("stopped", "failed", "blocked")
_COMMAND_DELIVERY_REPLAY_ADVISORY_LOCK_ID = 5_702_301


def _cap_runner_slots(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except Exception:
        parsed = 0
    return max(1, min(parsed or RUNNER_NODE_SLOT_LIMIT_DEFAULT, RUNNER_NODE_SLOT_LIMIT_DEFAULT))

_LOGIN_FAILURE_DEFAULTS: dict[str, dict[str, Any]] = {
    "INVALID_CREDENTIALS": {
        "retryable": False,
        "failure_kind": "credential",
        "failure_category": "credential_failure",
        "user_message_key": "mt5_invalid_credentials",
    },
    "INVALID_PASSWORD": {
        "retryable": False,
        "failure_kind": "credential",
        "failure_category": "credential_failure",
        "user_message_key": "mt5_invalid_password",
    },
    "INVALID_SERVER": {
        "retryable": False,
        "failure_kind": "credential",
        "failure_category": "credential_failure",
        "user_message_key": "mt5_invalid_server",
    },
    "ACCOUNT_NOT_FOUND": {
        "retryable": False,
        "failure_kind": "credential",
        "failure_category": "credential_failure",
        "user_message_key": "mt5_account_not_found",
    },
    "TRANSIENT_MT5": {
        "retryable": True,
        "failure_kind": "mt5_temporary_unavailable",
        "failure_category": "mt5_runtime_failure",
        "user_message_key": "mt5_temporary_unavailable",
    },
    "NETWORK_TIMEOUT": {
        "retryable": True,
        "failure_kind": "mt5_temporary_unavailable",
        "failure_category": "mt5_runtime_failure",
        "user_message_key": "mt5_network_timeout_retry",
    },
    "MT5_BUSY": {
        "retryable": True,
        "failure_kind": "mt5_temporary_unavailable",
        "failure_category": "mt5_runtime_failure",
        "user_message_key": "mt5_busy_retry",
    },
    "TERMINAL_LOG_TIMEOUT": {
        "retryable": True,
        "failure_kind": "mt5_temporary_unavailable",
        "failure_category": "mt5_runtime_failure",
        "user_message_key": "mt5_terminal_log_timeout_retry",
    },
    "RUNNER_SLOT_BUSY": {
        "retryable": True,
        "failure_kind": "runner_slot_busy",
        "failure_category": "runner_slot",
        "user_message_key": "runner_slot_busy_retry",
    },
    "RUNNER_SLOT_UNAVAILABLE": {
        "retryable": True,
        "failure_kind": "runner_slot_unavailable",
        "failure_category": "runner_slot",
        "user_message_key": "runner_slot_unavailable_retry",
    },
    "MT5_LOGIN_FAILED": {
        "retryable": True,
        "failure_kind": "login_failed",
        "failure_category": "unknown",
        "user_message_key": "mt5_login_retry",
    },
}

_LOGIN_CREDENTIAL_ERROR_CODES = {
    "INVALID_CREDENTIALS",
    "INVALID_PASSWORD",
    "INVALID_SERVER",
    "ACCOUNT_NOT_FOUND",
}


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _norm_catalog_identity(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _norm_login_error_code(value: Any) -> str:
    raw = _norm(value).upper().replace("-", "_").replace(" ", "_")
    return raw


def _login_retryable(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    raw = _norm(value).lower()
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)


def _legacy_login_failure_metadata(*, error_text: Any, payload: dict[str, Any]) -> dict[str, Any]:
    payload_map = payload if isinstance(payload, dict) else {}
    text = " ".join(
        [
            _norm(error_text),
            _norm(payload_map.get("reason")),
            _norm(payload_map.get("error")),
            _norm(payload_map.get("phase")),
            _norm(payload_map.get("mt5_last_error")),
            _norm(payload_map.get("terminal_log_line")),
        ]
    ).lower()
    if not text:
        return {}
    normalized_text = text.replace("_", " ").replace("-", " ")
    reason = _norm(payload_map.get("reason")).lower()
    phase = _norm(payload_map.get("phase")).lower()
    mt5_last_error = _norm(payload_map.get("mt5_last_error")).lower()
    terminal_log_line = _norm(payload_map.get("terminal_log_line")).lower()
    auth_text = " ".join([reason, phase, mt5_last_error, terminal_log_line, _norm(error_text).lower()])
    normalized_auth_text = auth_text.replace("_", " ").replace("-", " ")

    transient_tokens = (
        "transient_mt5",
        "template_login_worker_timeout",
        "terminal_log_login_timeout",
        "template_terminal_lock_timeout",
        "mt5_initialize_failed",
        "login_subprocess_timeout",
        "login_hard_timeout",
        "interactive_login_timeout",
        "interactive_login_worker_timeout",
        "terminal_initialize_failed",
        "login_mt5_init_lock_timeout",
        "warm_attach_failed",
        "warm_attach_direct_credentials_failed",
        "broker_connection_timeout",
    )
    auth_failure_tokens = (
        "authorization failed",
        "authorization_failed",
        "auth failed",
        "auth_failed",
        "login failed",
        "invalid account",
        "unknown account",
        "account_not_found",
        "account not found",
        "wrong password",
        "bad credentials",
    )
    has_transient_token = any(token in text for token in transient_tokens)
    has_auth_failure_token = any(
        token in auth_text or token in normalized_auth_text for token in auth_failure_tokens
    )
    login_returned_false_with_auth_log = "login_returned_false" in auth_text and any(
        token in terminal_log_line or token in terminal_log_line.replace("_", " ").replace("-", " ")
        for token in ("authorization failed", "authorization_failed", "invalid account", "login failed")
    )
    mt5_login_failed_with_auth_error = phase == "mt5_login_failed" and (
        has_auth_failure_token
        or any(
            token in mt5_last_error or token in mt5_last_error.replace("_", " ").replace("-", " ")
            for token in ("auth", "login", "password", "invalid server", "server not found", "unknown server")
        )
    )
    explicit_auth_reason = any(
        token in auth_text
        for token in (
            "authorization_failed",
            "auth_failed",
            "login_mismatch",
            "server_mismatch",
        )
    ) or any(token in normalized_auth_text for token in ("authorization failed", "auth failed"))
    password_credential_failure = any(
        token in auth_text or token in normalized_auth_text for token in ("wrong password", "bad credentials")
    )

    if has_transient_token or "ipc" in text:
        code = "TRANSIENT_MT5"
    elif (
        explicit_auth_reason
        or login_returned_false_with_auth_log
        or mt5_login_failed_with_auth_error
        or password_credential_failure
    ):
        code = "INVALID_CREDENTIALS"
    elif "invalid server" in text or "invalid server" in normalized_text:
        code = "INVALID_SERVER"
    elif "invalid account" in text or "account_not_found" in text or "account not found" in normalized_text:
        code = "ACCOUNT_NOT_FOUND"
    elif has_auth_failure_token:
        code = "INVALID_CREDENTIALS"
    elif "slot_busy:" in text:
        code = "RUNNER_SLOT_BUSY"
    elif "slot_unhealthy" in text or "no_healthy_slot" in text or "slot_unavailable" in text:
        code = "RUNNER_SLOT_UNAVAILABLE"
    elif any(
        token in text
        for token in (
            "login_terminal_silent",
            "account_info_none",
            "account_info_failed",
        )
    ):
        code = "TRANSIENT_MT5"
    else:
        code = "MT5_LOGIN_FAILED"
    defaults = _LOGIN_FAILURE_DEFAULTS[code]
    return {"error_code": code, **defaults}


def _login_failure_metadata(*, error_text: Any, payload: Any) -> dict[str, Any]:
    payload_map = payload if isinstance(payload, dict) else {}
    code = _norm_login_error_code(payload_map.get("error_code"))
    if not code:
        return _legacy_login_failure_metadata(error_text=error_text, payload=payload_map)

    defaults = dict(
        _LOGIN_FAILURE_DEFAULTS.get(
            code,
            {
                "retryable": True,
                "failure_kind": "login_failed",
                "failure_category": "unknown",
                "user_message_key": "mt5_login_retry",
            },
        )
    )
    return {
        "error_code": code,
        "retryable": _login_retryable(payload_map.get("retryable"), default=bool(defaults["retryable"])),
        "failure_kind": _norm(payload_map.get("failure_kind")) or str(defaults["failure_kind"]),
        "failure_category": _norm(payload_map.get("failure_category")) or str(defaults["failure_category"]),
        "user_message_key": _norm(payload_map.get("user_message_key")) or str(defaults["user_message_key"]),
    }


def _norm_slot_id(value: Any) -> str:
    raw = _norm(value)
    if not raw:
        return ""
    lowered = raw.lower()
    if lowered.startswith("slot_") or lowered.startswith("slot-"):
        return f"slot-{raw[5:]}"
    return raw


def _slot_sequence_number(value: Any) -> Optional[int]:
    raw = _norm(value)
    if not raw:
        return None
    match = re.search(r"([0-9]+)$", raw)
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.isoformat()
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, set):
        return sorted(value, key=str)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _json_payload(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, separators=(",", ":"), default=_json_default)


def _json_list(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        raw = [item for item in value]
    else:
        raw = []
    return json.dumps(raw, ensure_ascii=False, separators=(",", ":"), default=_json_default)


def _epoch_now() -> int:
    return int(time.time())


def _parse_bool(value: Any) -> bool:
    raw = _norm(value).lower()
    return raw in {"1", "true", "yes", "y", "on"}


def _metadata_flag(metadata: Any, *keys: str) -> bool:
    payload = metadata if isinstance(metadata, dict) else {}
    for key in keys:
        if _parse_bool(payload.get(key)):
            return True
    return False


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _json_loads_if_string(value: Any, default: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _readiness_text_values(value: Any) -> set[str]:
    values: set[str] = set()
    if value is None:
        return values
    if isinstance(value, dict):
        for key in ("bot_id", "bot_code", "bot_name", "name", "code"):
            raw = _norm(value.get(key))
            if raw:
                values.add(raw)
        for key in ("bots", "bot_codes", "available_bots", "available_bot_names", "supported_bots"):
            values.update(_readiness_text_values(value.get(key)))
        return values
    if isinstance(value, (list, tuple, set)):
        for item in value:
            values.update(_readiness_text_values(item))
        return values
    raw = _norm(value)
    if raw:
        values.add(raw)
    return values


def _runner_readiness_bot_codes(*sources: Any) -> list[str]:
    values: set[str] = set()
    for source in sources:
        data = _json_dict(source)
        for key in ("available_bots", "available_bot_names", "supported_bots"):
            values.update(_readiness_text_values(data.get(key)))
        values.update(_readiness_text_values(data.get("bot_catalog")))
    return sorted(values)


def _extract_runner_heartbeat_capacity(
    *,
    current_max_slots: int,
    existing_metadata: Optional[dict[str, Any]],
    payload: Optional[dict[str, Any]],
) -> int:
    existing = existing_metadata if isinstance(existing_metadata, dict) else {}
    heartbeat = payload if isinstance(payload, dict) else {}
    scale_vps = heartbeat.get("scale_vps") if isinstance(heartbeat.get("scale_vps"), dict) else {}

    selected_slots = heartbeat.get("selected_slots")
    selected_slots_count = len(selected_slots) if isinstance(selected_slots, list) else 0
    disabled_slots = heartbeat.get("disabled_slots")
    disabled_slots_count = len(disabled_slots) if isinstance(disabled_slots, list) else 0

    hard_candidates = [
        _safe_int(scale_vps.get("hard_limit"), 0),
        _safe_int(scale_vps.get("max_slots"), 0),
        _safe_int(heartbeat.get("max_slots"), 0),
        _safe_int(heartbeat.get("runner_max_slots"), 0),
        int(current_max_slots or 0),
    ]
    hard_positive = [candidate for candidate in hard_candidates if candidate > 0]
    if hard_positive:
        capacity = min(max(hard_positive), RUNNER_NODE_SLOT_LIMIT_DEFAULT)
        slots_total = _safe_int(heartbeat.get("slots_total"), 0)
        if disabled_slots_count > 0 and slots_total >= capacity:
            capacity = max(1, slots_total - disabled_slots_count)
        return _cap_runner_slots(capacity)

    configured_candidates = [
        _safe_int(heartbeat.get("requested_slots"), 0),
        _safe_int(heartbeat.get("effective_slots"), 0),
        _safe_int(heartbeat.get("phase10_effective_slots"), 0),
        _safe_int(heartbeat.get("configured_slots"), 0),
        _safe_int(heartbeat.get("slots_configured"), 0),
        _safe_int(heartbeat.get("slots_total"), 0),
    ]
    if selected_slots_count > 0:
        configured_candidates.append(selected_slots_count)
    if disabled_slots_count > 0:
        configured_candidates.append(max(0, _safe_int(heartbeat.get("slots_total"), 0) - disabled_slots_count))

    positive = [candidate for candidate in configured_candidates if candidate > 0]
    if not positive:
        fallback_candidates = [
            _safe_int(existing.get("requested_slots"), 0),
            _safe_int(existing.get("effective_slots"), 0),
            int(current_max_slots or 0),
        ]
        positive = [candidate for candidate in fallback_candidates if candidate > 0]
    if not positive:
        return 1
    if int(current_max_slots or 0) > 0:
        positive.append(_cap_runner_slots(current_max_slots))
    return _cap_runner_slots(min(positive))


def _build_runner_heartbeat_metadata(
    *,
    current_max_slots: int,
    existing_metadata: Optional[dict[str, Any]],
    payload: Optional[dict[str, Any]],
) -> dict[str, Any]:
    existing = dict(existing_metadata or {})
    heartbeat = payload if isinstance(payload, dict) else {}
    scale_vps = heartbeat.get("scale_vps") if isinstance(heartbeat.get("scale_vps"), dict) else {}
    effective_slots = _extract_runner_heartbeat_capacity(
        current_max_slots=current_max_slots,
        existing_metadata=existing,
        payload=heartbeat,
    )

    metadata = dict(existing)
    metadata["requested_slots"] = effective_slots
    metadata["effective_slots"] = effective_slots
    metadata["max_slots"] = effective_slots
    active_limit_signal = (
        _safe_int(heartbeat.get("active_limit"), 0)
        or _safe_int(scale_vps.get("active_limit"), 0)
        or _safe_int(scale_vps.get("hard_limit"), 0)
        or _safe_int(scale_vps.get("max_slots"), 0)
        or effective_slots
    )
    metadata["active_limit"] = _cap_runner_slots(min(active_limit_signal, effective_slots))
    metadata["min_healthy_slots"] = max(1, min(effective_slots, _safe_int(existing.get("min_healthy_slots"), effective_slots) or effective_slots))

    for key in (
        "reason",
        "runner_state",
        "paused",
        "frozen",
        "maintenance",
        "maintenance_mode",
        "dispatch_paused",
        "login_paused",
        "warm_guard_paused",
        "warm_guard_pause",
        "warm_pool_paused",
    ):
        value = heartbeat.get(key)
        if value is None:
            value = scale_vps.get(key)
        if value is not None:
            metadata[key] = value

    if "accepting_new_accounts" in scale_vps:
        metadata["accepting_new_accounts"] = scale_vps.get("accepting_new_accounts")
    if "allow_start_on_this_runner" in scale_vps:
        metadata["allow_start_on_this_runner"] = scale_vps.get("allow_start_on_this_runner")

    slot_count_aliases = (
        (("reported_slots_total",), ("slots_total",)),
        (("reported_slots_ready", "reported_ready_slots"), ("ready_slots", "slots_ready")),
        (("reported_slots_active", "reported_active_slots"), ("active_slots", "slots_active")),
        (("reported_slots_broken", "reported_broken_slots"), ("broken_slots", "slots_broken")),
        (("reported_slots_degraded", "reported_degraded_slots"), ("degraded_slots", "slots_degraded")),
        (("reported_slots_login_reserved", "reported_login_reserved_slots"), ("login_reserved_slots", "slots_login_reserved")),
    )
    for output_keys, payload_keys in slot_count_aliases:
        value = next((heartbeat.get(key) for key in payload_keys if heartbeat.get(key) is not None), None)
        if value is None:
            continue
        for output_key in output_keys:
            metadata[output_key] = value

    mt5_recovery = heartbeat.get("mt5_recovery") if isinstance(heartbeat.get("mt5_recovery"), dict) else {}
    if "reported_degraded_slots" not in metadata and "degraded_slots" in mt5_recovery:
        metadata["reported_degraded_slots"] = mt5_recovery.get("degraded_slots")
    if "problem_slots" in mt5_recovery:
        metadata["reported_problem_slots"] = mt5_recovery.get("problem_slots")

    if heartbeat.get("last_error") is not None:
        metadata["last_error"] = heartbeat.get("last_error")
    if heartbeat.get("runtime_error") is not None:
        metadata["runtime_error"] = heartbeat.get("runtime_error")
    for key in ("available_bots", "available_bot_names", "supported_bots", "bot_catalog"):
        if key in heartbeat:
            metadata[key] = heartbeat.get(key)
    return metadata


def _overlay_runner_slot_projection_metadata(
    metadata: Optional[dict[str, Any]],
    slot_counts: Optional[dict[str, Any]],
) -> dict[str, Any]:
    merged = dict(metadata or {})
    counts = slot_counts if isinstance(slot_counts, dict) else {}
    total_slots = _safe_int(counts.get("total_slots"), 0)
    if total_slots <= 0:
        return merged

    ready_slots = _safe_int(counts.get("ready_slots"), 0)
    allocated_slots = _safe_int(counts.get("allocated_slots"), 0)
    degraded_slots = _safe_int(counts.get("degraded_slots"), 0)
    broken_slots = _safe_int(counts.get("broken_slots"), 0)
    login_reserved_slots = _safe_int(counts.get("login_reserved_slots"), 0)
    canonical_counts = {
        "reported_slots_total": total_slots,
        "reported_slots_ready": ready_slots,
        "reported_ready_slots": ready_slots,
        "reported_slots_active": allocated_slots,
        "reported_active_slots": allocated_slots,
        "reported_slots_degraded": degraded_slots,
        "reported_degraded_slots": degraded_slots,
        "reported_slots_broken": broken_slots,
        "reported_broken_slots": broken_slots,
        "reported_slots_login_reserved": login_reserved_slots,
        "reported_login_reserved_slots": login_reserved_slots,
    }
    merged.update(canonical_counts)
    return _clear_resolved_runner_failure_metadata(merged)


def _clear_resolved_runner_failure_metadata(metadata: Optional[dict[str, Any]]) -> dict[str, Any]:
    merged = dict(metadata or {})
    degraded_slots = _safe_int(
        merged.get("reported_slots_degraded", merged.get("reported_degraded_slots")),
        0,
    )
    broken_slots = _safe_int(
        merged.get("reported_slots_broken", merged.get("reported_broken_slots")),
        0,
    )
    ready_slots = _safe_int(
        merged.get("reported_slots_ready", merged.get("reported_ready_slots")),
        0,
    )
    active_slots = _safe_int(
        merged.get("reported_slots_active", merged.get("reported_active_slots")),
        0,
    )
    runner_state = _norm(merged.get("runner_state")).lower()
    unhealthy_state = runner_state in {
        "broken",
        "degraded",
        "draining",
        "frozen",
        "maintenance",
        "offline",
        "paused",
        "unhealthy",
    }
    penalty_until = _parse_projection_timestamp(merged.get("dispatch_penalty_until"))
    penalty_active = penalty_until > time.time()
    if (
        not penalty_active
        and not unhealthy_state
        and degraded_slots <= 0
        and broken_slots <= 0
        and (ready_slots + active_slots) > 0
    ):
        for key in (
            "dispatch_penalty_until",
            "last_start_failure_at",
            "last_start_failure_reason",
            "start_failure_recent_count",
        ):
            merged.pop(key, None)
    return merged


def _parse_projection_timestamp(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        raw = float(value)
        return raw / 1000.0 if raw > 10_000_000_000 else raw
    raw = _norm(value)
    if not raw:
        return 0.0
    try:
        normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return float(parsed.timestamp())
    except Exception:
        return 0.0


def _slot_projection_freshness(metadata: Optional[dict[str, Any]]) -> tuple[float, int]:
    payload = metadata if isinstance(metadata, dict) else {}
    inventory = payload.get("slot_inventory_entry") if isinstance(payload.get("slot_inventory_entry"), dict) else {}
    timestamp_candidates = [
        _parse_projection_timestamp(payload.get("event_at")),
        _parse_projection_timestamp(payload.get("heartbeat_created_at")),
        _parse_projection_timestamp(payload.get("heartbeat_received_at")),
        _parse_projection_timestamp(payload.get("observed_at_iso")),
        _parse_projection_timestamp(payload.get("observed_at")),
        _parse_projection_timestamp(payload.get("updated_at")),
        _parse_projection_timestamp(inventory.get("event_at")),
        _parse_projection_timestamp(inventory.get("heartbeat_created_at")),
        _parse_projection_timestamp(inventory.get("heartbeat_received_at")),
        _parse_projection_timestamp(inventory.get("observed_at_iso")),
        _parse_projection_timestamp(inventory.get("observed_at")),
        _parse_projection_timestamp(inventory.get("updated_at")),
    ]
    version_candidates = [
        _safe_int(payload.get("state_version"), 0),
        _safe_int(inventory.get("state_version"), 0),
    ]
    return max(timestamp_candidates or [0.0]), max(version_candidates or [0])


def _slot_registration_should_update_projection(
    *,
    existing_metadata: Optional[dict[str, Any]],
    incoming_metadata: Optional[dict[str, Any]],
) -> bool:
    existing_ts, existing_version = _slot_projection_freshness(existing_metadata)
    incoming_ts, incoming_version = _slot_projection_freshness(incoming_metadata)
    if existing_ts > 0 and incoming_ts <= 0:
        return False
    if existing_ts > 0 and incoming_ts > 0:
        if incoming_ts < existing_ts:
            return False
        if incoming_ts == existing_ts and incoming_version < existing_version:
            return False
    return True


def _normalize_runner_status_for_db(value: Any) -> str:
    raw = _norm(value).lower()
    if raw in {"offline", "stale"}:
        return "offline"
    if raw in {"degraded", "unhealthy"}:
        return "degraded"
    if raw in {"draining", "maintenance", "paused", "frozen", "freeze", "warm_guard_paused"}:
        return "draining"
    return "online"


def _runner_heartbeat_allows_online_status(metadata: dict[str, Any]) -> bool:
    if _metadata_flag(
        metadata,
        "maintenance_mode",
        "maintenance",
        "paused",
        "pause",
        "frozen",
        "freeze",
        "dispatch_paused",
        "login_paused",
        "warm_guard_paused",
        "warm_guard_pause",
        "warm_pool_paused",
    ):
        return False
    runner_state = _norm(metadata.get("runner_state")).lower()
    return runner_state not in {"draining", "frozen", "maintenance", "paused", "login_paused", "warm_guard_paused"}


def _runner_operational_status(row: dict[str, Any]) -> str:
    metadata = row.get("metadata_json") if isinstance(row.get("metadata_json"), dict) else {}
    status = _norm(row.get("status")).lower()
    is_stale = bool(row.get("is_stale"))
    total_slots = _safe_int(row.get("total_slots"))
    healthy_slots = _safe_int(row.get("healthy_slots"))
    available_slots = _safe_int(row.get("available_slots"))
    allocated_slots = _safe_int(row.get("allocated_slots"))
    active_count = _safe_int(row.get("running_deployments"))
    degraded_slots = _safe_int(row.get("degraded_slots"))
    broken_slots = _safe_int(row.get("broken_slots"))
    active_limit = _cap_runner_slots(
        _safe_int(metadata.get("active_limit"), RUNNER_ACTIVE_LIMIT_DEFAULT) or RUNNER_ACTIVE_LIMIT_DEFAULT
    )
    min_healthy = _safe_int(metadata.get("min_healthy_slots"), RUNNER_MIN_HEALTHY_SLOTS_DEFAULT) or RUNNER_MIN_HEALTHY_SLOTS_DEFAULT

    if status == "offline" or is_stale:
        return "OFFLINE"
    if status == "draining" or _metadata_flag(
        metadata,
        "maintenance_mode",
        "maintenance",
        "paused",
        "pause",
        "frozen",
        "freeze",
        "dispatch_paused",
        "login_paused",
        "warm_guard_paused",
        "warm_guard_pause",
        "warm_pool_paused",
    ):
        return "MAINTENANCE"
    if status == "degraded" or degraded_slots > 0:
        return "DEGRADED"
    if total_slots >= min_healthy and healthy_slots < min_healthy:
        return "DEGRADED"
    if total_slots > 0 and broken_slots >= total_slots:
        return "DEGRADED"
    if active_count >= active_limit:
        return "FULL"
    near_full_threshold = max(1, active_limit - 1)
    if active_count >= near_full_threshold or (available_slots <= 1 and allocated_slots > 0):
        return "ONLINE_NEAR_FULL"
    if total_slots > 0:
        return "ONLINE_AVAILABLE"
    return "DEGRADED"


def _capacity_state_from_operational_status(value: str) -> str:
    mapping = {
        "ONLINE_AVAILABLE": "online_available",
        "ONLINE_NEAR_FULL": "online_near_full",
        "FULL": "full",
        "DEGRADED": "degraded",
        "MAINTENANCE": "maintenance",
        "OFFLINE": "offline",
    }
    return mapping.get(str(value or "").strip().upper(), "degraded")


def _derive_login_state(*, account_status: Any, reservation_status: Any) -> str:
    job_s = _norm(reservation_status).lower()
    account_s = _norm(account_status).lower()
    if job_s in {"pending", "dispatched"}:
        return "LOGIN_IN_PROGRESS"
    if job_s == "verified":
        return "READY"
    if job_s == "failed":
        return "FAILED"
    if account_s == "pending_login":
        return "LOGIN_IN_PROGRESS"
    if account_s == "connected":
        return "READY"
    if account_s == "login_failed":
        return "FAILED"
    return "UNKNOWN"


def _derive_login_ui_state(row: dict[str, Any]) -> str:
    job_s = _norm(row.get("status") or row.get("login_reservation_status")).lower()
    account_s = _norm(row.get("account_status") or row.get("status")).lower()
    runner_id = _norm(row.get("runner_id"))
    slot_id = _norm_slot_id(row.get("slot_id"))

    if job_s == "verified":
        return "READY"
    if job_s in {"failed", "cancelled"}:
        return "FAILED"
    if job_s == "dispatched":
        return "LOGIN_IN_PROGRESS"
    if job_s == "pending" and runner_id and slot_id:
        return "ASSIGNED"
    if job_s == "pending":
        return "SUBMITTED"
    if account_s == "connected":
        return "READY"
    if account_s == "login_failed":
        return "FAILED"
    if account_s == "pending_login":
        return "SUBMITTED"
    return "UNKNOWN"


def _normalize_runner_slot_projection_status(value: Any) -> str:
    raw = _norm(value).lower()
    if raw in {"ready", "allocated", "degraded", "broken", "disabled"}:
        return raw
    if raw in {"empty", "stopped"}:
        return "ready"
    if raw in {"active", "verifying", "preparing", "stopping"}:
        return "allocated"
    if raw == "rebuilding":
        return "degraded"
    return "ready"


def _slot_inventory_projection_status(entry: dict[str, Any]) -> str | None:
    """Normalize a runner heartbeat slot_inventory entry into DB slot status."""
    for key in (
        "current_control_plane_state",
        "control_plane_state",
        "new_state",
        "slot_state",
        "runner_state",
        "current_runner_state",
        "current_state",
    ):
        raw = _norm(entry.get(key)).lower()
        if not raw:
            continue
        if raw in {
            "ready",
            "empty",
            "stopped",
            "idle",
            "warm",
            "warm_idle",
            "warm-idle",
            "ipc_ready",
            "ipc-ready",
            "slot_ipc_ready",
            "terminal_ready",
            "terminal-ready",
            "bridge_ready",
            "bridge-ready",
        }:
            return "ready"
        if raw in {"allocated", "active", "running", "verifying", "preparing", "stopping"}:
            return "allocated"
        if raw in {"degraded", "rebuilding"}:
            return "degraded"
        if raw in {"broken", "disabled"}:
            return raw
    return None


def _decorate_account_login_projection(
    row: Optional[dict[str, Any]],
    *,
    account_status_key: str,
    reservation_status_key: str = "login_reservation_status",
) -> Optional[dict[str, Any]]:
    if not row:
        return row
    decorated = dict(row)
    decorated["login_state"] = _derive_login_state(
        account_status=decorated.get(account_status_key),
        reservation_status=decorated.get(reservation_status_key),
    )
    decorated["login_ui_state"] = _derive_login_ui_state(decorated)
    account_status = _norm(decorated.get(account_status_key)).lower()
    if account_status == "pending_login" and bool(decorated.get("has_credentials")):
        decorated.setdefault("raw_status", decorated.get(account_status_key))
        decorated[account_status_key] = "connected"
        if _norm(decorated.get("status")).lower() == "pending_login":
            decorated["status"] = "connected"
        decorated["connect_status"] = "PENDING_RUNTIME_LOGIN"
        decorated["connection_state"] = "PENDING_RUNTIME_LOGIN"
        decorated["runtime_login_required"] = True
        # Credentials are on control-plane; MT5 proof is pending on the reserved login slot.
        decorated["login_state"] = "LOGIN_IN_PROGRESS"
        decorated["login_ui_state"] = "SUBMITTED"
        account_status = "connected"
    start_ready = bool(
        decorated["login_state"] == "READY"
        or account_status == "connected"
    )
    decorated["start_login_ready"] = start_ready
    active_deployment_id = decorated.get("active_deployment_id")
    if not active_deployment_id and decorated.get("deployment_id"):
        deployment_status = _norm(decorated.get("deployment_status")).lower()
        if deployment_status in ACTIVE_DEPLOYMENT_STATUSES:
            active_deployment_id = decorated.get("deployment_id")
    decorated["can_start_bot"] = bool(start_ready and not active_deployment_id)
    if not start_ready:
        decorated["start_block_reason"] = "account_credentials_unavailable" if account_status == "pending_login" else "account_not_connected"
    elif active_deployment_id:
        decorated["start_block_reason"] = "account_has_active_deployment"
    else:
        decorated["start_block_reason"] = None
    failure_payload = decorated.get("login_reservation_payload_json")
    if not isinstance(failure_payload, dict):
        failure_payload = decorated.get("payload_json")
    failure_payload_map = failure_payload if isinstance(failure_payload, dict) else {}
    failure = _login_failure_metadata(
        error_text=decorated.get("last_error") or decorated.get("login_last_error"),
        payload=failure_payload_map,
    )
    if failure and (
        _norm(decorated.get(account_status_key)).lower() == "login_failed"
        or _norm(decorated.get(reservation_status_key)).lower() == "failed"
        or _norm_login_error_code(failure_payload_map.get("error_code"))
    ):
        decorated["login_failure"] = failure
        decorated.update(failure)
    return decorated


def _decorate_login_reservation_row(row: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not row:
        return row
    decorated = dict(row)
    decorated["login_state"] = _derive_login_state(
        account_status=decorated.get("account_status"),
        reservation_status=decorated.get("status"),
    )
    decorated["login_ui_state"] = _derive_login_ui_state(decorated)
    payload_map = decorated.get("payload_json") if isinstance(decorated.get("payload_json"), dict) else {}
    failure = _login_failure_metadata(
        error_text=decorated.get("last_error"),
        payload=payload_map,
    )
    if failure and (
        _norm(decorated.get("status")).lower() == "failed"
        or _norm_login_error_code(payload_map.get("error_code"))
    ):
        decorated["login_failure"] = failure
        decorated.update(failure)
    return decorated
