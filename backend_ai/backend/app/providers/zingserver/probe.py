from __future__ import annotations

import time
from typing import Any, Optional

from app.providers.zingserver.client import ZingServerClient


_SENSITIVE_KEYS = {
    "defaultpassword",
    "password",
    "access_token",
    "accesstoken",
    "token",
    "authorization",
    "secret",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _is_sensitive_key(key: Any) -> bool:
    raw = _text(key).lower().replace("_", "").replace("-", "")
    if raw == "randompassword":
        return False
    return any(item in raw for item in _SENSITIVE_KEYS)


def scrub_zingserver_payload(value: Any) -> Any:
    """Remove provider secrets before printing or writing artifacts."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_s = str(key)
            if _is_sensitive_key(key_s):
                out[key_s] = "[REDACTED]" if item is not None else None
            else:
                out[key_s] = scrub_zingserver_payload(item)
        return out
    if isinstance(value, list):
        return [scrub_zingserver_payload(item) for item in value]
    return value


def mask_email(value: Any) -> str:
    raw = _text(value)
    if "@" not in raw:
        return raw
    local, _, domain = raw.partition("@")
    if len(local) <= 2:
        masked_local = local[:1] + "***"
    else:
        masked_local = f"{local[:2]}***{local[-1:]}"
    return f"{masked_local}@{domain}"


def sanitize_account(payload: dict[str, Any]) -> dict[str, Any]:
    user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
    return {
        "status": payload.get("status"),
        "user": {
            "customerId": user.get("customerId"),
            "email_masked": mask_email(user.get("email")),
            "balance": user.get("balance"),
        },
    }


def sanitize_cloud(cloud: dict[str, Any]) -> dict[str, Any]:
    clean = scrub_zingserver_payload(cloud)
    allowed = {
        "active",
        "sourceName",
        "uId",
        "ip",
        "port",
        "state",
        "cpu",
        "ssd",
        "ram",
        "bandwidth",
        "ethernetPort",
        "countryName",
        "isAutoRenew",
        "userName",
        "product",
        "location",
        "os",
        "v_changeIp",
        "createdAt",
        "dateEnd",
        "note",
    }
    return {key: clean.get(key) for key in sorted(allowed) if key in clean}


def sanitize_product(product: dict[str, Any]) -> dict[str, Any]:
    return {
        "planCode": product.get("planCode"),
        "planId": product.get("planId"),
        "countryCode": product.get("countryCode"),
        "countryName": product.get("countryName"),
        "config": product.get("config") or {},
        "prices": product.get("prices") or {},
        "locations": product.get("locations") or [],
        "os": product.get("os") or [],
    }


def _find_cloud_by_ip(clouds: list[dict[str, Any]], match_ip: str) -> Optional[dict[str, Any]]:
    ip_s = _text(match_ip)
    if not ip_s:
        return None
    for cloud in clouds:
        if _text(cloud.get("ip")) == ip_s:
            return cloud
    return None


def _find_product(products: list[dict[str, Any]], cloud: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not cloud:
        return None
    product_ref = cloud.get("product") if isinstance(cloud.get("product"), dict) else {}
    plan_id = _text(product_ref.get("planId"))
    plan_code = _text(product_ref.get("planCode"))
    for product in products:
        if plan_id and _text(product.get("planId")) == plan_id:
            return product
        if plan_code and _text(product.get("planCode")) == plan_code:
            return product
    return None


def _active_windows_os(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items:
        if _text(item.get("osType")).lower() != "windows":
            continue
        if _text(item.get("status")).lower() not in {"", "active"}:
            continue
        out.append(
            {
                "osId": item.get("osId"),
                "osName": item.get("osName"),
                "status": item.get("status"),
            }
        )
    return out


def build_zingserver_probe_report(
    client: ZingServerClient,
    *,
    datacenter: str = "",
    country: str = "",
    cloud_state: str = "running",
    match_ip: str = "",
) -> dict[str, Any]:
    """Build a read-only provider probe report.

    No create/renew/action endpoint is called here.
    """
    account_payload = client.account_detail()
    datacenters_payload = client.datacenters(country=country or None)
    os_payload = client.operating_systems()
    locations_payload = client.locations()
    clouds_payload = client.list_clouds(state=cloud_state)
    products_payload: dict[str, Any] = {"status": "skipped", "products": []}
    if _text(datacenter):
        products_payload = client.products(datacenter=datacenter)

    clouds = [item for item in clouds_payload.get("clouds") or [] if isinstance(item, dict)]
    products = [item for item in products_payload.get("products") or [] if isinstance(item, dict)]
    matched_cloud = _find_cloud_by_ip(clouds, match_ip)
    matched_product = _find_product(products, matched_cloud)

    return {
        "ok": True,
        "generated_at": int(time.time()),
        "mode": "read_only",
        "account": sanitize_account(account_payload),
        "datacenters": {
            "country_filter": country or None,
            "count": len(datacenters_payload.get("datacenters") or []),
            "items": scrub_zingserver_payload(datacenters_payload.get("datacenters") or []),
        },
        "products": {
            "datacenter": datacenter or None,
            "count": len(products),
            "items": [sanitize_product(item) for item in products],
        },
        "operating_systems": {
            "windows_active": _active_windows_os(
                [item for item in os_payload.get("operatingSystems") or [] if isinstance(item, dict)]
            ),
        },
        "locations": {
            "count": len(locations_payload.get("locations") or []),
            "items": scrub_zingserver_payload(locations_payload.get("locations") or []),
        },
        "clouds": {
            "state": cloud_state,
            "count": len(clouds),
            "items": [sanitize_cloud(item) for item in clouds],
        },
        "match": {
            "ip": match_ip or None,
            "cloud": sanitize_cloud(matched_cloud) if matched_cloud else None,
            "product": sanitize_product(matched_product) if matched_product else None,
        },
        "next_step": "choose_runner_vps_profile",
    }
