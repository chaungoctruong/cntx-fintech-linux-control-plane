from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Optional


_BOT_IDENTITIES = {"gsalgo", "gsalgomt5bot"}
_OPERATIONS = {
    "store_candle",
    "has_candle",
    "store_event",
    "has_order_intent",
    "store_order_intent",
    "store_execution",
    "store_pending_entry",
    "close_pending_entry",
    "load_active_pending_entry",
    "store_trade",
    "daily_realized_pnl",
    "consecutive_losses",
    "store_health",
}
_RECORD_TYPES = {
    "store_candle": "candle",
    "has_candle": "candle",
    "store_event": "event",
    "has_order_intent": "order_intent",
    "store_order_intent": "order_intent",
    "store_execution": "execution",
    "store_pending_entry": "pending_entry",
    "close_pending_entry": "pending_entry",
    "load_active_pending_entry": "pending_entry",
    "store_trade": "trade",
    "daily_realized_pnl": "trade",
    "consecutive_losses": "trade",
    "store_health": "health",
}
_SENSITIVE_KEY_TOKENS = (
    "password",
    "passwd",
    "pwd",
    "token",
    "secret",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "postgres_dsn",
    "state_dsn",
    "dsn",
    "database_url",
    "redis_url",
)
_SENSITIVE_VALUE_TOKENS = (
    "postgresql://",
    "postgres://",
    "redis://",
    "bearer ",
)


def _norm_text(value: Any) -> str:
    return str(value or "").strip()


def _norm_bot_identity(value: Any) -> str:
    return "".join(ch for ch in _norm_text(value).lower() if ch.isalnum())


def _canonical_slot_id(value: Any) -> str:
    raw = _norm_text(value)
    if not raw:
        return ""
    lowered = raw.lower()
    if lowered.startswith("slot_") or lowered.startswith("slot-"):
        return f"slot-{raw[5:]}"
    return raw


def _is_gsalgo_identity(value: Any) -> bool:
    return _norm_bot_identity(value) in _BOT_IDENTITIES


def _int_or_none(value: Any) -> Optional[int]:
    try:
        if value is None or str(value).strip() == "":
            return None
        return int(value)
    except Exception:
        return None


def _float_or_none(value: Any) -> Optional[float]:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except Exception:
        return None


def _contains_secret(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = str(key or "").strip().lower().replace("-", "_")
            if any(token in normalized_key for token in _SENSITIVE_KEY_TOKENS):
                return True
            if _contains_secret(item):
                return True
        return False
    if isinstance(value, (list, tuple, set)):
        return any(_contains_secret(item) for item in value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        return any(token in lowered for token in _SENSITIVE_VALUE_TOKENS)
    return False


def _payload_sources(payload: dict[str, Any], record_type: str) -> list[dict[str, Any]]:
    sources = [payload]
    nested_keys = (
        record_type,
        "data",
        "record",
        "candle",
        "event",
        "order_intent",
        "intent",
        "execution",
        "pending_entry",
        "entry",
        "trade",
        "health",
    )
    for key in nested_keys:
        nested = payload.get(key)
        if isinstance(nested, dict) and nested not in sources:
            sources.append(nested)
    return sources


def _lookup(payload: dict[str, Any], record_type: str, *keys: str) -> Any:
    for source in _payload_sources(payload, record_type):
        for key in keys:
            if key in source and source.get(key) is not None:
                return source.get(key)
    return None


def _payload_hash(payload: dict[str, Any]) -> str:
    try:
        rendered = json.dumps(payload or {}, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        rendered = repr(payload)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _timestamp(value: Any) -> Optional[str]:
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
        except Exception:
            return None
    raw = str(value).strip()
    try:
        if raw.isdigit():
            return datetime.fromtimestamp(float(raw), tz=timezone.utc).isoformat()
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.isoformat()
    except Exception:
        return None


def _day(value: Any) -> str:
    timestamp = _timestamp(value)
    if timestamp:
        return datetime.fromisoformat(timestamp).date().isoformat()
    raw = str(value or "").strip()
    if raw:
        try:
            return datetime.fromisoformat(raw[:10]).date().isoformat()
        except Exception:
            pass
    return datetime.now(timezone.utc).date().isoformat()


def _record_key(
    *,
    record_type: str,
    context: dict[str, Any],
    payload: dict[str, Any],
    allow_missing: bool = False,
) -> Optional[str]:
    key_fields = {
        "candle": ("candle_id", "candle_key", "bar_id", "bar_key", "id"),
        "order_intent": ("intent_id", "order_intent_id", "client_intent_id", "client_order_id", "signal_id", "id"),
        "execution": ("execution_id", "deal_id", "order_id", "ticket", "id"),
        "pending_entry": ("pending_entry_id", "entry_id", "signal_id", "order_intent_id", "id"),
        "trade": ("trade_id", "deal_id", "order_id", "ticket", "position_id", "id"),
        "event": ("event_id", "id"),
        "health": ("health_id", "id"),
    }
    for key in key_fields.get(record_type, ("id",)):
        value = _lookup(payload, record_type, key)
        if value is not None and str(value).strip():
            return f"{record_type}:{str(value).strip()}"

    if record_type == "candle":
        symbol = _norm_text(_lookup(payload, record_type, "symbol"))
        timeframe = _norm_text(_lookup(payload, record_type, "timeframe", "tf", "period"))
        candle_time = _norm_text(_lookup(payload, record_type, "timestamp", "time", "open_time", "candle_time"))
        if symbol and timeframe and candle_time:
            return f"candle:{symbol}:{timeframe}:{candle_time}"

    if record_type == "pending_entry" and allow_missing:
        return None

    if record_type in {"pending_entry", "health"}:
        return f"{record_type}:{context['account_id']}:{context['deployment_id']}"

    if payload:
        return f"{record_type}:sha256:{_payload_hash(payload)}"
    return None


def _symbol(payload: dict[str, Any], record_type: str) -> Optional[str]:
    return _norm_text(_lookup(payload, record_type, "symbol", "instrument")) or None


def _side(payload: dict[str, Any], record_type: str) -> Optional[str]:
    return _norm_text(_lookup(payload, record_type, "side", "direction", "type")) or None


def _status(payload: dict[str, Any], record_type: str, *, default: str) -> str:
    return _norm_text(_lookup(payload, record_type, "status", "state")) or default


def _realized_pnl(payload: dict[str, Any], record_type: str) -> Optional[float]:
    value = _lookup(
        payload,
        record_type,
        "realized_pnl",
        "closed_pnl",
        "net_pnl",
        "pnl",
        "profit",
        "realized_profit",
    )
    return _float_or_none(value)


def _occurred_at(payload: dict[str, Any], record_type: str) -> Optional[str]:
    value = _lookup(
        payload,
        record_type,
        "occurred_at",
        "event_at",
        "closed_at",
        "executed_at",
        "timestamp",
        "time",
        "open_time",
    )
    return _timestamp(value)


class GsAlgoBackendStateService:
    def __init__(self, repo: Any) -> None:
        self._repo = repo

    def handle(self, *, operation: str, context: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        operation_s = _norm_text(operation).lower()
        if operation_s not in _OPERATIONS:
            return {"ok": False, "error": "unsupported_operation"}

        raw_context = dict(context or {})
        raw_payload = dict(payload or {})
        if _contains_secret(raw_context) or _contains_secret(raw_payload):
            return {"ok": False, "error": "secret_fields_not_allowed"}

        account_id = _int_or_none(raw_context.get("account_id"))
        deployment_id = _int_or_none(raw_context.get("deployment_id"))
        runner_id = _norm_text(raw_context.get("runner_id"))
        slot_id = _canonical_slot_id(raw_context.get("slot_id"))
        bot_id = _norm_text(raw_context.get("bot_id") or "gsalgo_mt5_bot")
        if not runner_id or not slot_id or account_id is None or deployment_id is None:
            return {"ok": False, "error": "invalid_context"}
        if not _is_gsalgo_identity(bot_id):
            return {"ok": False, "error": "bot_id_not_supported"}

        normalized_context = {
            "runner_id": runner_id,
            "slot_id": slot_id,
            "account_id": account_id,
            "deployment_id": deployment_id,
            "bot_id": "gsalgo_mt5_bot",
            "schema": _norm_text(raw_context.get("schema") or raw_context.get("schema_name"))
            or "gsalgo_backend_state.v1",
        }
        record_type = _RECORD_TYPES[operation_s]

        if operation_s in {"has_candle", "has_order_intent"}:
            return self._exists(record_type=record_type, context=normalized_context, payload=raw_payload)
        if operation_s == "load_active_pending_entry":
            data = self._repo.load_active_runner_bot_state_pending_entry(context=normalized_context)
            return {"ok": True, "exists": bool(data), "data": data or {}}
        if operation_s == "close_pending_entry":
            return self._close_pending(context=normalized_context, payload=raw_payload, record_type=record_type)
        if operation_s == "daily_realized_pnl":
            day = _day(_lookup(raw_payload, record_type, "date", "day", "trading_day", "closed_date"))
            data = self._repo.sum_runner_bot_state_realized_pnl(context=normalized_context, day=day)
            return {"ok": True, "data": data}
        if operation_s == "consecutive_losses":
            limit = _int_or_none(_lookup(raw_payload, record_type, "limit", "lookback"))
            data = self._repo.count_runner_bot_state_consecutive_losses(
                context=normalized_context,
                limit=limit or 100,
            )
            return {"ok": True, "data": data}

        return self._store(operation=operation_s, context=normalized_context, payload=raw_payload, record_type=record_type)

    def _exists(self, *, record_type: str, context: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        record_key = _record_key(record_type=record_type, context=context, payload=payload)
        if not record_key:
            return {"ok": False, "error": "invalid_record_key"}
        return {
            "ok": True,
            "exists": self._repo.runner_bot_state_record_exists(
                record_type=record_type,
                context=context,
                record_key=record_key,
            ),
        }

    def _close_pending(self, *, context: dict[str, Any], payload: dict[str, Any], record_type: str) -> dict[str, Any]:
        record_key = _record_key(record_type=record_type, context=context, payload=payload, allow_missing=True)
        data = self._repo.close_runner_bot_state_pending_entry(
            context=context,
            record_key=record_key,
            payload=payload,
            closed_at=_occurred_at(payload, record_type),
        )
        return {"ok": True, "exists": bool(data), "data": data or {}}

    def _store(
        self,
        *,
        operation: str,
        context: dict[str, Any],
        payload: dict[str, Any],
        record_type: str,
    ) -> dict[str, Any]:
        default_status = {
            "store_pending_entry": "active",
            "store_execution": "closed",
            "store_trade": "closed",
            "store_health": "latest",
        }.get(operation, "recorded")
        record_key = _record_key(record_type=record_type, context=context, payload=payload)
        if not record_key:
            return {"ok": False, "error": "invalid_record_key"}
        data = self._repo.upsert_runner_bot_state_record(
            operation=operation,
            record_type=record_type,
            context=context,
            record_key=record_key,
            payload=payload,
            status=_status(payload, record_type, default=default_status),
            symbol=_symbol(payload, record_type),
            side=_side(payload, record_type),
            realized_pnl=_realized_pnl(payload, record_type),
            occurred_at=_occurred_at(payload, record_type),
        )
        if operation == "store_health":
            self._record_health_snapshot(context=context, payload=payload)
        return {"ok": True, "data": data}

    def _record_health_snapshot(self, *, context: dict[str, Any], payload: dict[str, Any]) -> None:
        heartbeat_payload = {"source": "gsalgo_backend_state", **dict(payload or {})}
        toucher = getattr(self._repo, "touch_deployment_heartbeat", None)
        if callable(toucher):
            toucher(
                deployment_id=int(context["deployment_id"]),
                account_id=int(context["account_id"]),
                runner_id=str(context["runner_id"]),
                slot_id=str(context["slot_id"]),
                payload=heartbeat_payload,
            )

        snapshotter = getattr(self._repo, "upsert_account_state_snapshot", None)
        if callable(snapshotter):
            snapshotter(
                account_id=int(context["account_id"]),
                deployment_id=int(context["deployment_id"]),
                runner_id=str(context["runner_id"]),
                slot_id=str(context["slot_id"]),
                connection_status=_norm_text(payload.get("connection_status") or payload.get("status")) or "connected",
                pnl=_float_or_none(payload.get("pnl")),
                balance=_float_or_none(payload.get("balance")),
                equity=_float_or_none(payload.get("equity")),
                free_margin=_float_or_none(payload.get("free_margin")),
                payload=heartbeat_payload,
            )
