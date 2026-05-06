"""Quota policy theo plan cuoc.

Nguon: bang `billing_subscriptions` (1 user co the co 0..n subscription;
ta lay subscription `active` moi nhat). Neu khong co -> mac dinh "free".

Mapping mac dinh:
  free       -> 1 active deployment, 1 broker account
  pro        -> 5 active deployments, 5 broker accounts
  enterprise -> unlimited (max_int)

Co the override qua settings:
  QUOTA_PLAN_LIMITS = {"free": {"max_active_deployments": 1, "max_accounts": 1}, ...}
"""
from __future__ import annotations

from typing import Any, Optional

from app.risk.orchestration_policy import OrchestrationPolicyError


_DEFAULT_LIMITS: dict[str, dict[str, int]] = {
    "free": {"max_active_deployments": 1, "max_accounts": 1},
    "pro": {"max_active_deployments": 5, "max_accounts": 5},
    "enterprise": {"max_active_deployments": 10**9, "max_accounts": 10**9},
}


def _normalize_plan(plan_code: Optional[str]) -> str:
    raw = str(plan_code or "").strip().lower()
    if raw in _DEFAULT_LIMITS:
        return raw
    return "free"


def _resolve_limits(plan_code: Optional[str], override: Optional[dict[str, Any]] = None) -> dict[str, int]:
    plan = _normalize_plan(plan_code)
    base = dict(_DEFAULT_LIMITS[plan])
    if override and isinstance(override, dict):
        plan_override = override.get(plan)
        if isinstance(plan_override, dict):
            for key, val in plan_override.items():
                try:
                    base[str(key)] = int(val)
                except (TypeError, ValueError):
                    continue
    return base


def get_user_plan(subscription: Optional[dict[str, Any]]) -> str:
    """Suy ra plan_code tu billing_subscriptions row, fallback 'free'."""
    if not subscription:
        return "free"
    if str(subscription.get("status") or "").strip().lower() != "active":
        return "free"
    return _normalize_plan(subscription.get("plan_code"))


def validate_can_start_new_deployment(
    *,
    subscription: Optional[dict[str, Any]],
    active_deployment_count: int,
    limits_override: Optional[dict[str, Any]] = None,
) -> None:
    """Raise OrchestrationPolicyError('quota_exceeded') neu user da dung het quota.

    `active_deployment_count` la so deployment dang o status start_requested/starting/running
    (TRUOC khi tao deployment moi).
    """
    plan = get_user_plan(subscription)
    limits = _resolve_limits(plan, limits_override)
    cap = int(limits.get("max_active_deployments") or 0)
    if cap <= 0:
        return
    if int(active_deployment_count) >= cap:
        raise OrchestrationPolicyError("quota_exceeded")


def validate_can_connect_new_account(
    *,
    subscription: Optional[dict[str, Any]],
    existing_account_count: int,
    limits_override: Optional[dict[str, Any]] = None,
) -> None:
    """Raise OrchestrationPolicyError('account_quota_exceeded') neu user da dung het quota account."""
    plan = get_user_plan(subscription)
    limits = _resolve_limits(plan, limits_override)
    cap = int(limits.get("max_accounts") or 0)
    if cap <= 0:
        return
    if int(existing_account_count) >= cap:
        raise OrchestrationPolicyError("account_quota_exceeded")


def describe_quota(
    *,
    subscription: Optional[dict[str, Any]],
    active_deployment_count: int,
    account_count: int,
    limits_override: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Snapshot cho FE hien thi quota panel."""
    plan = get_user_plan(subscription)
    limits = _resolve_limits(plan, limits_override)
    cap_dep = int(limits.get("max_active_deployments") or 0)
    cap_acc = int(limits.get("max_accounts") or 0)
    return {
        "plan_code": plan,
        "limits": {
            "max_active_deployments": cap_dep,
            "max_accounts": cap_acc,
        },
        "usage": {
            "active_deployments": int(active_deployment_count),
            "accounts": int(account_count),
        },
        "remaining": {
            "active_deployments": max(cap_dep - int(active_deployment_count), 0),
            "accounts": max(cap_acc - int(account_count), 0),
        },
    }
