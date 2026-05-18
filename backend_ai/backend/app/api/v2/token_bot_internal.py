from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.v2.control_plane_deps import service_dep, translate_control_plane_error
from app.partner_users.deps import require_internal_key
from app.services.bot_token_license import BotTokenLicenseError, BotTokenLicenseService
from app.services.control_plane_service import MT5ControlPlaneService
from app.services.store_service import get_process_store


router = APIRouter(prefix="/token-bot/internal", tags=["token-bot-internal"])


def bot_token_license_dep() -> BotTokenLicenseService:
    return BotTokenLicenseService(get_process_store())


class ProductPartnerUpsertRequest(BaseModel):
    partner_id: str = Field(min_length=1, max_length=120)
    partner_code: str = Field(min_length=1, max_length=80)
    display_name: str = Field(min_length=1, max_length=160)
    telegram_id: Optional[str] = Field(default=None, max_length=80)
    allowed_bot_codes: list[str] = Field(default_factory=list)
    allowed_duration_days: list[int] = Field(default_factory=lambda: [1, 3, 7, 30])
    max_active_tokens: Optional[int] = Field(default=None, ge=0)
    max_tokens_per_day: Optional[int] = Field(default=None, ge=0)
    created_by_admin_telegram_id: Optional[str] = Field(default=None, max_length=80)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProductTokenIssueRequest(BaseModel):
    partner_id: str = Field(min_length=1, max_length=120)
    bot_code: str = Field(min_length=1, max_length=120)
    duration_days: int = Field(ge=1)
    issued_by_telegram_id: Optional[str] = Field(default=None, max_length=80)
    issued_to_note: Optional[str] = Field(default=None, max_length=240)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProductTokenRevokeRequest(BaseModel):
    partner_id: Optional[str] = Field(default=None, max_length=120)
    revoked_by_telegram_id: Optional[str] = Field(default=None, max_length=80)
    reason: str = Field(default="partner_revoke", max_length=200)


@router.post("/partners/upsert")
async def token_bot_internal_upsert_partner(
    payload: ProductPartnerUpsertRequest,
    _: None = Depends(require_internal_key),
    licenses: BotTokenLicenseService = Depends(bot_token_license_dep),
) -> dict[str, Any]:
    try:
        partner = licenses.upsert_partner(
            partner_id=payload.partner_id,
            partner_code=payload.partner_code,
            display_name=payload.display_name,
            telegram_id=payload.telegram_id,
            allowed_bot_codes=payload.allowed_bot_codes,
            allowed_duration_days=payload.allowed_duration_days,
            max_active_tokens=payload.max_active_tokens,
            max_tokens_per_day=payload.max_tokens_per_day,
            metadata={"source": "token_bot", **(payload.metadata or {})},
            created_by_admin_telegram_id=payload.created_by_admin_telegram_id,
        )
    except BotTokenLicenseError as exc:
        raise translate_control_plane_error(ValueError(str(exc))) from exc
    return {
        "partner_id": partner.get("partner_id"),
        "partner_code": partner.get("partner_code"),
        "display_name": partner.get("display_name"),
        "status": partner.get("status"),
        "allowed_bot_codes": partner.get("allowed_bot_codes"),
        "allowed_duration_days": partner.get("allowed_duration_days"),
    }


@router.post("/tokens/issue")
async def token_bot_internal_issue_token(
    payload: ProductTokenIssueRequest,
    _: None = Depends(require_internal_key),
    licenses: BotTokenLicenseService = Depends(bot_token_license_dep),
) -> dict[str, Any]:
    try:
        issued = licenses.issue_token(
            partner_id=payload.partner_id,
            bot_code=payload.bot_code,
            duration_days=payload.duration_days,
            issued_by_telegram_id=payload.issued_by_telegram_id,
            issued_to_note=payload.issued_to_note,
            metadata={"source": "token_bot", **(payload.metadata or {})},
        )
    except BotTokenLicenseError as exc:
        raise translate_control_plane_error(ValueError(str(exc))) from exc
    return issued


@router.post("/tokens/{token_id}/revoke")
async def token_bot_internal_revoke_token(
    token_id: str,
    payload: ProductTokenRevokeRequest,
    _: None = Depends(require_internal_key),
    licenses: BotTokenLicenseService = Depends(bot_token_license_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict[str, Any]:
    try:
        revoked = licenses.revoke_token(
            token_id=token_id,
            revoked_by_telegram_id=payload.revoked_by_telegram_id,
            reason=payload.reason,
            expected_partner_id=payload.partner_id,
        )
    except BotTokenLicenseError as exc:
        raise translate_control_plane_error(ValueError(str(exc))) from exc

    stops: list[dict[str, Any]] = []
    for entitlement in revoked.get("revoked_entitlements") or []:
        deployment_id = entitlement.get("deployment_id")
        telegram_id = str(entitlement.get("telegram_id") or "").strip()
        entitlement_id = str(entitlement.get("entitlement_id") or "").strip()
        if not deployment_id or not telegram_id:
            continue
        try:
            result = await service.stop_deployment(
                telegram_id=telegram_id,
                username=None,
                deployment_id=int(deployment_id),
                reason=payload.reason or "bot_token_revoked",
            )
            command = result.get("command") if isinstance(result, dict) else {}
            licenses.record_entitlement_stop_requested(
                entitlement_id=entitlement_id,
                stop_command_id=command.get("command_id") if isinstance(command, dict) else None,
                reason=payload.reason or "bot_token_revoked",
            )
            stops.append({"entitlement_id": entitlement_id, "deployment_id": deployment_id, "ok": True})
        except Exception as exc:
            stops.append(
                {
                    "entitlement_id": entitlement_id,
                    "deployment_id": deployment_id,
                    "ok": False,
                    "error": str(exc)[:240],
                }
            )

    return {**revoked, "stops": stops}


@router.get("/partners/{partner_id}/tokens")
async def token_bot_internal_list_partner_tokens(
    partner_id: str,
    scope: str = Query(default="all"),
    query: Optional[str] = Query(default=None, max_length=120),
    limit: int = Query(default=500, ge=1, le=500),
    _: None = Depends(require_internal_key),
    licenses: BotTokenLicenseService = Depends(bot_token_license_dep),
) -> dict[str, Any]:
    try:
        return licenses.list_partner_tokens(
            partner_id=partner_id,
            scope=scope,
            query=query,
            limit=limit,
        )
    except BotTokenLicenseError as exc:
        raise translate_control_plane_error(ValueError(str(exc))) from exc
