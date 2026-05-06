from __future__ import annotations

from typing import Any, Optional

import httpx

from app.settings import settings

_SHARED_HTTPX_CLIENT: Optional[httpx.AsyncClient] = None


def _shared_httpx_limits() -> httpx.Limits:
    return httpx.Limits(max_connections=100, max_keepalive_connections=20)


def _get_shared_httpx_client(timeout_sec: float) -> httpx.AsyncClient:
    global _SHARED_HTTPX_CLIENT
    if _SHARED_HTTPX_CLIENT is None or _SHARED_HTTPX_CLIENT.is_closed:
        _SHARED_HTTPX_CLIENT = httpx.AsyncClient(
            timeout=timeout_sec,
            limits=_shared_httpx_limits(),
        )
    return _SHARED_HTTPX_CLIENT


async def close_shared_ctrader_broker_http_client() -> None:
    global _SHARED_HTTPX_CLIENT
    if _SHARED_HTTPX_CLIENT is None or _SHARED_HTTPX_CLIENT.is_closed:
        return
    await _SHARED_HTTPX_CLIENT.aclose()
    _SHARED_HTTPX_CLIENT = None


class CTraderBrokerApiClient:
    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
        timeout_sec: Optional[float] = None,
    ) -> None:
        resolved_base_url = (
            str(base_url or "").strip()
            or str(getattr(settings, "BROKER_API_CTRADER_BASE_URL", "") or "").strip()
        ).rstrip("/")
        if not resolved_base_url:
            raise ValueError("ctrader_backend_url_required")
        self._base_url = resolved_base_url
        self._api_key = (
            str(api_key or "").strip()
            or str(getattr(settings, "BROKER_API_CTRADER_SHARED_KEY", "") or "").strip()
            or str(getattr(settings, "BACKEND_API_KEY", "") or "").strip()
        )
        self._timeout_sec = max(
            3.0,
            float(timeout_sec or getattr(settings, "BROKER_API_CTRADER_TIMEOUT_SEC", 15.0) or 15.0),
        )
        self._client = client or _get_shared_httpx_client(self._timeout_sec)

    async def build_authorize_url(
        self,
        *,
        tenant_user_id: str,
        redirect_uri: str,
        scope: Optional[str] = None,
        state: Optional[str] = None,
    ) -> dict[str, Any]:
        payload = {
            "tenant_user_id": tenant_user_id,
            "redirect_uri": redirect_uri,
            "scope": scope,
            "state": state,
        }
        return await self._request("POST", "/api/v1/oauth/authorize-url", json_payload=_strip_nones(payload))

    async def exchange_code(
        self,
        *,
        tenant_user_id: str,
        code: str,
        redirect_uri: str,
        scope: Optional[str] = None,
        state: Optional[str] = None,
    ) -> dict[str, Any]:
        payload = {
            "tenant_user_id": tenant_user_id,
            "code": code,
            "redirect_uri": redirect_uri,
            "scope": scope,
            "state": state,
        }
        return await self._request("POST", "/api/v1/oauth/exchange", json_payload=_strip_nones(payload))

    async def complete_callback(
        self,
        *,
        tenant_user_id: Optional[str],
        code: str,
        state: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> dict[str, Any]:
        params = {
            "tenant_user_id": tenant_user_id,
            "code": code,
            "state": state,
            "scope": scope,
        }
        return await self._request("GET", "/api/v1/oauth/callback", params=_strip_nones(params))

    async def list_connections(self, *, tenant_user_id: str) -> dict[str, Any]:
        return await self._request("GET", "/api/v1/oauth/connections", params={"tenant_user_id": tenant_user_id})

    async def refresh_connection(
        self,
        *,
        tenant_user_id: str,
        connection_id: str,
    ) -> dict[str, Any]:
        payload = {
            "tenant_user_id": tenant_user_id,
        }
        return await self._request(
            "POST",
            f"/api/v1/oauth/connections/{connection_id}/refresh",
            json_payload=payload,
        )

    async def list_accounts(self, *, tenant_user_id: str) -> dict[str, Any]:
        return await self._request("GET", "/api/v1/accounts", params={"tenant_user_id": tenant_user_id})

    async def list_bots(self) -> dict[str, Any]:
        return await self._request("GET", "/api/v1/bots")

    async def get_runtime_session_pool_status(self, *, refresh: bool = False) -> dict[str, Any]:
        params = {"refresh": "true"} if refresh else None
        return await self._request("GET", "/api/v1/runtime/session-pool", params=params)

    async def get_runtime_deployment_reconciler_status(self, *, refresh: bool = False) -> dict[str, Any]:
        params = {"refresh": "true"} if refresh else None
        return await self._request("GET", "/api/v1/runtime/deployment-reconciler", params=params)

    async def discover_accounts(
        self,
        *,
        tenant_user_id: str,
        broker_connection_id: str,
    ) -> dict[str, Any]:
        payload = {
            "tenant_user_id": tenant_user_id,
            "broker_connection_id": broker_connection_id,
        }
        return await self._request("POST", "/api/v1/accounts/discover", json_payload=payload)

    async def select_default_account(
        self,
        *,
        tenant_user_id: str,
        broker_connection_id: str,
        trading_account_id: str,
        live_risk_confirmed: bool = False,
    ) -> dict[str, Any]:
        payload = {
            "tenant_user_id": tenant_user_id,
            "broker_connection_id": broker_connection_id,
            "trading_account_id": trading_account_id,
            "live_risk_confirmed": live_risk_confirmed,
        }
        return await self._request("POST", "/api/v1/accounts/select-default", json_payload=payload)

    async def list_deployments(
        self,
        *,
        tenant_user_id: str,
        trading_account_id: Optional[str] = None,
    ) -> dict[str, Any]:
        params = {
            "tenant_user_id": tenant_user_id,
            "trading_account_id": trading_account_id,
        }
        return await self._request("GET", "/api/v1/deployments", params=_strip_nones(params))

    async def get_deployment(
        self,
        *,
        tenant_user_id: str,
        deployment_id: str,
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/api/v1/deployments/{deployment_id}",
            params={"tenant_user_id": tenant_user_id},
        )

    async def list_deployment_events(
        self,
        *,
        tenant_user_id: str,
        deployment_id: str,
        limit: int = 20,
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/api/v1/deployments/{deployment_id}/events",
            params={
                "tenant_user_id": tenant_user_id,
                "limit": max(1, min(int(limit), 100)),
            },
        )

    async def start_deployment(
        self,
        *,
        tenant_user_id: str,
        broker_connection_id: str,
        trading_account_id: str,
        bot_code: str,
        config: Optional[dict[str, Any]] = None,
        live_risk_confirmed: bool = False,
        force_reconnect: bool = False,
        reason: Optional[str] = None,
    ) -> dict[str, Any]:
        payload = {
            "tenant_user_id": tenant_user_id,
            "broker_connection_id": broker_connection_id,
            "trading_account_id": trading_account_id,
            "bot_code": bot_code,
            "config": config or {},
            "live_risk_confirmed": live_risk_confirmed,
            "force_reconnect": force_reconnect,
            "reason": reason,
        }
        return await self._request("POST", "/api/v1/deployments/start", json_payload=_strip_nones(payload))

    async def stop_deployment(
        self,
        *,
        tenant_user_id: str,
        deployment_id: str,
        reason: Optional[str] = None,
    ) -> dict[str, Any]:
        payload = {
            "tenant_user_id": tenant_user_id,
            "reason": reason,
        }
        return await self._request(
            "POST",
            f"/api/v1/deployments/{deployment_id}/stop",
            json_payload=_strip_nones(payload),
        )

    async def evaluate_deployment(
        self,
        *,
        tenant_user_id: str,
        deployment_id: str,
        market: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        payload = {
            "tenant_user_id": tenant_user_id,
            "market": market or {},
        }
        return await self._request(
            "POST",
            f"/api/v1/deployments/{deployment_id}/evaluate",
            json_payload=payload,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_payload: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> Any:
        headers = {}
        if self._api_key:
            headers["X-Backend-Api-Key"] = self._api_key
        try:
            response = await self._client.request(
                method,
                f"{self._base_url}{path}",
                json=json_payload,
                params=params,
                headers=headers,
            )
        except httpx.TimeoutException as exc:
            raise RuntimeError("ctrader_backend_timeout") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError("ctrader_backend_unreachable") from exc

        if response.status_code >= 400:
            detail = _extract_detail(response)
            raise RuntimeError(f"ctrader_backend_http_{response.status_code}:{detail}")
        return response.json()


def _extract_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
    text = (response.text or "").strip()
    return text[:200] if text else "unknown_error"


def _strip_nones(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}
