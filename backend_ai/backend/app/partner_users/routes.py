from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.v2.control_plane_deps import service_dep
from app.services.control_plane_service import MT5ControlPlaneService

from . import audit
from .deps import current_partner_user, require_internal_key, verify_jwt_and_state
from .schemas import (
    BotActionRequest,
    BotActionResponse,
    BotInfoResponse,
    LinkAccountRequest,
    LinkAccountResponse,
    LoginRequest,
    LoginResponse,
    PartnerUserContext,
)
from .service import PartnerUserService


router = APIRouter(prefix="/partner-user", tags=["partner-user"])


def _make_service(svc: MT5ControlPlaneService) -> PartnerUserService:
    return PartnerUserService(svc)


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest) -> LoginResponse:
    """Verify JWT (signature + Redis state). Trả claims nếu valid.

    Không tạo session server-side. JWT chính là session — frontend lưu
    localStorage và gửi qua Authorization header cho mọi request sau.
    """
    ctx = verify_jwt_and_state(payload.token)
    audit.login_ok(jti=ctx.jti, partner_id=ctx.partner_id, end_user_label=ctx.end_user_label)
    return LoginResponse(ok=True, me=ctx)


@router.get("/me", response_model=PartnerUserContext)
def me(ctx: PartnerUserContext = Depends(current_partner_user)) -> PartnerUserContext:
    return ctx


@router.get("/bot", response_model=BotInfoResponse)
def bot_info(
    ctx: PartnerUserContext = Depends(current_partner_user),
    svc: MT5ControlPlaneService = Depends(service_dep),
) -> BotInfoResponse:
    pu = _make_service(svc)
    return BotInfoResponse(me=ctx, bot=pu.status(ctx))


@router.post("/bot/start", response_model=BotActionResponse)
async def bot_start(
    payload: BotActionRequest | None = None,
    ctx: PartnerUserContext = Depends(current_partner_user),
    svc: MT5ControlPlaneService = Depends(service_dep),
) -> BotActionResponse:
    pu = _make_service(svc)
    payload = payload or BotActionRequest()
    result = await pu.start(
        ctx,
        lot_size=payload.lot_size,
        config_overrides=payload.config_overrides,
    )
    return BotActionResponse(
        ok=True,
        action=result["action"],
        deployment=result.get("deployment"),
        bot=pu.status(ctx),
        note=result.get("note"),
    )


@router.post("/bot/stop", response_model=BotActionResponse)
async def bot_stop(
    ctx: PartnerUserContext = Depends(current_partner_user),
    svc: MT5ControlPlaneService = Depends(service_dep),
) -> BotActionResponse:
    pu = _make_service(svc)
    result = await pu.stop(ctx)
    return BotActionResponse(
        ok=True,
        action=result["action"],
        deployment=result.get("deployment"),
        bot=pu.status(ctx),
        note=result.get("note"),
    )


@router.post("/link-account", response_model=LinkAccountResponse)
def link_account(
    payload: LinkAccountRequest,
    ctx: PartnerUserContext = Depends(current_partner_user),
    svc: MT5ControlPlaneService = Depends(service_dep),
) -> LinkAccountResponse:
    """Khách paste JWT + nhập MT5 account_id 1 lần để bind token với account.

    Sau khi link, mọi /bot/start, /bot/stop và auto force-stop khi hết hạn
    đều resolve account_id từ link này.
    """
    pu = PartnerUserService(svc)
    result = pu.link_account(ctx, payload.account_id)
    return LinkAccountResponse(
        ok=True,
        jti=ctx.jti,
        account_id=result["account_id"],
        mt5_login=result.get("mt5_login"),
        broker=result.get("broker"),
        server=result.get("server"),
        linked_at=datetime.fromisoformat(result["linked_at"]),
        note=result.get("note") or "Bot có thể bật/tắt được rồi. Khi token hết hạn, bot sẽ tự dừng.",
    )


class ForceStopRequest(BaseModel):
    jti: str = Field(..., min_length=1, max_length=128)
    reason: str = Field(..., min_length=1, max_length=255)


@router.post("/internal/force-stop", dependencies=[Depends(require_internal_key)])
async def internal_force_stop(
    payload: ForceStopRequest,
    svc: MT5ControlPlaneService = Depends(service_dep),
) -> dict[str, Any]:
    """Token-bot gọi khi lock loop / partner revoke / admin revoke grant → dừng bot.

    Lookup JTI → account_id trong Redis link mapping. Idempotent:
    - JTI chưa link account → noop "no_account_linked"
    - Account không có deployment đang chạy → noop "no_active_deployment"
    - Lỗi dispatch → action="error" thay vì 5xx để token-bot không retry infinite.
    """
    pu = PartnerUserService(svc)
    result = await pu.force_stop_for_jti(jti=payload.jti, reason=payload.reason)
    return {"ok": True, **result}
