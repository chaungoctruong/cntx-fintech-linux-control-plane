"""TradingView alert ingress (public). Fast response; no secrets in logs."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from app.api.v2.control_plane_deps import service_dep, translate_control_plane_error
from app.services.control_plane_service import MT5ControlPlaneService

router = APIRouter(prefix="/public/tradingview", tags=["public-tradingview"])
log = logging.getLogger(__name__)


def _tradingview_secret_header(*, x_secret: str | None, authorization: str | None) -> str:
    explicit = str(x_secret or "").strip()
    if explicit:
        return explicit
    raw = str(authorization or "").strip()
    if raw.lower().startswith("bearer "):
        return raw.split(" ", 1)[1].strip()
    return raw


async def _json_object_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_json")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid_request")
    return body


async def _dispatch_broadcast_request(
    *,
    request: Request,
    service: MT5ControlPlaneService,
    x_tradingview_secret: str | None,
    authorization: str | None,
    secret: str | None,
    path_signal_id: str = "",
) -> dict[str, Any]:
    body = await _json_object_body(request)
    signal_id = str(path_signal_id or "").strip()
    if signal_id:
        body_signal_id = str(body.get("signal_id") or "").strip()
        if body_signal_id and body_signal_id != signal_id:
            raise HTTPException(status_code=400, detail="tradingview_signal_id_mismatch")
        body = {**body, "signal_id": signal_id}
    try:
        return await service.dispatch_tradingview_broadcast(
            body=body,
            query_secret=str(secret or ""),
            header_secret=_tradingview_secret_header(
                x_secret=x_tradingview_secret,
                authorization=authorization,
            ),
        )
    except Exception as exc:
        log.warning("tradingview_broadcast_rejected error=%s", type(exc).__name__)
        raise translate_control_plane_error(exc) from exc


@router.post("/alert")
async def tradingview_alert(
    request: Request,
    service: MT5ControlPlaneService = Depends(service_dep),
    x_tradingview_secret: str | None = Header(default=None, alias="X-TradingView-Secret"),
    authorization: str | None = Header(default=None, alias="Authorization"),
    secret: str | None = Query(default=None, description="Optional shared secret if not sent in header"),
) -> dict[str, Any]:
    body = await _json_object_body(request)

    try:
        return await service.dispatch_tradingview_alert(
            body=body,
            query_secret=str(secret or ""),
            header_secret=_tradingview_secret_header(
                x_secret=x_tradingview_secret,
                authorization=authorization,
            ),
        )
    except Exception as exc:
        log.warning("tradingview_alert_rejected error=%s", type(exc).__name__)
        raise translate_control_plane_error(exc) from exc


@router.post("/broadcast")
async def tradingview_broadcast(
    request: Request,
    service: MT5ControlPlaneService = Depends(service_dep),
    x_tradingview_secret: str | None = Header(default=None, alias="X-TradingView-Secret"),
    authorization: str | None = Header(default=None, alias="Authorization"),
    secret: str | None = Query(default=None, description="Optional shared secret if not sent in header"),
) -> dict[str, Any]:
    """Fan-out 1 TradingView signal to N subscribers via Redis pipeline batch.

    Body shape (minimal):
      { "alert_id": "...", "signal_id": "...", "action": "BUY|SELL|CLOSE",
        "symbol": "EURUSD", "default_volume": 0.01 }

    See `MT5ControlPlaneService.dispatch_tradingview_broadcast` docstring for
    the full contract. Idempotent — safe for TradingView retries.
    """
    return await _dispatch_broadcast_request(
        request=request,
        service=service,
        x_tradingview_secret=x_tradingview_secret,
        authorization=authorization,
        secret=secret,
    )


@router.post("/broadcast/{signal_id}")
async def tradingview_broadcast_for_signal(
    signal_id: str,
    request: Request,
    service: MT5ControlPlaneService = Depends(service_dep),
    x_tradingview_secret: str | None = Header(default=None, alias="X-TradingView-Secret"),
    authorization: str | None = Header(default=None, alias="Authorization"),
    secret: str | None = Query(default=None, description="Optional shared secret if not sent in header"),
) -> dict[str, Any]:
    """Dedicated TradingView URL for one signal_id.

    Example:
      /api/v2/public/tradingview/broadcast/gsalgovip-xauusd

    The JSON body may omit signal_id. If it includes signal_id, it must match
    the path to avoid accidentally routing a signal to the wrong product.
    """
    return await _dispatch_broadcast_request(
        request=request,
        service=service,
        x_tradingview_secret=x_tradingview_secret,
        authorization=authorization,
        secret=secret,
        path_signal_id=signal_id,
    )
