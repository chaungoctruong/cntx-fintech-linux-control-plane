from __future__ import annotations

from typing import Any, Optional
from urllib.parse import quote

import httpx

from app.settings import settings


class MT5RunnerControlPlaneClient:
    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
        timeout_sec: float = 15.0,
    ) -> None:
        resolved_base_url = (
            str(base_url or "").strip()
            or str(getattr(settings, "RUNNER_CONTROL_PLANE_URL", "") or "").strip()
            or str(getattr(settings, "BACKEND_URL", "") or "").strip()
        ).rstrip("/")
        if not resolved_base_url:
            raise ValueError("runner_control_plane_url_required")
        self._base_url = resolved_base_url
        self._api_key = str(api_key or getattr(settings, "BACKEND_API_KEY", "") or "").strip()
        self._client = client
        self._timeout_sec = max(5.0, float(timeout_sec or 15.0))

    async def _request(self, method: str, path: str, *, json_payload: Optional[dict[str, Any]] = None) -> Any:
        headers = {}
        if self._api_key:
            headers["X-Backend-Api-Key"] = self._api_key
        if self._client is not None:
            response = await self._client.request(method, f"{self._base_url}{path}", json=json_payload, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=self._timeout_sec) as client:
                response = await client.request(method, f"{self._base_url}{path}", json=json_payload, headers=headers)
        response.raise_for_status()
        return response.json()

    async def register_runner(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/api/v2/runner/register", json_payload=payload)

    async def bootstrap(self, runner_id: Optional[str] = None) -> dict[str, Any]:
        suffix = f"?runner_id={quote(str(runner_id), safe='')}" if runner_id else ""
        return await self._request("GET", f"/api/v2/runner/bootstrap{suffix}")

    async def heartbeat(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/api/v2/runner/heartbeat", json_payload=payload)

    async def emit_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/api/v2/runner/events", json_payload=payload)

    async def fetch_account_bundle(self, account_id: int) -> dict[str, Any]:
        return await self._request("GET", f"/api/v2/runner/accounts/{int(account_id)}/bundle")

    async def fetch_deployment_package(self, deployment_id: int) -> dict[str, Any]:
        return await self._request("GET", f"/api/v2/runner/deployments/{int(deployment_id)}/package")

    async def submit_verification_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/api/v2/runner/account-verifications/result", json_payload=payload)

    async def get_command(self, command_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/v2/runner/commands/{command_id}")

    async def claim_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/api/v2/runner/commands/claim", json_payload=payload)

    async def update_command_delivery(self, command_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", f"/api/v2/runner/commands/{command_id}/delivery", json_payload=payload)
