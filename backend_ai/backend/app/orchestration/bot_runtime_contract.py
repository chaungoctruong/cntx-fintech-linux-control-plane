from __future__ import annotations

from typing import Any


BOT_RUNTIME_TEXT_KEYS = (
    "catalog_lane",
    "bot_type",
    "execution_owner",
    "windows_role",
    "tradingview_webhook_owner",
    "runtime_language",
)
BOT_RUNTIME_BOOL_KEYS = ("requires_executor_slot",)

_DIRECT_EA_CATALOG_LANES = {"bot_ea", "mt5_ea", "mt5_ea_runtime"}
_DIRECT_EA_BOT_TYPES = {"mt5_ea_runtime"}
_DIRECT_EA_WINDOWS_ROLES = {"mt5_ea_runtime"}

BOT_RUNTIME_LANE_BACKEND_WEBHOOK_SIGNAL = "backend_webhook_signal"
BOT_RUNTIME_LANE_MT5_EA_RUNTIME = "mt5_ea_runtime"
BOT_RUNTIME_LANE_WINDOWS_PYTHON_MT5 = "windows_python_mt5_runtime"
BOT_RUNTIME_LANE_UNKNOWN = "unknown"

_ALL_LANES = {"", "*", "all", "any"}
_BACKEND_WEBHOOK_ALIASES = {
    "backend",
    "backend_webhook",
    BOT_RUNTIME_LANE_BACKEND_WEBHOOK_SIGNAL,
    "linux_backend",
    "tradingview",
    "tradingview_backend",
    "tv",
}
_MT5_EA_RUNTIME_ALIASES = {
    "bot_ea",
    "ea",
    "mt5_ea",
    BOT_RUNTIME_LANE_MT5_EA_RUNTIME,
}


def norm_contract_text(value: Any) -> str:
    return str(value or "").strip()


def contract_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def normalize_runtime_lane(value: Any) -> str:
    raw = norm_contract_text(value).lower()
    if raw in _ALL_LANES:
        return "all"
    if raw in _BACKEND_WEBHOOK_ALIASES:
        return BOT_RUNTIME_LANE_BACKEND_WEBHOOK_SIGNAL
    if raw in _MT5_EA_RUNTIME_ALIASES:
        return BOT_RUNTIME_LANE_MT5_EA_RUNTIME
    if raw in {"windows_python", "python_mt5_runtime", BOT_RUNTIME_LANE_WINDOWS_PYTHON_MT5}:
        return BOT_RUNTIME_LANE_WINDOWS_PYTHON_MT5
    return raw


def is_template_catalog_identity(value: Any) -> bool:
    text = norm_contract_text(value)
    return not text or text.startswith(".") or text.startswith("_")


def iter_runtime_contract_sources(*sources: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        out.append(source)
        for nested_key in (
            "runtime_env",
            "resource_hints",
            "metadata",
            "metadata_json",
            "execution_contract",
            "manifest_contract",
            "bot_contract",
        ):
            nested = source.get(nested_key)
            if isinstance(nested, dict):
                out.extend(iter_runtime_contract_sources(nested))
    return out


def bot_runtime_contract(*sources: Any) -> dict[str, Any]:
    contract: dict[str, Any] = {}
    for source in iter_runtime_contract_sources(*sources):
        for key in BOT_RUNTIME_TEXT_KEYS:
            value = norm_contract_text(source.get(key))
            if value and key not in contract:
                contract[key] = value
        for key in BOT_RUNTIME_BOOL_KEYS:
            if key in source and key not in contract:
                contract[key] = contract_bool(source.get(key))
    return contract


def merge_runtime_contract(target: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    for key, value in contract.items():
        if value is not None:
            target[key] = value
    return target


def is_template_catalog_entry(bot: dict[str, Any] | None) -> bool:
    if not bot:
        return False
    values = (
        bot.get("bot_id"),
        bot.get("bot_code"),
        bot.get("bot_name"),
        bot.get("display_name"),
        bot.get("package_dir"),
        bot.get("package"),
    )
    return any(is_template_catalog_identity(value) for value in values if norm_contract_text(value))


def is_mt5_ea_runtime_bot(bot: dict[str, Any] | None) -> bool:
    if not bot:
        return False
    contract = bot_runtime_contract(bot)
    catalog_lane = norm_contract_text(contract.get("catalog_lane")).lower()
    bot_type = norm_contract_text(contract.get("bot_type")).lower()
    windows_role = norm_contract_text(contract.get("windows_role")).lower()
    return (
        catalog_lane in _DIRECT_EA_CATALOG_LANES
        or bot_type in _DIRECT_EA_BOT_TYPES
        or windows_role in _DIRECT_EA_WINDOWS_ROLES
    )


def bot_runtime_lane(bot: dict[str, Any] | None) -> str:
    if not bot:
        return BOT_RUNTIME_LANE_UNKNOWN
    if is_mt5_ea_runtime_bot(bot):
        return BOT_RUNTIME_LANE_MT5_EA_RUNTIME
    contract = bot_runtime_contract(bot)
    catalog_lane = normalize_runtime_lane(contract.get("catalog_lane"))
    if catalog_lane and catalog_lane != "all":
        return catalog_lane
    bot_type = normalize_runtime_lane(contract.get("bot_type"))
    if bot_type and bot_type != "all":
        return bot_type
    windows_role = norm_contract_text(contract.get("windows_role")).lower()
    owner = norm_contract_text(contract.get("execution_owner")).lower()
    tv_owner = norm_contract_text(contract.get("tradingview_webhook_owner")).lower()
    if windows_role == "mt5_executor_only" or owner == "linux_backend" or tv_owner == "linux":
        return BOT_RUNTIME_LANE_BACKEND_WEBHOOK_SIGNAL
    if windows_role == "python_mt5_runtime" or owner == "windows_runner":
        return BOT_RUNTIME_LANE_WINDOWS_PYTHON_MT5
    return BOT_RUNTIME_LANE_UNKNOWN


def bot_matches_runtime_lane(bot: dict[str, Any] | None, requested_runtime_lane: Any) -> bool:
    requested = normalize_runtime_lane(requested_runtime_lane)
    if requested == "all":
        return True
    return bot_runtime_lane(bot) == requested


def mt5_direct_ea_runtime_enabled(settings_obj: Any) -> bool:
    for key in ("MT5_DIRECT_EA_RUNTIME_ENABLED", "MT5_EA_RUNTIME_ENABLED", "BOT_EA_RUNTIME_ENABLED"):
        if hasattr(settings_obj, key):
            return contract_bool(getattr(settings_obj, key))
    return False


def bot_start_runtime_supported(bot: dict[str, Any] | None, *, settings_obj: Any) -> bool:
    if is_mt5_ea_runtime_bot(bot):
        return mt5_direct_ea_runtime_enabled(settings_obj)
    return True


def bot_start_runtime_disabled_reason(bot: dict[str, Any] | None, *, settings_obj: Any) -> str:
    if is_mt5_ea_runtime_bot(bot) and not mt5_direct_ea_runtime_enabled(settings_obj):
        return "direct_ea_runtime_not_enabled"
    return ""


def bot_runtime_start_guard_reason(
    bot: dict[str, Any] | None,
    *,
    settings_obj: Any,
    requested_runtime_lane: Any = None,
    require_explicit_lane: bool = False,
) -> str:
    actual_lane = bot_runtime_lane(bot)
    requested_raw = norm_contract_text(requested_runtime_lane)
    requested_lane = normalize_runtime_lane(requested_runtime_lane)
    if requested_raw and requested_lane != "all" and requested_lane != actual_lane:
        return "bot_runtime_lane_mismatch"
    if require_explicit_lane and actual_lane == BOT_RUNTIME_LANE_MT5_EA_RUNTIME and not requested_raw:
        return "bot_runtime_lane_required"
    return bot_start_runtime_disabled_reason(bot, settings_obj=settings_obj)
