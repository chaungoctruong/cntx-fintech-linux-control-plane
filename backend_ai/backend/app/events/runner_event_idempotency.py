from __future__ import annotations

from typing import Any


_FINAL_EVENT_TYPES = {
    "LOGIN_SLOT_VERIFIED",
    "LOGIN_SLOT_FAILED",
    "LOGIN_SLOT_RELEASED",
    "BOT_STARTED",
    "BOT_STOPPED",
    "BOT_LISTENING",
    "SIGNAL_EXECUTOR_READY",
    "SIGNAL_EXECUTOR_STOPPED",
    "RECOVERY_STARTED",
    "RECOVERY_COMPLETED",
    "RECOVERY_FAILED",
    "RECOVERY_BLOCKED",
    "MT5_RECOVERY_BUDGET_EXHAUSTED",
    "COMMAND_REJECTED",
    "ORDER_SENT",
    "ORDER_FILLED",
    "ORDER_REJECTED",
}

_ORDER_ID_KEYS = (
    "ticket",
    "position_ticket",
    "mt5_position_id",
    "position_id",
    "position",
    "deal_id",
    "order_id",
    "backend_order_id",
)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _payload_identifier(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = _clean(payload.get(key))
        if value:
            return value
    request = payload.get("request")
    if isinstance(request, dict):
        for key in keys:
            value = _clean(request.get(key))
            if value:
                return value
    return ""


def stable_runner_event_id(
    *,
    event_id: str,
    event_type: str,
    command_id: str,
    payload: dict[str, Any],
) -> str:
    """Return deterministic ids for final runner events.

    Windows should send stable event ids, but production callback delivery must
    also tolerate replays that accidentally regenerate UUIDs. Runtime logs,
    heartbeats and slot telemetry stay untouched because they are intentionally
    high-cardinality events.
    """
    original = _clean(event_id)
    event_type_s = _clean(event_type).upper()
    if event_type_s not in _FINAL_EVENT_TYPES:
        return original

    payload_map = payload if isinstance(payload, dict) else {}
    command_s = _clean(command_id) or _payload_identifier(payload_map, "command_id")

    if event_type_s.startswith("LOGIN_SLOT_"):
        reservation_id = _payload_identifier(payload_map, "login_reservation_id", "reservation_id")
        if reservation_id:
            return f"runner-final:login-slot:{reservation_id}:{event_type_s.lower()}"

    if event_type_s in {
        "RECOVERY_STARTED",
        "RECOVERY_COMPLETED",
        "RECOVERY_FAILED",
        "RECOVERY_BLOCKED",
        "MT5_RECOVERY_BUDGET_EXHAUSTED",
    }:
        recovery_id = _payload_identifier(payload_map, "recovery_id", "recovery_attempt_id", "attempt_id")
        deployment_id = _payload_identifier(payload_map, "deployment_id")
        attempt = _payload_identifier(payload_map, "recovery_attempt", "attempt")
        if recovery_id:
            return f"runner-final:recovery:{recovery_id}:{event_type_s.lower()}"
        if deployment_id and attempt:
            return f"runner-final:recovery:{deployment_id}:{attempt}:{event_type_s.lower()}"

    if not command_s:
        return original

    suffix = ""
    if event_type_s.startswith("ORDER_"):
        order_id = _payload_identifier(payload_map, *_ORDER_ID_KEYS)
        if order_id:
            suffix = f":{order_id}"

    return f"runner-final:{command_s}:{event_type_s.lower()}{suffix}"
