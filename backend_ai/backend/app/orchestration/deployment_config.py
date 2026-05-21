from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any, Optional


TRADING_CONFIG_SCHEMA_VERSION = 1
TRADING_CONFIG_KEY = "trading"
TRADING_CONFIG_DEFAULTS: dict[str, Any] = {
    "schema_version": TRADING_CONFIG_SCHEMA_VERSION,
    "lot_size": 0.01,
    "stop_loss": 5,
    "take_profit": 5,
    "trading_unit": "price_distance",
}

_TRADING_NUMERIC_KEYS = ("lot_size", "stop_loss", "take_profit")
_TRADING_UNIT_KEYS = ("unit", "distance_unit", "sl_tp_unit", "trade_unit", "trading_unit")
_TRADING_BOOLEAN_KEYS = ("dca_enabled",)
_UNSUPPORTED_DCA_CONFIG_KEYS = ("max_entries", "volume_multiplier")
_ALLOWED_UNITS = {"price_distance", "points"}
_TRADING_CONFIG_BOT_IDENTITIES: set[str] = set()
_RESTART_ON_CONFIG_UPDATE_KEYS = {
    "restart_on_config_update",
    "requires_restart_on_config_update",
    "restart_required_on_config_update",
}


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _norm_key(value: Any) -> str:
    return str(value or "").strip().lower()


def _norm_identity(value: Any) -> str:
    return "".join(ch for ch in _norm_key(value) if ch.isalnum() or ch == "_")


def bot_is_gsalgo_trading_config_bot(bot: Optional[dict[str, Any]]) -> bool:
    payload = _as_mapping(bot)
    identities = {
        _norm_identity(payload.get(key))
        for key in ("bot_id", "bot_code", "bot_name", "display_name")
        if _norm_identity(payload.get(key))
    }
    return bool(identities.intersection(_TRADING_CONFIG_BOT_IDENTITIES))


def _json_number(value: Decimal) -> int | float:
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _positive_number(raw: Any) -> int | float:
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("invalid_deployment_config") from None
    if not value.is_finite() or value <= 0:
        raise ValueError("invalid_deployment_config")
    return _json_number(value)


def _boolean_value(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int) and not isinstance(raw, bool):
        if raw in {0, 1}:
            return bool(raw)
        raise ValueError("invalid_deployment_config")
    if isinstance(raw, float):
        if raw in {0.0, 1.0}:
            return bool(int(raw))
        raise ValueError("invalid_deployment_config")
    text = str(raw).strip().lower()
    if text in {"true", "1", "on"}:
        return True
    if text in {"false", "0", "off"}:
        return False
    raise ValueError("invalid_deployment_config")


def _extract_declared_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return {_norm_key(key) for key in value.keys() if _norm_key(key)}
    if isinstance(value, (list, tuple, set)):
        return {_norm_key(item) for item in value if _norm_key(item)}
    return set()


def _metadata_declares_trading_config(bot: Optional[dict[str, Any]]) -> bool:
    payload = _as_mapping(bot)
    identities = {
        _norm_identity(payload.get(key))
        for key in ("bot_id", "bot_code", "bot_name", "display_name")
        if _norm_identity(payload.get(key))
    }
    if identities.intersection(_TRADING_CONFIG_BOT_IDENTITIES):
        return True

    required = _extract_declared_keys(payload.get("required_params"))
    if set(_TRADING_NUMERIC_KEYS).issubset(required):
        return True

    for key in ("risk_profile", "resource_hints", "runtime_env", "metadata", "config_schema", "deployment_config_schema"):
        section = _as_mapping(payload.get(key))
        if not section:
            continue
        section_keys = {_norm_key(item) for item in section.keys()}
        if set(_TRADING_NUMERIC_KEYS).issubset(section_keys):
            return True
        nested = section.get(TRADING_CONFIG_KEY) or section.get("trading_parameters")
        nested_keys = _extract_declared_keys(nested)
        if set(_TRADING_NUMERIC_KEYS).issubset(nested_keys):
            return True
    return False


def _metadata_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def bot_requires_restart_on_config_update(bot: Optional[dict[str, Any]]) -> bool:
    """Whether config changes must be applied through STOP/START.

    GSALGO needs this because trading config affects strategy state, not only
    risk-order placement. Other bots can opt in through metadata without making
    this behavior global.
    """
    payload = _as_mapping(bot)
    if bot_is_gsalgo_trading_config_bot(payload):
        return True

    for key in _RESTART_ON_CONFIG_UPDATE_KEYS:
        if _metadata_bool(payload.get(key)):
            return True

    for section_key in (
        "risk_profile",
        "resource_hints",
        "runtime_env",
        "metadata",
        "config_schema",
        "deployment_config_schema",
    ):
        section = _as_mapping(payload.get(section_key))
        if any(_metadata_bool(section.get(key)) for key in _RESTART_ON_CONFIG_UPDATE_KEYS):
            return True
        trading_section = _as_mapping(section.get(TRADING_CONFIG_KEY))
        if any(_metadata_bool(trading_section.get(key)) for key in _RESTART_ON_CONFIG_UPDATE_KEYS):
            return True
    return False


def _extract_requested_trading_config(config: dict[str, Any]) -> dict[str, Any]:
    requested: dict[str, Any] = {}
    nested = config.get(TRADING_CONFIG_KEY)
    if isinstance(nested, dict):
        requested.update(nested)

    for key in _TRADING_NUMERIC_KEYS:
        if key in config:
            requested[key] = config[key]
    for key in _TRADING_UNIT_KEYS:
        if key in config:
            requested["trading_unit"] = config[key]
    for key in _TRADING_BOOLEAN_KEYS:
        if key in config:
            requested[key] = config[key]
    return requested


def _reject_unsupported_dca_config(config: dict[str, Any]) -> None:
    containers = [config]
    nested = config.get(TRADING_CONFIG_KEY)
    if isinstance(nested, dict):
        containers.append(nested)
    for container in containers:
        raw_dca = container.get("dca")
        if isinstance(raw_dca, (dict, list, tuple, set)):
            raise ValueError("invalid_deployment_config")
        if any(key in container for key in _UNSUPPORTED_DCA_CONFIG_KEYS):
            raise ValueError("invalid_deployment_config")


def has_requested_trading_config(config: Optional[dict[str, Any]]) -> bool:
    return bool(_extract_requested_trading_config(_as_mapping(config)))


def requested_trading_config_fields(config: Optional[dict[str, Any]]) -> set[str]:
    requested = _extract_requested_trading_config(_as_mapping(config))
    return {key for key in (*_TRADING_NUMERIC_KEYS, "trading_unit", *_TRADING_BOOLEAN_KEYS) if key in requested}


def is_dca_only_config_update(config: Optional[dict[str, Any]]) -> bool:
    payload = _as_mapping(config)
    if not payload:
        return False
    for key, value in payload.items():
        normalized_key = _norm_key(key)
        if normalized_key == TRADING_CONFIG_KEY:
            if not isinstance(value, dict):
                return False
            if any(_norm_key(nested_key) != "dca_enabled" for nested_key in value.keys()):
                return False
            continue
        if normalized_key != "dca_enabled":
            return False
    return requested_trading_config_fields(payload) == {"dca_enabled"}


def bot_supports_trading_config(bot: Optional[dict[str, Any]]) -> bool:
    return _metadata_declares_trading_config(bot)


def should_apply_trading_config(*, bot: Optional[dict[str, Any]], config: Optional[dict[str, Any]]) -> bool:
    return has_requested_trading_config(config) or bot_supports_trading_config(bot)


def normalize_trading_config(config: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    payload = _as_mapping(config)
    _reject_unsupported_dca_config(payload)
    requested = _extract_requested_trading_config(payload)
    normalized = dict(TRADING_CONFIG_DEFAULTS)

    for key in _TRADING_NUMERIC_KEYS:
        if key in requested:
            normalized[key] = _positive_number(requested[key])

    raw_unit = requested.get("trading_unit", requested.get("unit", normalized["trading_unit"]))
    trading_unit = _norm_key(raw_unit) or str(normalized["trading_unit"])
    if trading_unit not in _ALLOWED_UNITS:
        raise ValueError("invalid_deployment_config")
    normalized["trading_unit"] = trading_unit
    if "dca_enabled" in requested:
        normalized["dca_enabled"] = _boolean_value(requested["dca_enabled"])
    return normalized


def normalize_deployment_config(*, bot: Optional[dict[str, Any]], config: Optional[dict[str, Any]]) -> dict[str, Any]:
    base = deepcopy(_as_mapping(config))
    if not should_apply_trading_config(bot=bot, config=base):
        return base

    trading_config = normalize_trading_config(base)
    for key in (*_TRADING_NUMERIC_KEYS, *_TRADING_UNIT_KEYS, *_TRADING_BOOLEAN_KEYS):
        base.pop(key, None)
    base[TRADING_CONFIG_KEY] = trading_config
    return base


def build_trading_config_audit_patch(
    *,
    bot: Optional[dict[str, Any]],
    original_config: Optional[dict[str, Any]],
    effective_config: Optional[dict[str, Any]],
) -> dict[str, Any]:
    changed_fields = sorted(requested_trading_config_fields(original_config))
    if not changed_fields:
        return {}
    effective = normalize_deployment_config(bot=bot, config=effective_config)
    trading = _as_mapping(effective.get(TRADING_CONFIG_KEY))
    return {
        "changed_fields": changed_fields,
        TRADING_CONFIG_KEY: {key: trading.get(key) for key in changed_fields if key in trading},
        "schema_version": TRADING_CONFIG_SCHEMA_VERSION,
    }
