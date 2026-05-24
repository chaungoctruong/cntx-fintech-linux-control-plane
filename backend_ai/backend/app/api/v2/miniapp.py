from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.v2.error_catalog import to_http_exception
from app.api.v2.terms_deps import (
    miniapp_terms_dep,
    miniapp_terms_enforcement_enabled,
    require_miniapp_terms_accepted,
    request_ip,
    request_user_agent,
)
from app.api.v2.control_plane_deps import service_dep, user_dep
from app.schemas.ctrader import (
    CTraderAuthorizeUrlRequest,
    CTraderDiscoverAccountsRequest,
    CTraderEvaluateDeploymentRequest,
    CTraderExchangeRequest,
    CTraderStartDeploymentRequest,
    CTraderStopDeploymentRequest,
    CTraderSelectDefaultAccountRequest,
)
from app.services.bot_token_license import BotTokenLicenseError, BotTokenLicenseService
from app.services.control_plane_service import MT5ControlPlaneService
from app.services.miniapp_access import build_full_access_entitlement, has_miniapp_full_access
from app.services.miniapp_terms import MiniappTermsConsentService, MiniappTermsError, TERMS_VERSION
from app.services.broker import CTraderBrokerApiClient
from app.services.broker.ctrader_public_beta import build_ctrader_public_beta_overview
from app.services.store_service import get_store

router = APIRouter(prefix="/miniapp", tags=["mt5-miniapp"])
mini_router = APIRouter(prefix="/mini", tags=["mt5-mini"])


class Mt5BotTokenClaimRequest(BaseModel):
    account_id: int = Field(gt=0)
    bot_name: str = Field(min_length=1)
    token: str = Field(min_length=1)


class MiniappTermsAcceptRequest(BaseModel):
    version: str = Field(min_length=1)
    checkbox_1: bool
    checkbox_2: bool
    checkbox_3: bool
    partner_id: Optional[str] = None
    token_id: Optional[str] = None


def bot_token_license_dep(request: Request) -> BotTokenLicenseService:
    return BotTokenLicenseService(get_store(request))


def _translate_bot_token_error(exc: Exception) -> HTTPException:
    detail = str(exc) or exc.__class__.__name__
    status_code = status.HTTP_403_FORBIDDEN
    if detail in {
        "bot_token_not_found",
        "bot_token_already_used",
        "bot_token_expired",
        "bot_token_revoked",
        "bot_token_bot_package_not_found",
        "bot_token_duration_invalid",
    }:
        status_code = status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=status_code, detail=detail)


@router.get("/dashboard")
async def miniapp_dashboard(
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    return service.miniapp_dashboard(
        telegram_id=str(user["telegram_id"]),
        username=user.get("username"),
    )


@router.get("/access")
async def miniapp_access(user: dict = Depends(user_dep)) -> dict:
    full_access = has_miniapp_full_access(user["telegram_id"])
    return {
        "mt5_full_access": full_access,
        "bot_token_required": not full_access,
        "terms_enforcement_enabled": miniapp_terms_enforcement_enabled(),
    }


@router.get("/terms/status")
async def miniapp_terms_status(
    user: dict = Depends(user_dep),
    terms: MiniappTermsConsentService = Depends(miniapp_terms_dep),
) -> dict:
    if not miniapp_terms_enforcement_enabled():
        return {
            "accepted": True,
            "version": TERMS_VERSION,
            "accepted_at": None,
            "requires_acceptance": False,
            "enabled": False,
        }
    status_payload = terms.status(
        telegram_id=str(user["telegram_id"]),
        username=user.get("username"),
    )
    status_payload["enabled"] = True
    return status_payload


@router.post("/terms/accept")
async def miniapp_terms_accept(
    payload: MiniappTermsAcceptRequest,
    request: Request,
    user: dict = Depends(user_dep),
    terms: MiniappTermsConsentService = Depends(miniapp_terms_dep),
) -> dict:
    try:
        accept_payload = terms.accept(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
            version=payload.version,
            checkbox_1=payload.checkbox_1,
            checkbox_2=payload.checkbox_2,
            checkbox_3=payload.checkbox_3,
            partner_id=payload.partner_id,
            token_id=payload.token_id,
            ip_address=request_ip(request),
            user_agent=request_user_agent(request),
        )
        accept_payload["enabled"] = miniapp_terms_enforcement_enabled()
        return accept_payload
    except MiniappTermsError as exc:
        raise to_http_exception(str(exc)) from exc


@router.get("/accounts")
async def miniapp_accounts(
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    return {
        "items": service.list_accounts(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
        )
    }


@router.get("/deployments")
async def miniapp_deployments(
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    return {
        "items": service.list_deployments(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
        )
    }


@router.get("/bot-token/entitlements")
async def miniapp_bot_token_entitlements(
    account_id: int,
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
    bot_token_license: BotTokenLicenseService = Depends(bot_token_license_dep),
) -> dict:
    user_row = service.ensure_user(telegram_id=str(user["telegram_id"]), username=user.get("username"))
    account = service.get_account(
        telegram_id=str(user["telegram_id"]),
        username=user.get("username"),
        account_id=int(account_id),
    )
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account_not_found")
    if has_miniapp_full_access(user["telegram_id"]):
        return {
            "items": [
                build_full_access_entitlement(
                    telegram_id=str(user["telegram_id"]),
                    user_id=int(user_row["id"]),
                    account_id=int(account_id),
                )
            ]
        }
    return {
        "items": bot_token_license.list_active_entitlements(
            telegram_id=str(user["telegram_id"]),
            user_id=int(user_row["id"]),
            account_id=int(account_id),
        )
    }


@router.post("/bot-token/claim")
async def miniapp_bot_token_claim(
    payload: Mt5BotTokenClaimRequest,
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
    bot_token_license: BotTokenLicenseService = Depends(bot_token_license_dep),
    terms: MiniappTermsConsentService = Depends(miniapp_terms_dep),
    _terms_accepted: None = Depends(require_miniapp_terms_accepted),
) -> dict:
    del _terms_accepted
    user_row = service.ensure_user(telegram_id=str(user["telegram_id"]), username=user.get("username"))
    account = service.get_account(
        telegram_id=str(user["telegram_id"]),
        username=user.get("username"),
        account_id=payload.account_id,
    )
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account_not_found")
    bot = service.get_bot(bot_name=payload.bot_name, force_sync=False)
    if not bot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="bot_not_found")
    try:
        entitlement = bot_token_license.claim_token(
            telegram_id=str(user["telegram_id"]),
            user_id=int(user_row["id"]),
            account_id=payload.account_id,
            bot_name=payload.bot_name,
            bot_code=bot.get("bot_code"),
            raw_token=payload.token,
        )
    except BotTokenLicenseError as exc:
        raise _translate_bot_token_error(exc) from exc
    terms.attach_partner_context(
        telegram_id=str(user["telegram_id"]),
        username=user.get("username"),
        partner_id=entitlement.get("partner_id"),
        token_id=entitlement.get("token_id"),
    )
    return {"entitlement": entitlement}


@mini_router.get("/bots")
async def mini_bots(
    force_sync: bool = False,
    runtime_lane: str = "backend_webhook_signal",
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> list[dict]:
    del user
    return service.list_mini_bots(force_sync=force_sync, runtime_lane=runtime_lane)


def _ctrader_tenant_user_id(user: dict) -> str:
    return str(user["telegram_id"])


def _filter_visible_ctrader_bots(payload: dict) -> dict:
    raw_items = payload.get("items") if isinstance(payload, dict) else []
    visible_items = [
        item
        for item in raw_items
        if isinstance(item, dict) and not bool(item.get("is_template"))
    ]
    return {
        "items": visible_items,
        "execution_ready": bool(payload.get("execution_ready")) if isinstance(payload, dict) else False,
        "blockers": payload.get("blockers") if isinstance(payload, dict) else [],
    }


def _extract_default_ctrader_selection(connection: dict | None) -> Optional[dict]:
    if not isinstance(connection, dict):
        return None
    metadata = connection.get("metadata_json")
    if not isinstance(metadata, dict):
        return None
    selection = metadata.get("default_account_selection")
    if not isinstance(selection, dict):
        return None
    trading_account_id = str(selection.get("trading_account_id") or "").strip()
    if not trading_account_id:
        return None
    return selection


def _derive_ctrader_next_action(
    *,
    accounts: list[dict],
    default_selection: Optional[dict],
    discover_error: Optional[str],
) -> str:
    if default_selection is not None:
        environment = str(default_selection.get("environment") or "").strip().lower()
        if environment == "live" and not bool(default_selection.get("live_risk_confirmed")):
            return "confirm_live_risk"
        return "ready"
    if len(accounts) == 1:
        environment = str(accounts[0].get("environment") or "").strip().lower()
        if environment == "live":
            return "confirm_live_risk"
    if discover_error and not accounts:
        return "select_account"
    return "select_account"


def _translate_ctrader_bridge_error(exc: Exception) -> HTTPException:
    detail = str(exc) or exc.__class__.__name__
    if detail == "ctrader_backend_url_required":
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="ctrader_service_unavailable")
    if detail == "ctrader_backend_timeout":
        return HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="ctrader_service_timeout")
    if detail == "ctrader_backend_unreachable":
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="ctrader_service_unavailable")
    if detail.startswith("ctrader_backend_http_"):
        prefix, _, downstream_detail = detail.partition(":")
        try:
            code = int(prefix.rsplit("_", 1)[-1])
        except ValueError:
            code = status.HTTP_502_BAD_GATEWAY
        if code < 400 or code > 599:
            code = status.HTTP_502_BAD_GATEWAY
        public_detail = _normalize_ctrader_public_error_detail(downstream_detail or prefix, code)
        return HTTPException(status_code=code, detail=public_detail)
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def _normalize_ctrader_public_error_detail(detail: str, status_code: int) -> str:
    normalized = (detail or "").strip()
    if not normalized:
        return "temporary_unavailable"
    if normalized in {"ctrader_backend_timeout", "ctrader_service_timeout"}:
        return "ctrader_service_timeout"
    if normalized in {
        "ctrader_backend_url_required",
        "ctrader_backend_unreachable",
        "ctrader_service_unavailable",
    }:
        return "ctrader_service_unavailable"
    if normalized.startswith("ctrader_backend_"):
        return "ctrader_service_unavailable"
    if status_code == status.HTTP_504_GATEWAY_TIMEOUT:
        return "ctrader_service_timeout"
    if status_code >= 500:
        return "ctrader_service_unavailable"
    return normalized


@router.post("/ctrader/authorize-url")
async def miniapp_ctrader_authorize_url(
    payload: CTraderAuthorizeUrlRequest,
    user: dict = Depends(user_dep),
) -> dict:
    try:
        client = CTraderBrokerApiClient()
        return await client.build_authorize_url(
            tenant_user_id=_ctrader_tenant_user_id(user),
            redirect_uri=payload.redirect_uri,
            scope=payload.scope,
            state=payload.state,
        )
    except Exception as exc:
        raise _translate_ctrader_bridge_error(exc) from exc


@router.post("/ctrader/exchange")
async def miniapp_ctrader_exchange(
    payload: CTraderExchangeRequest,
    user: dict = Depends(user_dep),
) -> dict:
    try:
        client = CTraderBrokerApiClient()
        return await client.complete_callback(
            tenant_user_id=_ctrader_tenant_user_id(user),
            code=payload.code,
            scope=payload.scope,
            state=payload.state,
        )
    except Exception as exc:
        raise _translate_ctrader_bridge_error(exc) from exc


@router.post("/ctrader/callback/complete")
async def miniapp_ctrader_callback_complete(
    payload: CTraderExchangeRequest,
    user: dict = Depends(user_dep),
) -> dict:
    try:
        client = CTraderBrokerApiClient()
        callback = await client.complete_callback(
            tenant_user_id=_ctrader_tenant_user_id(user),
            code=payload.code,
            scope=payload.scope,
            state=payload.state,
        )
    except Exception as exc:
        raise _translate_ctrader_bridge_error(exc) from exc

    accounts: list[dict] = []
    discover_error: Optional[str] = None
    connection = callback.get("connection") if isinstance(callback, dict) else {}
    connection_id = str(connection.get("id") or "").strip() if isinstance(connection, dict) else ""

    if connection_id:
        try:
            discovered = await client.discover_accounts(
                tenant_user_id=_ctrader_tenant_user_id(user),
                broker_connection_id=connection_id,
            )
            raw_items = discovered.get("items") if isinstance(discovered, dict) else []
            if isinstance(raw_items, list):
                accounts = [item for item in raw_items if isinstance(item, dict)]
        except Exception as exc:
            raw_detail = str(exc) or exc.__class__.__name__
            if raw_detail.startswith("ctrader_backend_http_"):
                prefix, _, downstream_detail = raw_detail.partition(":")
                try:
                    code = int(prefix.rsplit("_", 1)[-1])
                except ValueError:
                    code = 502
                discover_error = _normalize_ctrader_public_error_detail(downstream_detail or prefix, code)
            else:
                discover_error = _normalize_ctrader_public_error_detail(raw_detail, 502)

    default_selection = _extract_default_ctrader_selection(connection if isinstance(connection, dict) else None)
    next_action = _derive_ctrader_next_action(
        accounts=accounts,
        default_selection=default_selection,
        discover_error=discover_error,
    )
    return {
        **callback,
        "accounts": accounts,
        "discover_error": discover_error,
        "default_account_selection": default_selection,
        "next_action": next_action,
    }


@router.get("/ctrader/connections")
async def miniapp_ctrader_connections(
    user: dict = Depends(user_dep),
) -> dict:
    try:
        client = CTraderBrokerApiClient()
        return await client.list_connections(tenant_user_id=_ctrader_tenant_user_id(user))
    except Exception as exc:
        raise _translate_ctrader_bridge_error(exc) from exc


@router.post("/ctrader/connections/{connection_id}/refresh")
async def miniapp_ctrader_refresh_connection(
    connection_id: str,
    user: dict = Depends(user_dep),
) -> dict:
    try:
        client = CTraderBrokerApiClient()
        return await client.refresh_connection(
            tenant_user_id=_ctrader_tenant_user_id(user),
            connection_id=connection_id,
        )
    except Exception as exc:
        raise _translate_ctrader_bridge_error(exc) from exc


@router.get("/ctrader/accounts")
async def miniapp_ctrader_accounts(
    user: dict = Depends(user_dep),
) -> dict:
    try:
        client = CTraderBrokerApiClient()
        return await client.list_accounts(tenant_user_id=_ctrader_tenant_user_id(user))
    except Exception as exc:
        raise _translate_ctrader_bridge_error(exc) from exc


@router.post("/ctrader/accounts/discover")
async def miniapp_ctrader_discover_accounts(
    payload: CTraderDiscoverAccountsRequest,
    user: dict = Depends(user_dep),
) -> dict:
    try:
        client = CTraderBrokerApiClient()
        return await client.discover_accounts(
            tenant_user_id=_ctrader_tenant_user_id(user),
            broker_connection_id=payload.broker_connection_id,
        )
    except Exception as exc:
        raise _translate_ctrader_bridge_error(exc) from exc


@router.post("/ctrader/accounts/select-default")
async def miniapp_ctrader_select_default_account(
    payload: CTraderSelectDefaultAccountRequest,
    user: dict = Depends(user_dep),
) -> dict:
    try:
        client = CTraderBrokerApiClient()
        return await client.select_default_account(
            tenant_user_id=_ctrader_tenant_user_id(user),
            broker_connection_id=payload.broker_connection_id,
            trading_account_id=payload.trading_account_id,
            live_risk_confirmed=payload.live_risk_confirmed,
        )
    except Exception as exc:
        raise _translate_ctrader_bridge_error(exc) from exc


@router.get("/ctrader/bots")
async def miniapp_ctrader_bots(
    user: dict = Depends(user_dep),
) -> dict:
    del user
    try:
        client = CTraderBrokerApiClient()
        payload = await client.list_bots()
        return _filter_visible_ctrader_bots(payload)
    except Exception as exc:
        raise _translate_ctrader_bridge_error(exc) from exc


@router.get("/ctrader/overview")
async def miniapp_ctrader_overview(
    user: dict = Depends(user_dep),
) -> dict:
    del user
    try:
        client = CTraderBrokerApiClient()
        return await build_ctrader_public_beta_overview(client=client)
    except Exception as exc:
        raise _translate_ctrader_bridge_error(exc) from exc


@router.get("/ctrader/runtime-state")
async def miniapp_ctrader_runtime_state(
    trading_account_id: Optional[str] = None,
    events_limit: int = 8,
    user: dict = Depends(user_dep),
) -> dict:
    try:
        client = CTraderBrokerApiClient()
        overview_payload, bots_payload, deployments_payload = await asyncio.gather(
            build_ctrader_public_beta_overview(client=client),
            client.list_bots(),
            client.list_deployments(
                tenant_user_id=_ctrader_tenant_user_id(user),
                trading_account_id=trading_account_id,
            ),
        )
        bot_catalog = _filter_visible_ctrader_bots(bots_payload)
        deployments = deployments_payload.get("items") if isinstance(deployments_payload, dict) else []
        if not isinstance(deployments, list):
            deployments = []

        active_deployment = next(
            (
                item
                for item in deployments
                if isinstance(item, dict) and str(item.get("desired_state") or "").strip().lower() == "started"
            ),
            None,
        )

        deployment_detail = active_deployment
        deployment_events: list[dict] = []
        if isinstance(active_deployment, dict):
            deployment_id = str(active_deployment.get("id") or "").strip()
            if deployment_id:
                detail_result, events_result = await asyncio.gather(
                    client.get_deployment(
                        tenant_user_id=_ctrader_tenant_user_id(user),
                        deployment_id=deployment_id,
                    ),
                    client.list_deployment_events(
                        tenant_user_id=_ctrader_tenant_user_id(user),
                        deployment_id=deployment_id,
                        limit=events_limit,
                    ),
                    return_exceptions=True,
                )
                if not isinstance(detail_result, Exception) and isinstance(detail_result, dict):
                    deployment_detail = detail_result
                if not isinstance(events_result, Exception) and isinstance(events_result, dict):
                    raw_items = events_result.get("items")
                    if isinstance(raw_items, list):
                        deployment_events = [item for item in raw_items if isinstance(item, dict)]

        return {
            "overview": overview_payload,
            "bot_catalog": bot_catalog,
            "deployments": {"items": deployments},
            "active_deployment": active_deployment,
            "deployment_detail": deployment_detail,
            "deployment_events": {"items": deployment_events},
        }
    except Exception as exc:
        raise _translate_ctrader_bridge_error(exc) from exc


@router.get("/ctrader/deployments")
async def miniapp_ctrader_deployments(
    trading_account_id: Optional[str] = None,
    user: dict = Depends(user_dep),
) -> dict:
    try:
        client = CTraderBrokerApiClient()
        return await client.list_deployments(
            tenant_user_id=_ctrader_tenant_user_id(user),
            trading_account_id=trading_account_id,
        )
    except Exception as exc:
        raise _translate_ctrader_bridge_error(exc) from exc


@router.get("/ctrader/deployments/{deployment_id}")
async def miniapp_ctrader_deployment_detail(
    deployment_id: str,
    user: dict = Depends(user_dep),
) -> dict:
    try:
        client = CTraderBrokerApiClient()
        return await client.get_deployment(
            tenant_user_id=_ctrader_tenant_user_id(user),
            deployment_id=deployment_id,
        )
    except Exception as exc:
        raise _translate_ctrader_bridge_error(exc) from exc


@router.get("/ctrader/deployments/{deployment_id}/events")
async def miniapp_ctrader_deployment_events(
    deployment_id: str,
    limit: int = 20,
    user: dict = Depends(user_dep),
) -> dict:
    try:
        client = CTraderBrokerApiClient()
        return await client.list_deployment_events(
            tenant_user_id=_ctrader_tenant_user_id(user),
            deployment_id=deployment_id,
            limit=limit,
        )
    except Exception as exc:
        raise _translate_ctrader_bridge_error(exc) from exc


@router.post("/ctrader/deployments/start")
async def miniapp_ctrader_start_deployment(
    payload: CTraderStartDeploymentRequest,
    user: dict = Depends(user_dep),
) -> dict:
    try:
        client = CTraderBrokerApiClient()
        return await client.start_deployment(
            tenant_user_id=_ctrader_tenant_user_id(user),
            broker_connection_id=payload.broker_connection_id,
            trading_account_id=payload.trading_account_id,
            bot_code=payload.bot_code,
            config=payload.config,
            live_risk_confirmed=payload.live_risk_confirmed,
            force_reconnect=payload.force_reconnect,
            reason=payload.reason,
        )
    except Exception as exc:
        raise _translate_ctrader_bridge_error(exc) from exc


@router.post("/ctrader/deployments/{deployment_id}/evaluate")
async def miniapp_ctrader_evaluate_deployment(
    deployment_id: str,
    payload: CTraderEvaluateDeploymentRequest,
    user: dict = Depends(user_dep),
) -> dict:
    try:
        client = CTraderBrokerApiClient()
        return await client.evaluate_deployment(
            tenant_user_id=_ctrader_tenant_user_id(user),
            deployment_id=deployment_id,
            market=payload.market,
        )
    except Exception as exc:
        raise _translate_ctrader_bridge_error(exc) from exc


@router.post("/ctrader/deployments/{deployment_id}/stop")
async def miniapp_ctrader_stop_deployment(
    deployment_id: str,
    payload: CTraderStopDeploymentRequest,
    user: dict = Depends(user_dep),
) -> dict:
    try:
        client = CTraderBrokerApiClient()
        return await client.stop_deployment(
            tenant_user_id=_ctrader_tenant_user_id(user),
            deployment_id=deployment_id,
            reason=payload.reason,
        )
    except Exception as exc:
        raise _translate_ctrader_bridge_error(exc) from exc
