import json
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .models import Partner, Token


router = APIRouter(prefix="/api/v1")


class PartnerCreate(BaseModel):
    id: str | None = None
    name: str
    contact: str | None = None


class PartnerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    contact: str | None
    active: bool
    created_at: datetime


class TokenIssueRequest(BaseModel):
    partner_id: str
    bot_ids: list[str] = Field(min_length=1)
    ttl_seconds: int | None = None


class TokenIssueResponse(BaseModel):
    token: str
    jti: str
    partner_id: str
    bot_ids: list[str]
    expires_at: datetime


class TokenOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    jti: str
    partner_id: str
    bot_ids: list[str]
    issued_at: datetime
    expires_at: datetime
    revoked: bool


def _admin_guard(request: Request, x_admin_key: str | None = Header(default=None)):
    if not x_admin_key or x_admin_key != request.app.state.settings.admin_api_key:
        raise HTTPException(status_code=401, detail="invalid admin key")


def _token_guard(
    request: Request, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    raw = authorization.split(" ", 1)[1].strip()
    try:
        claims = request.app.state.token_service.verify(raw)
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"invalid token: {e}")
    sf = request.app.state.session_factory
    with sf() as s:
        row = s.get(Token, claims["jti"])
        if row is None:
            raise HTTPException(status_code=401, detail="token not registered")
        if row.revoked:
            raise HTTPException(status_code=401, detail="token revoked")
    return claims


def _token_to_out(row: Token) -> TokenOut:
    return TokenOut(
        jti=row.jti,
        partner_id=row.partner_id,
        bot_ids=json.loads(row.bot_ids_json),
        issued_at=row.issued_at,
        expires_at=row.expires_at,
        revoked=row.revoked,
    )


@router.post("/admin/partners", response_model=PartnerOut, dependencies=[Depends(_admin_guard)])
def create_partner(payload: PartnerCreate, request: Request):
    pid = payload.id or uuid.uuid4().hex[:12]
    sf = request.app.state.session_factory
    with sf() as s:
        if s.get(Partner, pid) is not None:
            raise HTTPException(status_code=409, detail="partner_id already exists")
        p = Partner(id=pid, name=payload.name, contact=payload.contact, active=True)
        s.add(p)
        s.commit()
        s.refresh(p)
        return p


@router.get("/admin/partners", response_model=list[PartnerOut], dependencies=[Depends(_admin_guard)])
def list_partners(request: Request):
    sf = request.app.state.session_factory
    with sf() as s:
        return list(s.query(Partner).order_by(Partner.created_at.desc()).all())


@router.post("/admin/partners/{partner_id}/deactivate", dependencies=[Depends(_admin_guard)])
def deactivate_partner(partner_id: str, request: Request):
    sf = request.app.state.session_factory
    with sf() as s:
        p = s.get(Partner, partner_id)
        if not p:
            raise HTTPException(status_code=404, detail="partner not found")
        p.active = False
        s.commit()
    return {"ok": True}


@router.post("/admin/bots/encrypt-all", dependencies=[Depends(_admin_guard)])
def encrypt_all_bots(request: Request):
    reg = request.app.state.registry
    results = reg.encrypt_all()
    return {"encrypted_count": len(results), "packages": results}


@router.get("/admin/bots", dependencies=[Depends(_admin_guard)])
def list_bots_admin(request: Request):
    reg = request.app.state.registry
    return {"encrypted": reg.list_encrypted()}


@router.get("/admin/bots/{bot_id}/package", dependencies=[Depends(_admin_guard)])
def admin_get_package(bot_id: str, request: Request):
    if not request.app.state.settings.enable_debug_decrypt:
        raise HTTPException(status_code=403, detail="debug decrypt disabled")
    reg = request.app.state.registry
    try:
        return Response(
            content=reg.decrypt_package(bot_id),
            media_type="application/gzip",
            headers={"Content-Disposition": f'attachment; filename="{bot_id}.tar.gz"'},
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="bot not found")


@router.post("/admin/tokens", response_model=TokenIssueResponse, dependencies=[Depends(_admin_guard)])
def issue_token(payload: TokenIssueRequest, request: Request):
    settings = request.app.state.settings
    if not getattr(settings, "enable_legacy_jwt_tokens", False):
        raise HTTPException(
            status_code=410,
            detail=(
                "legacy JWT token issuing is disabled. "
                "Use the Telegram partner flow to create Mini App activation codes."
            ),
        )
    ts = request.app.state.token_service
    sf = request.app.state.session_factory
    reg = request.app.state.registry

    unknown = [b for b in payload.bot_ids if not reg.has(b)]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"bot_ids chưa được mã hóa: {unknown}",
        )

    ttl = payload.ttl_seconds or settings.token_default_ttl_sec
    with sf() as s:
        partner = s.get(Partner, payload.partner_id)
        if partner is None or not partner.active:
            raise HTTPException(status_code=404, detail="partner not found or inactive")
        token, jti, exp = ts.issue(payload.partner_id, payload.bot_ids, ttl)
        row = Token(
            jti=jti,
            partner_id=payload.partner_id,
            bot_ids_json=json.dumps(payload.bot_ids),
            issued_at=datetime.utcnow(),
            expires_at=exp.replace(tzinfo=None),
            revoked=False,
        )
        s.add(row)
        s.commit()

    return TokenIssueResponse(
        token=token,
        jti=jti,
        partner_id=payload.partner_id,
        bot_ids=payload.bot_ids,
        expires_at=exp,
    )


@router.get("/admin/tokens", response_model=list[TokenOut], dependencies=[Depends(_admin_guard)])
def list_tokens(request: Request, partner_id: str | None = None):
    sf = request.app.state.session_factory
    with sf() as s:
        q = s.query(Token)
        if partner_id:
            q = q.filter(Token.partner_id == partner_id)
        rows = q.order_by(Token.issued_at.desc()).all()
        return [_token_to_out(r) for r in rows]


@router.post("/admin/tokens/{jti}/revoke", dependencies=[Depends(_admin_guard)])
def revoke_token(jti: str, request: Request):
    sf = request.app.state.session_factory
    with sf() as s:
        row = s.get(Token, jti)
        if row is None:
            raise HTTPException(status_code=404, detail="token not found")
        if row.revoked:
            return {"ok": True, "already_revoked": True}
        row.revoked = True
        row.revoked_at = datetime.utcnow()
        s.commit()
    return {"ok": True}


@router.get("/bots")
def partner_list_bots(request: Request, claims: dict = Depends(_token_guard)):
    from .manifest import public_summary

    reg = request.app.state.registry
    items = []
    for bid in claims["scope"]["bot_ids"]:
        if not reg.has(bid):
            continue
        try:
            items.append(public_summary(reg.get_manifest(bid)))
        except FileNotFoundError:
            continue
    return {
        "partner_id": claims["sub"],
        "expires_at": claims["exp"],
        "bots": items,
    }


@router.get("/bots/{bot_id}")
def partner_get_bot(bot_id: str, request: Request, claims: dict = Depends(_token_guard)):
    from .manifest import public_summary

    if bot_id not in set(claims["scope"]["bot_ids"]):
        raise HTTPException(status_code=403, detail="bot not in your scope")
    reg = request.app.state.registry
    try:
        return public_summary(reg.get_manifest(bot_id))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="bot not found")


class StartDeploymentRequest(BaseModel):
    account_id: int | None = None
    config: dict[str, Any] | None = None


@router.post("/bots/{bot_id}/deployments")
def partner_start_deployment(
    bot_id: str,
    payload: StartDeploymentRequest,
    request: Request,
    claims: dict = Depends(_token_guard),
):
    if bot_id not in set(claims["scope"]["bot_ids"]):
        raise HTTPException(status_code=403, detail="bot not in your scope")
    reg = request.app.state.registry
    if not reg.has(bot_id):
        raise HTTPException(status_code=404, detail="bot not found")
    return {
        "status": "stub",
        "note": "deployment chưa nối backend chính — verify token + scope + giải mã package thành công",
        "bot_id": bot_id,
        "manifest_version": reg.get_manifest(bot_id).get("version"),
        "partner_id": claims["sub"],
        "requested": {"account_id": payload.account_id, "config": payload.config},
    }
