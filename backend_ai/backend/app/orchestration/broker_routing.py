from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping


_DEFAULT_BROKER_ALIASES: dict[str, set[str]] = {
    "dbg": {"dbg", "dbgmarket", "dbgmarkets"},
    "exness": {"exness"},
    "xm": {"xm", "xmglobal", "xmtrading"},
    "icmarket": {"icmarket", "icmarkets", "icmarketsglobal"},
    "vantage": {"vantage"},
    "dupoin": {"dupoin"},
}


@dataclass(frozen=True)
class BrokerRoutePolicy:
    enabled: bool = True
    strict: bool = True
    require_capability: bool = False
    route_key: str = ""
    broker: str = ""
    server: str = ""
    aliases: Mapping[str, frozenset[str]] = field(default_factory=dict)
    runner_broker_map: Mapping[str, frozenset[str]] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return bool(self.enabled and self.route_key)


def _compact(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _split_items(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    raw = str(value or "").strip()
    if not raw:
        return []
    return [item.strip() for item in re.split(r"[,|\n]+", raw) if item.strip()]


def _load_json_mapping(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return dict(value)
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def parse_broker_aliases(value: Any) -> dict[str, set[str]]:
    aliases: dict[str, set[str]] = {key: set(items) | {key} for key, items in _DEFAULT_BROKER_ALIASES.items()}
    parsed = _load_json_mapping(value)
    if parsed is not None:
        for key, raw_values in parsed.items():
            canonical = _compact(key)
            if not canonical:
                continue
            aliases.setdefault(canonical, {canonical})
            aliases[canonical].update(_compact(item) for item in _split_items(raw_values) if _compact(item))
        return aliases

    raw = str(value or "").strip()
    if not raw:
        return aliases
    for chunk in re.split(r"[;\n]+", raw):
        item = chunk.strip()
        if not item:
            continue
        separator = "=" if "=" in item else ":" if ":" in item else ""
        if not separator:
            continue
        key, raw_values = item.split(separator, 1)
        canonical = _compact(key)
        if not canonical:
            continue
        aliases.setdefault(canonical, {canonical})
        aliases[canonical].update(_compact(part) for part in _split_items(raw_values) if _compact(part))
    return aliases


def canonical_broker_key(value: Any, aliases: Mapping[str, set[str]] | None = None) -> str:
    compact = _compact(value)
    if not compact:
        return ""
    alias_map = aliases or _DEFAULT_BROKER_ALIASES
    for canonical, values in sorted(alias_map.items(), key=lambda item: -max(len(v) for v in item[1] or {item[0]})):
        canonical_compact = _compact(canonical)
        candidates = {_compact(item) for item in (values or set()) if _compact(item)}
        candidates.add(canonical_compact)
        for candidate in sorted(candidates, key=len, reverse=True):
            if not candidate:
                continue
            if compact == candidate or candidate in compact:
                return canonical_compact
    return compact


def _known_broker_key(value: Any, aliases: Mapping[str, set[str]] | None = None) -> str:
    compact = _compact(value)
    if not compact:
        return ""
    alias_map = aliases or _DEFAULT_BROKER_ALIASES
    for canonical, values in sorted(alias_map.items(), key=lambda item: -max(len(v) for v in item[1] or {item[0]})):
        canonical_compact = _compact(canonical)
        candidates = {_compact(item) for item in (values or set()) if _compact(item)}
        candidates.add(canonical_compact)
        for candidate in sorted(candidates, key=len, reverse=True):
            if candidate and (compact == candidate or candidate in compact):
                return canonical_compact
    return ""


def normalize_account_broker_route(
    *,
    broker: Any,
    server: Any,
    aliases: Mapping[str, set[str]] | None = None,
) -> str:
    for value in (broker, server, f"{broker or ''} {server or ''}"):
        route_key = _known_broker_key(value, aliases)
        if route_key:
            return route_key
    for value in (broker, server, f"{broker or ''} {server or ''}"):
        route_key = _compact(value)
        if route_key:
            return route_key
    return ""


def parse_runner_broker_map(value: Any, aliases: Mapping[str, set[str]] | None = None) -> dict[str, frozenset[str]]:
    parsed = _load_json_mapping(value)
    items: dict[str, Any] = {}
    if parsed is not None:
        items = parsed
    else:
        raw = str(value or "").strip()
        if not raw:
            return {}
        for chunk in re.split(r"[;\n]+", raw):
            item = chunk.strip()
            if not item:
                continue
            separator = "=" if "=" in item else ":" if ":" in item else ""
            if not separator:
                continue
            runner_id, raw_values = item.split(separator, 1)
            items[str(runner_id).strip()] = raw_values

    mapped: dict[str, frozenset[str]] = {}
    for runner_id, raw_values in items.items():
        runner = str(runner_id or "").strip()
        if not runner:
            continue
        values = set()
        for item in _split_items(raw_values):
            if str(item).strip() == "*":
                values.add("*")
                continue
            key = canonical_broker_key(item, aliases)
            if key:
                values.add(key)
        if values:
            mapped[runner] = frozenset(values)
    return mapped


def broker_route_policy_from_settings(*, account: Mapping[str, Any] | None, settings_obj: Any) -> BrokerRoutePolicy:
    data = dict(account or {})
    aliases = parse_broker_aliases(getattr(settings_obj, "MT5_BROKER_ROUTE_ALIASES", ""))
    route_key = normalize_account_broker_route(
        broker=data.get("broker"),
        server=data.get("server"),
        aliases=aliases,
    )
    return BrokerRoutePolicy(
        enabled=bool(getattr(settings_obj, "MT5_BROKER_ROUTING_ENABLED", True)),
        strict=bool(getattr(settings_obj, "MT5_BROKER_ROUTING_STRICT", True)),
        require_capability=bool(getattr(settings_obj, "MT5_BROKER_ROUTING_REQUIRE_CAPABILITY", False)),
        route_key=route_key,
        broker=str(data.get("broker") or ""),
        server=str(data.get("server") or ""),
        aliases={key: frozenset(values) for key, values in aliases.items()},
        runner_broker_map=parse_runner_broker_map(
            getattr(settings_obj, "MT5_RUNNER_BROKER_MAP", ""),
            aliases=aliases,
        ),
    )


def _normalized_values(value: Any) -> set[str]:
    values: set[str] = set()
    if value is None:
        return values
    if isinstance(value, dict):
        for key in (
            "broker",
            "broker_key",
            "broker_route",
            "broker_route_key",
            "runner_pool",
            "pool",
            "name",
            "code",
        ):
            values.update(_normalized_values(value.get(key)))
        for key in (
            "brokers",
            "broker_keys",
            "supported_brokers",
            "supported_broker_keys",
            "pools",
            "runner_pools",
        ):
            values.update(_normalized_values(value.get(key)))
        return values
    if isinstance(value, (list, tuple, set)):
        for item in value:
            values.update(_normalized_values(item))
        return values
    raw = str(value or "").strip()
    if not raw:
        return values
    for item in _split_items(raw):
        compact = _compact(item)
        if compact:
            values.add(compact)
    return values


def _server_values(value: Any) -> set[str]:
    values: set[str] = set()
    if value is None:
        return values
    if isinstance(value, dict):
        for key in ("server", "mt5_server", "name", "code"):
            values.update(_server_values(value.get(key)))
        for key in ("servers", "supported_servers", "supported_mt5_servers", "mt5_servers", "broker_servers"):
            values.update(_server_values(value.get(key)))
        return values
    if isinstance(value, (list, tuple, set)):
        for item in value:
            values.update(_server_values(item))
        return values
    raw = str(value or "").strip()
    if not raw:
        return values
    for item in _split_items(raw):
        if item:
            values.add(item.strip().lower())
            compact = _compact(item)
            if compact:
                values.add(compact)
    return values


def _broker_values_match_route(values: set[str], policy: BrokerRoutePolicy) -> bool:
    route_key = policy.route_key
    if "*" in values or route_key in values:
        return True
    aliases = {str(item or "") for item in policy.aliases.get(route_key, frozenset())}
    aliases.add(route_key)
    for value in values:
        if not value:
            continue
        if value in aliases:
            return True
        if any(alias and (alias in value or value in alias) for alias in aliases):
            return True
    return False


def _dict_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _slot_sources(slot: Mapping[str, Any]) -> list[dict[str, Any]]:
    slot_data = dict(slot or {})
    metadata = _dict_payload(slot_data.get("metadata_json") or slot_data.get("metadata"))
    inventory = _dict_payload(metadata.get("slot_inventory_entry"))
    runner_metadata = _dict_payload(slot_data.get("runner_metadata_json") or slot_data.get("runner_metadata"))
    runner_capabilities = _dict_payload(slot_data.get("runner_capabilities_json") or slot_data.get("capabilities_json"))
    return [slot_data, metadata, inventory, runner_metadata, runner_capabilities]


def runner_slot_supports_broker_route(slot: Mapping[str, Any], policy: BrokerRoutePolicy | None) -> bool:
    if not policy or not policy.active:
        return True

    route_key = policy.route_key
    runner_id = str((slot or {}).get("runner_id") or "").strip()
    env_allowed = policy.runner_broker_map.get(runner_id)
    if env_allowed is not None:
        return "*" in env_allowed or route_key in env_allowed

    sources = _slot_sources(slot)
    broker_values: set[str] = set()
    server_values: set[str] = set()
    has_signal = False
    for source in sources:
        for key in (
            "runner_pool",
            "pool",
            "broker_pool",
            "broker",
            "broker_key",
            "broker_route",
            "broker_route_key",
            "supported_brokers",
            "supported_broker_keys",
            "broker_keys",
            "brokers",
        ):
            if key in source:
                has_signal = True
                broker_values.update(_normalized_values(source.get(key)))
        for key in ("supported_mt5_servers", "supported_servers", "mt5_servers", "broker_servers", "server", "mt5_server"):
            if key in source:
                has_signal = True
                server_values.update(_server_values(source.get(key)))

        tags = source.get("capability_tags") or source.get("tags") or source.get("runner_tags")
        for raw_tag in _split_items(tags):
            tag = str(raw_tag or "").strip().lower()
            if not tag:
                continue
            if tag.startswith(("broker:", "pool:", "broker=")):
                has_signal = True
                broker_values.add(_compact(tag.split(":", 1)[-1].split("=", 1)[-1]))

    server = str(policy.server or "").strip().lower()
    server_compact = _compact(policy.server)
    if server and (server in server_values or server_compact in server_values):
        return True
    if _broker_values_match_route(broker_values, policy):
        return True
    if has_signal:
        return False
    if policy.runner_broker_map and policy.strict:
        return False
    if policy.require_capability:
        return False
    return True
