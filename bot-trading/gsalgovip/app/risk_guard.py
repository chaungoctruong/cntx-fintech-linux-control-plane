from __future__ import annotations

from .models import TradingViewSignal


class ValidationError(ValueError):
    pass


ALLOWED_STRATEGY_NAMES = {"gsalgovip_v1"}
ALLOWED_TIMEFRAMES = {"M1", "1"}


def validate_signal(signal: TradingViewSignal) -> None:
    if signal.source != "tradingview":
        raise ValidationError("source_invalid")
    if signal.strategy not in ALLOWED_STRATEGY_NAMES:
        raise ValidationError("strategy_invalid")
    if signal.event_type != "ENTRY":
        raise ValidationError("event_type_invalid")
    if signal.timeframe.upper() not in ALLOWED_TIMEFRAMES:
        raise ValidationError("timeframe_not_supported")
    if signal.side not in {"BUY", "SELL"}:
        raise ValidationError("side_invalid")
    if not signal.is_confirmed:
        raise ValidationError("bar_not_confirmed")
    if not signal.config_key:
        raise ValidationError("config_key_missing")
    if not signal.nonce:
        raise ValidationError("nonce_missing")
    if signal.bar_time_ms <= 0:
        raise ValidationError("bar_time_invalid")

    if signal.side == "BUY" and not (signal.sl < signal.entry < signal.tp):
        raise ValidationError("buy_geometry_invalid")
    if signal.side == "SELL" and not (signal.tp < signal.entry < signal.sl):
        raise ValidationError("sell_geometry_invalid")
