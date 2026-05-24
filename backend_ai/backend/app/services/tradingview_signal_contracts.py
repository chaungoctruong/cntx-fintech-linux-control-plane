"""Helpers for TradingView signal routing contracts.

The public webhook is intentionally small, but the backend needs a stable
language for routing many signal producers to many execution bots.  These
helpers keep that contract explicit while preserving the legacy payload shape.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


_STRATEGY_KEYS = (
    "strategy_code",
    "strategy_id",
    "strategy",
    "logic_code",
    "setup_code",
)
_STRATEGY_LIST_KEYS = (
    "strategy_codes",
    "allowed_strategy_codes",
    "allowed_strategies",
    "strategies",
)
_TRUTHY = {"1", "true", "yes", "on"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _normalize_code(value: Any) -> str:
    raw = _text(value)
    if not raw:
        return ""
    return raw.replace(" ", "-").lower()


def _as_code_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = value.replace(";", ",").replace("|", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = [value]
    out: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        code = _normalize_code(item)
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return _text(value).lower() in _TRUTHY


def _first_strategy_code(*sources: Any) -> str:
    for source in sources:
        payload = _dict(source)
        for key in _STRATEGY_KEYS:
            code = _normalize_code(payload.get(key))
            if code:
                return code
        for nested_key in ("signal", "signals", "tradingview", "routing", "metadata"):
            nested = _dict(payload.get(nested_key))
            for key in _STRATEGY_KEYS:
                code = _normalize_code(nested.get(key))
                if code:
                    return code
    return ""


def _allowed_strategy_codes(*sources: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for source in sources:
        payload = _dict(source)
        containers = [
            payload,
            _dict(payload.get("signal")),
            _dict(payload.get("signals")),
            _dict(payload.get("tradingview")),
            _dict(payload.get("routing")),
            _dict(payload.get("metadata")),
        ]
        for container in containers:
            for key in _STRATEGY_LIST_KEYS:
                for code in _as_code_list(container.get(key)):
                    if code not in seen:
                        seen.add(code)
                        out.append(code)
            code = _first_strategy_code(container)
            if code and code not in seen:
                seen.add(code)
                out.append(code)
    return out


def _feature_flags(body: dict[str, Any]) -> dict[str, bool]:
    features = _dict(body.get("features"))
    flags = {
        "market_entry": True,
        "dca_limit": False,
        "close_order": True,
    }
    for key, value in features.items():
        text_key = _normalize_code(key).replace("-", "_")
        if text_key:
            flags[text_key] = _bool(value)
    dca_type = _text(body.get("dca_order_type") or body.get("dca_entry_type")).lower()
    if dca_type in {"limit", "pending_limit", "buy_limit", "sell_limit"}:
        flags["dca_limit"] = True
    if body.get("dca_limit_price") not in (None, "") or body.get("dca_price") not in (None, ""):
        flags["dca_limit"] = True
    return flags


@dataclass(frozen=True)
class TradingViewSignalContract:
    signal_id: str
    requested_bot_code: str = ""
    strategy_code: str = "default"
    contract_version: int | None = None
    features: dict[str, bool] = field(default_factory=dict)

    @property
    def has_explicit_strategy(self) -> bool:
        return self.strategy_code != "default"

    def as_payload(self, *, subscriber_bot_code: str = "", allowed_strategy_codes: list[str] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "signal_id": self.signal_id,
            "bot_code": subscriber_bot_code or self.requested_bot_code,
            "requested_bot_code": self.requested_bot_code,
            "strategy_code": self.strategy_code,
            "contract_version": self.contract_version,
            "features": dict(self.features),
        }
        if allowed_strategy_codes:
            payload["allowed_strategy_codes"] = list(allowed_strategy_codes)
        return payload


def resolve_signal_contract(
    *,
    body: dict[str, Any],
    signal_id: str,
    requested_bot_code: str = "",
) -> TradingViewSignalContract:
    version_raw = body.get("contract_version") or body.get("schema_version") or body.get("order_contract_version")
    try:
        version = int(version_raw) if version_raw not in (None, "") else None
    except (TypeError, ValueError):
        version = None
    strategy_code = _first_strategy_code(body) or "default"
    return TradingViewSignalContract(
        signal_id=_text(signal_id),
        requested_bot_code=_text(requested_bot_code),
        strategy_code=strategy_code,
        contract_version=version,
        features=_feature_flags(body),
    )


def subscriber_allowed_strategy_codes(subscriber: dict[str, Any]) -> list[str]:
    return _allowed_strategy_codes(
        subscriber.get("subscription_metadata"),
        subscriber.get("deployment_config_json"),
    )


def subscriber_accepts_strategy(
    *,
    contract: TradingViewSignalContract,
    subscriber: dict[str, Any],
) -> tuple[bool, list[str]]:
    allowed = subscriber_allowed_strategy_codes(subscriber)
    if not allowed:
        return True, []
    return contract.strategy_code in allowed, allowed


def order_intent_payload(
    *,
    contract: TradingViewSignalContract,
    kind: str,
    side: str = "",
    role: str = "ENTRY",
    entry_type: str = "market",
    source_symbol: str = "",
    mapped_symbol: str = "",
) -> dict[str, Any]:
    return {
        "source": "tradingview",
        "kind": _text(kind),
        "side": _text(side).lower(),
        "role": _text(role).upper() or "ENTRY",
        "entry_type": _text(entry_type).lower() or "market",
        "signal_id": contract.signal_id,
        "bot_code": contract.requested_bot_code,
        "strategy_code": contract.strategy_code,
        "source_symbol": _text(source_symbol),
        "mapped_symbol": _text(mapped_symbol),
        "features": dict(contract.features),
    }
