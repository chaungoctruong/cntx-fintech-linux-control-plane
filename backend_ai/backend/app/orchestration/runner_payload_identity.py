from __future__ import annotations

from typing import Any


_RUNNER_IDENTITY_KEYS = ("runner_id", "node_id", "mt5_runner_id", "runner_uri")
_SLOT_TERMINAL_PATH_KEYS = (
    "terminal_path",
    "mt5_terminal_path",
    "terminal64_path",
    "terminal_exe_path",
    "terminal_executable",
)
_SLOT_STORAGE_KEYS = ("storage_slot_id", "slot_storage_id", "slot_path", "slot_dir", "data_path")


def runner_command_request_type(command_type: Any) -> str:
    raw = getattr(command_type, "value", command_type)
    return str(raw or "").strip().lower()


def _command_type_value(command_type: Any) -> str:
    return str(getattr(command_type, "value", command_type) or "").strip().upper()


def _safe_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except Exception:
        return None
    return parsed if parsed > 0 else None


def _first_text(source: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = str(source.get(key) or "").strip()
        if value:
            return value
    nested_paths = source.get("paths")
    if isinstance(nested_paths, dict):
        for key in keys:
            value = str(nested_paths.get(key) or "").strip()
            if value:
                return value
    return ""


def normalize_runner_payload_identity(
    payload: dict[str, Any] | None,
    *,
    runner_id: str,
    slot_id: str,
) -> dict[str, Any]:
    """Keep nested runner hints aligned with the command envelope target."""
    normalized = dict(payload or {})
    runner_value = str(runner_id or "").strip()
    slot_value = str(slot_id or "").strip()

    if runner_value:
        for key in _RUNNER_IDENTITY_KEYS:
            normalized[key] = runner_value
    if slot_value:
        normalized["slot_id"] = slot_value

    hints_source = normalized.get("resource_hints")
    hints = dict(hints_source) if isinstance(hints_source, dict) else {}
    if runner_value:
        for key in _RUNNER_IDENTITY_KEYS:
            hints[key] = runner_value
    if slot_value:
        hints["slot_id"] = slot_value
    normalized["resource_hints"] = hints
    return normalized


def normalize_runner_command_payload(
    payload: dict[str, Any] | None,
    *,
    command_type: Any,
    account_id: Any,
    deployment_id: Any,
    runner_id: str,
    slot_id: str,
    slot_runtime_hints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = normalize_runner_payload_identity(payload, runner_id=runner_id, slot_id=slot_id)
    command_type_value = _command_type_value(command_type)
    if command_type_value:
        normalized["command_type"] = command_type_value
    cmd_type = runner_command_request_type(command_type)
    if cmd_type:
        normalized["cmd_type"] = cmd_type
        normalized["requested_cmd_type"] = cmd_type

    account_id_i = _safe_positive_int(account_id)
    if account_id_i is not None:
        normalized["account_id"] = account_id_i
        normalized["profile_id"] = account_id_i
    deployment_id_i = _safe_positive_int(deployment_id)
    if deployment_id_i is not None:
        normalized["deployment_id"] = deployment_id_i

    normalized.pop("keep_prepared_session", None)
    normalized.pop("soft_detach", None)

    if command_type_value == "START_BOT":
        normalized.setdefault("runtime_login_required", True)
        normalized.setdefault("credential_check_policy", "login_before_start")
        normalized.setdefault("mt5_recovery_policy", "recover_or_launch")
    elif command_type_value == "STOP_BOT":
        normalized.setdefault("stop_policy", "end_task")
        normalized.setdefault("end_task", True)
        normalized.setdefault("kill_worker", True)
        normalized.setdefault("kill_mt5", False)
        normalized.setdefault("terminate_mt5", False)
        normalized.setdefault("release_terminal", True)

    hints = dict(slot_runtime_hints or {})
    terminal_path = _first_text(hints, _SLOT_TERMINAL_PATH_KEYS)
    storage_slot_id = _first_text(hints, _SLOT_STORAGE_KEYS)
    resource_hints = dict(normalized.get("resource_hints") or {})
    if terminal_path:
        normalized["terminal_path"] = terminal_path
        resource_hints["terminal_path"] = terminal_path
    if storage_slot_id:
        normalized["storage_slot_id"] = storage_slot_id
        resource_hints["storage_slot_id"] = storage_slot_id
    resource_hints.pop("keep_prepared_session", None)
    resource_hints.pop("soft_detach", None)
    normalized["resource_hints"] = resource_hints
    return normalized
