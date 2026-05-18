from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from app.api.v2.control_plane_deps import service_dep, translate_control_plane_error, user_dep
from app.api.v2.terms_deps import require_miniapp_terms_accepted
from app.models.control_plane import CommandType
from app.schemas.control_plane import (
    DeploymentCommandRequest,
    DeploymentConfigUpdateRequest,
    DeploymentStartRequest,
    DeploymentStopRequest,
)
from app.services.bot_token_license import BotTokenLicenseError, BotTokenLicenseService
from app.services.control_plane_service import MT5ControlPlaneService
from app.services.miniapp_access import has_miniapp_full_access
from app.services.store_service import get_store

router = APIRouter(prefix="/deployments", tags=["mt5-deployments"])
log = logging.getLogger(__name__)


def bot_token_license_dep(request: Request) -> BotTokenLicenseService:
    return BotTokenLicenseService(get_store(request))


def _translate_bot_token_error(exc: Exception) -> HTTPException:
    detail = str(exc) or exc.__class__.__name__
    status_code = 403
    if detail in {
        "bot_token_not_found",
        "bot_token_already_used",
        "bot_token_expired",
        "bot_token_revoked",
        "bot_token_bot_package_not_found",
        "bot_token_duration_invalid",
    }:
        status_code = 400
    return HTTPException(status_code=status_code, detail=detail)


@router.post("/start")
async def start_deployment(
    payload: DeploymentStartRequest,
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
    bot_token_license: BotTokenLicenseService = Depends(bot_token_license_dep),
    _terms_accepted: None = Depends(require_miniapp_terms_accepted),
) -> dict:
    del _terms_accepted
    if payload.lot_size is None:
        return {
            "deployment_id": None,
            "runner_id": None,
            "slot_id": None,
            "status": "start_skipped",
            "reason": "lot_size_required",
            "message": "Account đã được lưu. Hãy bật bot từ panel điều khiển sau khi chọn lot.",
        }
    entitlement_id = str(payload.entitlement_id or "").strip()
    full_access = has_miniapp_full_access(user["telegram_id"])
    if not entitlement_id and not full_access:
        raise HTTPException(status_code=403, detail="bot_token_required")

    user_row = service.ensure_user(telegram_id=str(user["telegram_id"]), username=user.get("username"))
    account = service.get_account(
        telegram_id=str(user["telegram_id"]),
        username=user.get("username"),
        account_id=payload.account_id,
    )
    if not account:
        raise translate_control_plane_error(ValueError("account_not_found"))
    bot = service.get_bot(bot_name=payload.bot_name, force_sync=False)
    if not bot:
        raise translate_control_plane_error(ValueError("bot_not_found"))

    if not full_access:
        try:
            bot_token_license.assert_active_entitlement(
                entitlement_id=entitlement_id,
                telegram_id=str(user["telegram_id"]),
                user_id=int(user_row["id"]),
                account_id=payload.account_id,
                bot_name=payload.bot_name,
                bot_code=bot.get("bot_code"),
            )
        except BotTokenLicenseError as exc:
            raise _translate_bot_token_error(exc) from exc

    try:
        result = await service.start_deployment(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
            account_id=payload.account_id,
            bot_name=payload.bot_name,
            bot_config_overrides=payload.merged_bot_config_overrides(),
            mode=payload.mode,
        )
    except Exception as exc:
        http_exc = translate_control_plane_error(exc)
        log.warning(
            "deployment_start_rejected telegram_id=%s account_id=%s bot_name=%s lot_size=%s mode=%s status=%s detail=%s exc_type=%s",
            user.get("telegram_id"),
            payload.account_id,
            payload.bot_name,
            payload.lot_size,
            payload.mode,
            http_exc.status_code,
            http_exc.detail,
            type(exc).__name__,
        )
        raise http_exc from exc
    deployment = result.get("deployment") or {}
    deployment_id = deployment.get("id")
    if deployment_id is not None and not full_access:
        try:
            bot_token_license.bind_deployment(
                entitlement_id=entitlement_id,
                deployment_id=int(deployment_id),
            )
        except BotTokenLicenseError as exc:
            log.error(
                "deployment_license_bind_failed telegram_id=%s account_id=%s deployment_id=%s entitlement_id=%s detail=%s",
                user.get("telegram_id"),
                payload.account_id,
                deployment_id,
                entitlement_id,
                str(exc),
            )
            try:
                await service.stop_deployment(
                    telegram_id=str(user["telegram_id"]),
                    username=user.get("username"),
                    deployment_id=int(deployment_id),
                    reason="bot_token_bind_failed",
                )
            except Exception as stop_exc:
                log.warning(
                    "deployment_license_bind_cleanup_failed deployment_id=%s detail=%s",
                    deployment_id,
                    str(stop_exc)[:180],
                )
            raise _translate_bot_token_error(exc) from exc
    scheduler = result.get("scheduler") or {}
    return {
        "deployment_id": deployment.get("id"),
        "runner_id": scheduler.get("runner_id"),
        "slot_id": scheduler.get("slot_id"),
        "status": deployment.get("status"),
        **result,
    }


@router.post("/stop")
async def stop_deployment(
    payload: DeploymentStopRequest,
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    try:
        result = await service.stop_deployment(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
            deployment_id=payload.deployment_id,
            reason=payload.reason,
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc
    deployment = result.get("deployment") or {}
    return {"status": deployment.get("status"), **result}


@router.post("/{deployment_id}/cancel")
async def cancel_pending_deployment(
    deployment_id: int,
    payload: Optional[dict] = Body(default=None),
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    """Huy 1 deployment dang ket o status start_requested/starting.

    - Chi cho phep cancel khi deployment chua thuc su running. Khi running -> dung POST /stop.
    - Idempotent o tang DB: deployment da stopped/failed se raise deployment_cannot_be_cancelled.
    - Best-effort phat them STOP_BOT priority cao cho runner skip; flag `command_dispatched` cho FE.
    """
    reason: Optional[str] = None
    if isinstance(payload, dict):
        raw_reason = payload.get("reason")
        if isinstance(raw_reason, str) and raw_reason.strip():
            reason = raw_reason.strip()[:200]
    try:
        result = await service.cancel_deployment(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
            deployment_id=deployment_id,
            reason=reason,
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc
    deployment = result.get("deployment") or {}
    return {
        "deployment_id": deployment.get("id") or deployment_id,
        "status": deployment.get("status"),
        **result,
    }


@router.patch("/{deployment_id}/config")
async def update_deployment_config(
    deployment_id: int,
    payload: DeploymentConfigUpdateRequest,
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    try:
        result = await service.update_deployment_config(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
            deployment_id=deployment_id,
            bot_config_overrides=payload.merged_bot_config_overrides(),
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc
    deployment = result.get("deployment") or {}
    return {
        "deployment_id": deployment.get("id") or deployment_id,
        "status": deployment.get("status"),
        **result,
    }


@router.post("/{deployment_id}/commands")
async def send_deployment_command(
    deployment_id: int,
    payload: DeploymentCommandRequest,
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    if payload.command_type not in {
        CommandType.PLACE_ORDER,
        CommandType.MODIFY_ORDER,
        CommandType.CLOSE_ORDER,
        CommandType.SYNC_STATE,
    }:
        raise HTTPException(status_code=400, detail="unsupported_runtime_command")
    try:
        result = await service.send_deployment_command(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
            deployment_id=deployment_id,
            command_type=payload.command_type,
            payload=payload.payload,
            priority=payload.priority,
            trace_id=payload.trace_id,
            command_id=payload.command_id,
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc
    return result


@router.get("")
async def list_deployments(
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    return {
        "items": service.list_deployments(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
        )
    }


@router.get("/{deployment_id}")
async def get_deployment(
    deployment_id: int,
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    deployment = service.get_deployment(
        telegram_id=str(user["telegram_id"]),
        username=user.get("username"),
        deployment_id=deployment_id,
    )
    if not deployment:
        raise HTTPException(status_code=404, detail="deployment_not_found")
    return deployment


@router.get("/{deployment_id}/commands")
async def list_deployment_commands(
    deployment_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    return {
        "items": service.list_deployment_commands(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
            deployment_id=deployment_id,
            limit=limit,
        )
    }


@router.get("/{deployment_id}/events")
async def list_deployment_events(
    deployment_id: int,
    limit: int = Query(default=200, ge=1, le=1000),
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    return {
        "items": service.list_deployment_events(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
            deployment_id=deployment_id,
            limit=limit,
        )
    }


@router.get("/{deployment_id}/performance")
async def get_deployment_performance(
    deployment_id: int,
    days: int = Query(default=30, ge=1, le=365),
    tz_offset_min: int = Query(default=0, ge=-840, le=840),
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    """Performance metrics tu execution_events ORDER_FILLED.

    Tra: total_trades, win_rate, profit_factor, max_drawdown, gross_win/loss,
    average_win/loss, daily_pnl_series (last `days` days), first/last_trade_at.

    Realized PnL only - unrealized lay tu SSE stream / position snapshots.
    """
    try:
        return service.get_deployment_performance(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
            deployment_id=deployment_id,
            days_window=days,
            tz_offset_min=tz_offset_min,
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc


@router.get("/{deployment_id}/audit")
async def list_deployment_audit(
    deployment_id: int,
    limit: int = Query(default=200, ge=1, le=1000),
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    return {
        "items": service.list_deployment_audit(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
            deployment_id=deployment_id,
            limit=limit,
        )
    }


@router.get("/{deployment_id}/logs")
async def list_deployment_logs(
    deployment_id: int,
    limit: int = Query(default=200, ge=1, le=1000),
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    return {
        "items": service.list_deployment_logs(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
            deployment_id=deployment_id,
            limit=limit,
        )
    }
