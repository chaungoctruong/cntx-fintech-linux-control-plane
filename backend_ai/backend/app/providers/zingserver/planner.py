from __future__ import annotations

import time
from typing import Any, Optional

from app.providers.zingserver.client import ZingServerClient
from app.providers.zingserver.probe import sanitize_account, sanitize_cloud, sanitize_product


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _price_vnd(product: Optional[dict[str, Any]], period: str) -> Optional[int]:
    if not product:
        return None
    prices = product.get("prices") if isinstance(product.get("prices"), dict) else {}
    value = prices.get(period)
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _find_product(products: list[dict[str, Any]], plan_ref: str) -> Optional[dict[str, Any]]:
    plan_ref_s = _text(plan_ref)
    if not plan_ref_s:
        return None
    for product in products:
        if _text(product.get("planId")) == plan_ref_s:
            return product
        if _text(product.get("planCode")) == plan_ref_s:
            return product
    return None


def _supports_os(product: Optional[dict[str, Any]], os_id: int) -> bool:
    if not product or os_id <= 0:
        return False
    os_items = [item for item in product.get("os") or [] if isinstance(item, dict)]
    if not os_items:
        return True
    for item in os_items:
        if isinstance(item, dict) and _int(item.get("osId")) == os_id:
            return True
    return False


def _supports_location(product: Optional[dict[str, Any]], location_id: str) -> bool:
    location_s = _text(location_id)
    if not product or not location_s:
        return False
    for item in product.get("locations") or []:
        if isinstance(item, dict) and _text(item.get("locationId")) == location_s:
            return True
    return False


def build_zingserver_create_vps_plan(
    client: ZingServerClient,
    *,
    datacenter: str,
    plan_ref: str,
    os_id: int,
    location_id: str,
    runner_id: str,
    period: str = "monthly",
    quantity: int = 1,
    auto_renew: bool = False,
    coupon: str = "",
    install_chrome: bool = True,
    install_firefox: bool = False,
    max_active_clouds: int = 3,
    max_create_quantity: int = 1,
    max_total_cost_vnd: int = 2000000,
) -> dict[str, Any]:
    """Build a dry-run create-VPS plan.

    This function intentionally does not call POST /cloud/create-vps.
    """
    blockers: list[str] = []
    datacenter_s = _text(datacenter)
    plan_ref_s = _text(plan_ref)
    location_s = _text(location_id)
    runner_s = _text(runner_id)
    period_s = _text(period) or "monthly"
    quantity_i = max(1, _int(quantity, 1))
    os_id_i = _int(os_id)
    max_active_i = max(1, _int(max_active_clouds, 3))
    max_quantity_i = max(1, _int(max_create_quantity, 1))
    max_cost_i = max(0, _int(max_total_cost_vnd, 0))

    if not client.configured:
        blockers.append("zingserver_api_token_missing")
    if not datacenter_s:
        blockers.append("datacenter_required")
    if not plan_ref_s:
        blockers.append("plan_id_required")
    if os_id_i <= 0:
        blockers.append("os_id_required")
    if not location_s:
        blockers.append("location_id_required")
    if not runner_s:
        blockers.append("runner_id_required")
    if quantity_i > max_quantity_i:
        blockers.append("quantity_exceeds_limit")

    empty_report: dict[str, Any] = {
        "ok": not blockers,
        "generated_at": int(time.time()),
        "mode": "dry_run",
        "post_called": False,
        "would_call": "POST /cloud/create-vps",
        "ok_to_create": False,
        "blockers": blockers,
        "input": {
            "datacenter": datacenter_s or None,
            "plan_ref": plan_ref_s or None,
            "os_id": os_id_i or None,
            "location_id": location_s or None,
            "runner_id": runner_s or None,
            "period": period_s,
            "quantity": quantity_i,
        },
        "account": None,
        "current_clouds": None,
        "selected_product": None,
        "cost": None,
        "planned_request_body": None,
    }
    if blockers:
        return empty_report

    account_payload = client.account_detail()
    products_payload = client.products(datacenter=datacenter_s)
    clouds_payload = client.list_clouds(state="running")

    products = [item for item in products_payload.get("products") or [] if isinstance(item, dict)]
    clouds = [item for item in clouds_payload.get("clouds") or [] if isinstance(item, dict)]
    product = _find_product(products, plan_ref_s)
    unit_price = _price_vnd(product, period_s)
    total_cost = unit_price * quantity_i if unit_price is not None else None
    account = sanitize_account(account_payload)
    balance = _int((account.get("user") or {}).get("balance"))

    if not product:
        blockers.append("plan_not_found")
    elif unit_price is None:
        blockers.append("price_unknown")
    if product and not _supports_os(product, os_id_i):
        blockers.append("os_not_supported")
    if product and not _supports_location(product, location_s):
        blockers.append("location_not_supported")
    if len(clouds) >= max_active_i:
        blockers.append("max_active_clouds_reached")
    if total_cost is not None and max_cost_i > 0 and total_cost > max_cost_i:
        blockers.append("cost_exceeds_limit")
    if total_cost is not None and balance < total_cost:
        blockers.append("insufficient_balance")

    selected_plan_id = _text(product.get("planId")) if product else plan_ref_s
    request_body = {
        "planId": selected_plan_id,
        "period": period_s,
        "autoRenew": bool(auto_renew),
        "quantity": quantity_i,
        "coupon": _text(coupon),
        "osId": os_id_i,
        "locationId": location_s,
        "randomPassword": True,
        "randomRemotePort": True,
        "installChrome": bool(install_chrome),
        "installFirefox": bool(install_firefox),
        "note": f"cntx-runner {runner_s}",
    }

    return {
        "ok": True,
        "generated_at": int(time.time()),
        "mode": "dry_run",
        "post_called": False,
        "would_call": "POST /cloud/create-vps",
        "ok_to_create": not blockers,
        "blockers": blockers,
        "input": {
            "datacenter": datacenter_s,
            "plan_ref": plan_ref_s,
            "os_id": os_id_i,
            "location_id": location_s,
            "runner_id": runner_s,
            "period": period_s,
            "quantity": quantity_i,
            "max_active_clouds": max_active_i,
            "max_create_quantity": max_quantity_i,
            "max_total_cost_vnd": max_cost_i,
        },
        "account": account,
        "current_clouds": {
            "state": "running",
            "count": len(clouds),
            "items": [sanitize_cloud(item) for item in clouds],
        },
        "selected_product": sanitize_product(product) if product else None,
        "cost": {
            "period": period_s,
            "unit_price_vnd": unit_price,
            "quantity": quantity_i,
            "total_cost_vnd": total_cost,
            "balance_vnd": balance,
        },
        "planned_request_body": request_body,
    }
