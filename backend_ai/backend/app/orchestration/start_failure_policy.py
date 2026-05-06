from __future__ import annotations

from dataclasses import dataclass
from typing import Any


MAX_START_AUTO_REROUTE_ATTEMPTS = 2
SLOT_QUARANTINE_SEC = 300
RUNNER_THROTTLE_FAILURE_THRESHOLD = 3
RUNNER_THROTTLE_SEC = 120

_SLOT_RUNTIME_FAILURE_FRAGMENTS = (
    "bootstrapping_attached_terminal",
    "did not become ready",
    "slot_resident_worker_stale",
    "resident_worker_stale",
    "resident worker stale",
    "slot_resident_worker_missing",
    "resident_worker_missing",
    "worker_not_ready",
    "worker not ready",
    "start_worker_not_running",
    "interactive_start_worker_not_running",
    "slot_not_ready",
    "slot_not_available",
    "slot_not_ipc_ready",
    "broken",
    "no_ipc_ready_start_slots",
    "ipc_ready",
    "duplicate_terminal",
    "duplicate terminal",
    "duplicate terminal64",
    "terminal64",
    "worker-managed slot",
    "attach",
    "attached_terminal",
    "slot_runtime",
    "slot bootstrap",
    "slot_bootstrap",
    "invalid slot transition",
    "slot transition",
)

_NON_REROUTE_FRAGMENTS = (
    "runner_uri_node_mismatch",
    "invalid_credentials",
    "authorization_failed",
    "auth_failed",
    "invalid account",
    "invalid password",
    "login_returned_false",
    "verify_login_mismatch",
    "verify_server_mismatch",
    "account_has_active_deployment",
    "account_not_active",
    "bot_not_available",
    "unsupported_command",
    "terminal_trade_not_allowed_manual_intervention_required",
)

_CREDENTIAL_FAILURE_FRAGMENTS = (
    "invalid_credentials",
    "invalid credential",
    "invalid_password",
    "invalid password",
    "wrong password",
    "bad credentials",
    "authorization_failed",
    "authorization failed",
    "auth_failed",
    "auth failed",
    "login_returned_false",
    "invalid account",
    "account_not_found",
    "account not found",
    "invalid_server",
    "invalid server",
    "server not found",
    "verify_login_mismatch",
    "verify_server_mismatch",
)

_CREDENTIAL_FAILURE_CODES = {
    "INVALID_CREDENTIALS",
    "INVALID_PASSWORD",
    "INVALID_SERVER",
    "ACCOUNT_NOT_FOUND",
}


@dataclass(frozen=True)
class StartFailureAction:
    reason: str
    slot_runtime_failure: bool
    auto_reroute: bool
    next_attempt: int
    quarantine_sec: int = SLOT_QUARANTINE_SEC


def start_failure_reason(payload: dict[str, Any] | None, fallback: str = "command_rejected") -> str:
    source = payload or {}
    for key in ("reason", "message", "error", "last_error", "exact_exception"):
        value = str(source.get(key) or "").strip()
        if value:
            return value[:300]
    return fallback


def start_auto_reroute_attempt(payload: dict[str, Any] | None) -> int:
    source = payload or {}
    try:
        return max(0, int(source.get("auto_reroute_attempt") or 0))
    except Exception:
        return 0


def start_failure_is_credential_failure(*, reason: str, payload: dict[str, Any] | None) -> bool:
    source = payload or {}
    error_code = str(source.get("error_code") or "").strip().upper().replace("-", "_").replace(" ", "_")
    if error_code in _CREDENTIAL_FAILURE_CODES:
        return True
    text = " ".join(
        str(source.get(key) or "")
        for key in ("reason", "message", "error", "last_error", "phase", "mt5_last_error", "terminal_log_line")
    )
    normalized = f"{reason or ''} {text}".strip().lower().replace("-", "_")
    readable = normalized.replace("_", " ")
    return any(fragment in normalized or fragment in readable for fragment in _CREDENTIAL_FAILURE_FRAGMENTS)


def classify_start_failure(
    *,
    reason: str,
    command_payload: dict[str, Any] | None,
) -> StartFailureAction:
    normalized = str(reason or "").strip().lower()
    current_attempt = start_auto_reroute_attempt(command_payload)
    non_reroute = any(fragment in normalized for fragment in _NON_REROUTE_FRAGMENTS)
    slot_runtime_failure = (not non_reroute) and any(
        fragment in normalized for fragment in _SLOT_RUNTIME_FAILURE_FRAGMENTS
    )
    next_attempt = current_attempt + 1
    return StartFailureAction(
        reason=str(reason or "command_rejected").strip()[:300],
        slot_runtime_failure=slot_runtime_failure,
        auto_reroute=slot_runtime_failure and next_attempt <= MAX_START_AUTO_REROUTE_ATTEMPTS,
        next_attempt=next_attempt,
    )
