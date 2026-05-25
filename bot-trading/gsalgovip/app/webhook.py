from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from .models import TradingViewSignal
from .risk_guard import ValidationError, validate_signal
from .state_store import StateStore


class TradingViewPayload(BaseModel):
    source: str
    strategy: str
    strategy_version: str = "v1"
    event_type: str
    side: str
    symbol: str
    timeframe: str
    entry: float
    sl: float
    tp: float
    sl_value: float
    tp_value: float
    bar_time_ms: int = Field(..., ge=1)
    is_confirmed: bool = True
    config_key: str
    nonce: str
    mode: str | None = None
    webhook_secret: str | None = None
    auth_token: str | None = None


def build_router(store: StateStore, webhook_secret: str) -> APIRouter:
    router = APIRouter()

    @router.post("/webhook/tradingview")
    def intake_tradingview_webhook(
        body: TradingViewPayload,
        x_webhook_secret: str | None = Header(default=None),
    ) -> dict[str, object]:
        if not webhook_secret:
            raise HTTPException(status_code=500, detail="webhook_secret_not_configured")
        provided_secret = (x_webhook_secret or "").strip()
        if not provided_secret:
            provided_secret = (body.webhook_secret or "").strip()
        if not provided_secret:
            provided_secret = (body.auth_token or "").strip()
        if provided_secret != webhook_secret:
            raise HTTPException(status_code=401, detail="invalid_secret")

        signal = TradingViewSignal(
            source=body.source,
            strategy=body.strategy,
            strategy_version=body.strategy_version,
            event_type=body.event_type,
            side=body.side.upper(),  # type: ignore[arg-type]
            symbol=body.symbol,
            timeframe=body.timeframe,
            entry=body.entry,
            sl=body.sl,
            tp=body.tp,
            sl_value=body.sl_value,
            tp_value=body.tp_value,
            bar_time_ms=body.bar_time_ms,
            is_confirmed=body.is_confirmed,
            config_key=body.config_key,
            nonce=body.nonce,
            mode=body.mode,
        )

        try:
            validate_signal(signal)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        inserted, signal_id = store.insert_signal(signal)
        if not inserted:
            return {"status": "duplicate", "signal_id": signal_id}
        return {"status": "accepted", "signal_id": signal_id}

    return router
