from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


@dataclass(frozen=True)
class ResolvedClosePosition:
    row: dict[str, Any]
    ticket: int
    position_key: str
    volume: float | None


def mt5_ticket_int(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or not re.fullmatch(r"\d+", text):
        return None
    ticket = int(text)
    return ticket if ticket > 0 else None


def position_snapshot_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload_json")
    return payload if isinstance(payload, dict) else {}


def position_snapshot_ticket(row: dict[str, Any]) -> int | None:
    payload = position_snapshot_payload(row)
    for key in (
        "ticket",
        "position_ticket",
        "mt5_position_id",
        "position_id",
        "position",
        "id",
    ):
        ticket = mt5_ticket_int(payload.get(key))
        if ticket is not None:
            return ticket
    return mt5_ticket_int(row.get("position_key"))


def position_snapshot_volume(row: dict[str, Any]) -> float | None:
    payload = position_snapshot_payload(row)
    for value in (
        row.get("volume"),
        payload.get("volume"),
        payload.get("volume_current"),
        payload.get("current_volume"),
        payload.get("lots"),
    ):
        if value is None or str(value).strip() == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _norm_symbol(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").strip().upper())


def _close_kind_side(close_kind: Any) -> str:
    raw = str(close_kind or "").strip().upper()
    if raw in {"CLOSE_BUY", "CLOSE_LONG"}:
        return "buy"
    if raw in {"CLOSE_SELL", "CLOSE_SHORT"}:
        return "sell"
    return ""


def _is_open_position(row: dict[str, Any]) -> bool:
    payload = position_snapshot_payload(row)
    status = str(payload.get("status") or payload.get("state") or "").strip().lower()
    if status in {"closed", "close", "done", "deleted", "removed", "inactive"}:
        return False
    volume = position_snapshot_volume(row)
    return volume is None or volume > 0


def _matches_close(row: dict[str, Any], *, symbol: str, close_kind: str) -> bool:
    if not _is_open_position(row):
        return False
    payload = position_snapshot_payload(row)
    if symbol:
        row_symbol = str(row.get("symbol") or payload.get("symbol") or "").strip()
        if _norm_symbol(row_symbol) != _norm_symbol(symbol):
            return False
    wanted_side = _close_kind_side(close_kind)
    if wanted_side:
        side = str(row.get("side") or payload.get("side") or "").strip().lower()
        if side == "long":
            side = "buy"
        elif side == "short":
            side = "sell"
        if not side or side != wanted_side:
            return False
    return position_snapshot_ticket(row) is not None


def resolve_close_positions(
    snapshots: list[dict[str, Any]],
    *,
    symbol: str,
    close_kind: str,
) -> list[ResolvedClosePosition]:
    """Return open MT5 positions that can be closed by ticket.

    Trading execution must close by explicit ticket/position id. This helper
    deliberately ignores magic-only or symbol-only closes so a TradingView
    broadcast cannot accidentally close the wrong position.
    """
    out: list[ResolvedClosePosition] = []
    seen_tickets: set[int] = set()
    for snapshot in snapshots:
        if not _matches_close(snapshot, symbol=symbol, close_kind=close_kind):
            continue
        ticket = position_snapshot_ticket(snapshot)
        if ticket is None or ticket in seen_tickets:
            continue
        seen_tickets.add(ticket)
        out.append(
            ResolvedClosePosition(
                row=snapshot,
                ticket=ticket,
                position_key=str(snapshot.get("position_key") or ticket).strip(),
                volume=position_snapshot_volume(snapshot),
            )
        )
    return out
