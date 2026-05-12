"""TradingView alert ingress (public). Fast response; no secrets in logs."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from app.api.v2.control_plane_deps import service_dep, translate_control_plane_error
from app.services.control_plane_service import MT5ControlPlaneService

router = APIRouter(prefix="/public/tradingview", tags=["public-tradingview"])
log = logging.getLogger(__name__)


@router.post("/alert")
async def tradingview_alert(
    request: Request,
    service: MT5ControlPlaneService = Depends(service_dep),
    x_tradingview_secret: str | None = Header(default=None, alias="X-TradingView-Secret"),
    secret: str | None = Query(default=None, description="Optional shared secret if not sent in header"),
) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_json")

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid_request")

    try:
        return await service.dispatch_tradingview_alert(
            body=body,
            query_secret=str(secret or ""),
            header_secret=str(x_tradingview_secret or ""),
        )
    except Exception as exc:
        log.warning("tradingview_alert_rejected error=%s", type(exc).__name__)
        raise translate_control_plane_error(exc) from exc


@router.post("/broadcast")
async def tradingview_broadcast(
    request: Request,
    service: MT5ControlPlaneService = Depends(service_dep),
    x_tradingview_secret: str | None = Header(default=None, alias="X-TradingView-Secret"),
    secret: str | None = Query(default=None, description="Optional shared secret if not sent in header"),
) -> dict[str, Any]:
    """Fan-out 1 TradingView signal to N subscribers via Redis pipeline batch.

    Body shape (minimal):
      { "alert_id": "...", "signal_id": "...", "action": "BUY|SELL|CLOSE",
        "symbol": "EURUSD", "default_volume": 0.01 }

    See `MT5ControlPlaneService.dispatch_tradingview_broadcast` docstring for
    the full contract. Idempotent — safe for TradingView retries.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_json")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid_request")

    try:
        return await service.dispatch_tradingview_broadcast(
            body=body,
            query_secret=str(secret or ""),
            header_secret=str(x_tradingview_secret or ""),
        )
    except Exception as exc:
        log.warning("tradingview_broadcast_rejected error=%s", type(exc).__name__)
        raise translate_control_plane_error(exc) from exc
