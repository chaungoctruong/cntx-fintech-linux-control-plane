"""Performance metrics computer cho 1 deployment.

Doc tu execution_events (event_type='ORDER_FILLED') va tinh:
  - total_trades
  - winning_trades / losing_trades / win_rate
  - total_realized_pnl
  - profit_factor (gross_win / gross_loss)
  - max_drawdown (peak-to-trough)
  - average_win / average_loss
  - daily_pnl_series (last N days, mac dinh 30)
  - first_trade_at / last_trade_at

KHONG tinh unrealized PnL (chỉ realized). Runner co the publish unrealized
qua POSITION_UPDATED event nhung tinh realtime tốt hơn ở FE/SSE.

Field PnL trong payload_json: 'realized_pnl' uu tien, 'closed_pnl' fallback,
'net_pnl' fallback (theo convention runner Windows).
"""
from __future__ import annotations

import math
import time
from typing import Any, Iterable


def _extract_pnl(payload: dict[str, Any]) -> float | None:
    """Try cac key thong dung. Tra None neu khong tim ra."""
    for key in ("realized_pnl", "closed_pnl", "net_pnl", "pnl"):
        v = payload.get(key)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _epoch_to_day(ts: int, *, tz_offset_min: int = 0) -> str:
    """Convert epoch -> 'YYYY-MM-DD' theo timezone offset (UTC mac dinh)."""
    if ts <= 0:
        return ""
    import datetime as _dt

    dt = _dt.datetime.utcfromtimestamp(ts) + _dt.timedelta(minutes=int(tz_offset_min or 0))
    return dt.strftime("%Y-%m-%d")


def _epoch_now() -> int:
    return int(time.time())


def compute_performance_metrics(
    events: Iterable[dict[str, Any]],
    *,
    days_window: int = 30,
    tz_offset_min: int = 0,
) -> dict[str, Any]:
    """Tinh performance metrics tu list event ORDER_FILLED.

    Moi event expected: {created_at_ts: int, payload: dict (chua realized_pnl)}
    Caller chiu trach nhiem filter event_type='ORDER_FILLED' truoc khi truyen.
    """
    pnl_records: list[tuple[int, float]] = []  # (created_at_ts, pnl)
    for event in events or []:
        ts = int(event.get("created_at_ts") or 0)
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        pnl = _extract_pnl(payload)
        if pnl is None:
            continue
        pnl_records.append((ts, float(pnl)))

    total_trades = len(pnl_records)
    if total_trades == 0:
        return {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "breakeven_trades": 0,
            "win_rate": 0.0,
            "total_realized_pnl": 0.0,
            "gross_win": 0.0,
            "gross_loss": 0.0,
            "profit_factor": None,  # khong xac dinh khi khong co loss
            "average_win": 0.0,
            "average_loss": 0.0,
            "max_drawdown": 0.0,
            "max_drawdown_pct": 0.0,
            "first_trade_at": None,
            "last_trade_at": None,
            "daily_pnl_series": [],
            "days_window": int(days_window),
            "tz_offset_min": int(tz_offset_min),
        }

    pnl_records.sort(key=lambda x: x[0])
    winning = [p for _, p in pnl_records if p > 0]
    losing = [p for _, p in pnl_records if p < 0]
    breakeven = total_trades - len(winning) - len(losing)
    total_pnl = sum(p for _, p in pnl_records)
    gross_win = sum(winning)
    gross_loss = abs(sum(losing))
    win_rate = (len(winning) / total_trades) if total_trades else 0.0
    avg_win = (gross_win / len(winning)) if winning else 0.0
    avg_loss = (gross_loss / len(losing)) if losing else 0.0
    profit_factor: float | None
    if gross_loss > 0:
        profit_factor = gross_win / gross_loss
    elif gross_win > 0:
        profit_factor = math.inf
    else:
        profit_factor = None

    # Max drawdown peak-to-trough on equity curve
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for _, p in pnl_records:
        equity += p
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    max_dd_pct = (max_dd / peak * 100.0) if peak > 0 else 0.0

    # Daily series (last days_window days, oldest -> newest)
    by_day: dict[str, float] = {}
    for ts, p in pnl_records:
        day = _epoch_to_day(ts, tz_offset_min=tz_offset_min)
        if day:
            by_day[day] = by_day.get(day, 0.0) + p
    sorted_days = sorted(by_day.keys())[-int(max(1, days_window)):]
    daily_series = [{"day": d, "pnl": round(by_day[d], 4)} for d in sorted_days]

    return {
        "total_trades": total_trades,
        "winning_trades": len(winning),
        "losing_trades": len(losing),
        "breakeven_trades": breakeven,
        "win_rate": round(win_rate, 4),
        "total_realized_pnl": round(total_pnl, 4),
        "gross_win": round(gross_win, 4),
        "gross_loss": round(gross_loss, 4),
        "profit_factor": (
            round(profit_factor, 4)
            if isinstance(profit_factor, float) and not math.isinf(profit_factor)
            else (None if profit_factor is None else "infinity")
        ),
        "average_win": round(avg_win, 4),
        "average_loss": round(avg_loss, 4),
        "max_drawdown": round(max_dd, 4),
        "max_drawdown_pct": round(max_dd_pct, 4),
        "first_trade_at": int(pnl_records[0][0]) if pnl_records else None,
        "last_trade_at": int(pnl_records[-1][0]) if pnl_records else None,
        "daily_pnl_series": daily_series,
        "days_window": int(days_window),
        "tz_offset_min": int(tz_offset_min),
    }
