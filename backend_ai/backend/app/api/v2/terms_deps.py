from __future__ import annotations

from typing import Any

from fastapi import Depends, Request

from app.api.v2.control_plane_deps import user_dep
from app.api.v2.error_catalog import to_http_exception
from app.services.miniapp_terms import MiniappTermsConsentService, MiniappTermsError
from app.services.store_service import get_store
from app.settings import settings


def miniapp_terms_enforcement_enabled() -> bool:
    return bool(getattr(settings, "MINIAPP_TERMS_ENFORCEMENT_ENABLED", False))


def miniapp_terms_dep(request: Request) -> MiniappTermsConsentService:
    return MiniappTermsConsentService(get_store(request))


def request_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip() or None
    if request.client:
        return request.client.host
    return None


def request_user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


def require_miniapp_terms_accepted(
    user: dict[str, Any] = Depends(user_dep),
    terms: MiniappTermsConsentService = Depends(miniapp_terms_dep),
) -> None:
    if not miniapp_terms_enforcement_enabled():
        return
    try:
        terms.assert_accepted(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
        )
    except MiniappTermsError as exc:
        raise to_http_exception(str(exc)) from exc
