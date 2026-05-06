from __future__ import annotations

from typing import Any, Callable, Optional
from urllib.parse import urljoin

import requests


RequestFn = Callable[..., Any]


class ZingServerError(RuntimeError):
    """Raised for ZingServer API or transport failures."""


class ZingServerClient:
    """Minimal read-only client for ZingServer Cloud API.

    This client intentionally exposes no create/renew/action methods yet. Provisioning
    will be added later behind explicit approval flags.
    """

    def __init__(
        self,
        *,
        base_url: str,
        access_token: str,
        timeout_sec: float = 15.0,
        request_fn: Optional[RequestFn] = None,
    ) -> None:
        self._base_url = str(base_url or "").strip().rstrip("/") or "https://api.zingserver.com"
        self._access_token = str(access_token or "").strip()
        self._timeout_sec = max(1.0, float(timeout_sec or 15.0))
        self._request_fn = request_fn or requests.request

    @property
    def configured(self) -> bool:
        return bool(self._access_token)

    @property
    def base_url(self) -> str:
        return self._base_url

    def _headers(self) -> dict[str, str]:
        if not self._access_token:
            raise ZingServerError("zingserver_api_token_missing")
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._access_token}",
            "User-Agent": "CNTxLabs-Ops/1.0",
        }

    def _url(self, path: str) -> str:
        return urljoin(f"{self._base_url}/", str(path or "").lstrip("/"))

    def _request(self, method: str, path: str, *, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        try:
            response = self._request_fn(
                method.upper(),
                self._url(path),
                headers=self._headers(),
                params=params or None,
                timeout=self._timeout_sec,
            )
        except ZingServerError:
            raise
        except Exception as exc:
            raise ZingServerError(f"zingserver_transport_error:{exc.__class__.__name__}") from exc

        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code < 200 or status_code >= 300:
            raise ZingServerError(f"zingserver_http_{status_code}")
        try:
            payload = response.json()
        except Exception as exc:
            raise ZingServerError("zingserver_invalid_json") from exc
        if not isinstance(payload, dict):
            raise ZingServerError("zingserver_unexpected_response")
        if str(payload.get("status") or "").lower() not in {"", "success"}:
            raise ZingServerError(str(payload.get("message") or payload.get("error") or "zingserver_api_error"))
        return payload

    def account_detail(self) -> dict[str, Any]:
        return self._request("GET", "/account/detail")

    def billing_invoices(self, *, page: int = 1) -> dict[str, Any]:
        params = {"page": max(1, int(page))}
        return self._request("GET", "/billing/invoices", params=params)

    def countries(self) -> dict[str, Any]:
        return self._request("GET", "/cloud/countries")

    def datacenters(self, *, country: Optional[str] = None) -> dict[str, Any]:
        params = {"country": str(country).strip()} if str(country or "").strip() else None
        return self._request("GET", "/cloud/datacenters", params=params)

    def products(self, *, datacenter: str) -> dict[str, Any]:
        datacenter_s = str(datacenter or "").strip()
        if not datacenter_s:
            raise ZingServerError("datacenter_required")
        return self._request("GET", "/cloud/products", params={"datacenter": datacenter_s})

    def operating_systems(self) -> dict[str, Any]:
        return self._request("GET", "/cloud/operating-system")

    def locations(self) -> dict[str, Any]:
        return self._request("GET", "/cloud/locations")

    def list_clouds(self, *, state: str = "running") -> dict[str, Any]:
        state_s = str(state or "running").strip().lower()
        if state_s not in {"running", "expiring", "cancelled", "all"}:
            raise ZingServerError("invalid_cloud_state")
        return self._request("GET", f"/cloud/list/{state_s}")

    def cloud_detail(self, *, uid: str) -> dict[str, Any]:
        uid_s = str(uid or "").strip()
        if not uid_s:
            raise ZingServerError("uid_required")
        return self._request("GET", f"/cloud/detail/{uid_s}")
