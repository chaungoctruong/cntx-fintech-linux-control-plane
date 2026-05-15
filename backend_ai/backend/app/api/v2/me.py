"""User-self endpoints: quota, activity, GDPR data export/delete.

Yeu cau Telegram auth (user_dep). Tat ca scope la `current user`.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Query

from app.api.v2.control_plane_deps import service_dep, translate_control_plane_error, user_dep
from app.services.control_plane_service import MT5ControlPlaneService

router = APIRouter(prefix="/me", tags=["me"])


@router.get("/notification-preferences")
async def get_my_notification_preferences(
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    """Tra notification preferences hien tai (channels x events)."""
    try:
        return service.get_notification_preferences(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc


@router.put("/notification-preferences")
async def update_my_notification_preferences(
    payload: dict = Body(...),
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    """Replace notification preferences. Body: {channels: {...}, events: {...}}.

    Server normalize: drop unknown channels/events, default missing fields.
    """
    if not isinstance(payload, dict):
        raise translate_control_plane_error(ValueError("invalid_request"))
    try:
        return service.update_notification_preferences(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
            preferences=payload,
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc


@router.get("/quota")
async def get_my_quota(
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    """Tra quota hien tai cua user (plan_code + limits + usage + remaining)."""
    try:
        return service.get_user_quota(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc


@router.get("/activity")
async def get_my_activity(
    limit: int = Query(default=50, ge=1, le=500),
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    """Lich su hoat dong dang human-readable cho user (audit_logs reformatted)."""
    try:
        items = service.list_user_activity(
            telegram_id=str(user["telegram_id"]),
            limit=int(limit),
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc
    return {"items": items}


@router.get("/export")
async def export_my_data(
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    """GDPR data export: tra ve toan bo data thuoc user (accounts, deployments,
    audit, risk policies). FE co the download dang JSON.
    """
    try:
        return service.export_user_data(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc


@router.get("/webhooks")
async def list_my_webhooks(
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    """List webhook user. KHONG tra secret_hex."""
    try:
        items = service.list_user_webhooks(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc
    return {"items": items}


@router.post("/webhooks")
async def create_my_webhook(
    payload: dict = Body(...),
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    """Tao webhook moi. Body: {url, event_filter}. Tra secret_hex MOT LAN duy nhat."""
    if not isinstance(payload, dict):
        raise translate_control_plane_error(ValueError("invalid_request"))
    url = payload.get("url") if isinstance(payload.get("url"), str) else ""
    event_filter = payload.get("event_filter") if isinstance(payload.get("event_filter"), list) else []
    try:
        return service.create_user_webhook(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
            url=url,
            event_filter=event_filter,
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc


@router.delete("/webhooks/{webhook_id}")
async def delete_my_webhook(
    webhook_id: int,
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    try:
        return service.delete_user_webhook(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
            webhook_id=webhook_id,
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc


@router.get("/onboarding")
async def get_my_onboarding(
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    """Tra trang thai onboarding tour cua user (completed_steps + next_step)."""
    try:
        return service.get_user_onboarding(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc


@router.post("/onboarding/dismiss")
async def dismiss_my_onboarding(
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    """Mark onboarding tour da dismissed. Idempotent."""
    try:
        return service.dismiss_user_onboarding(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc


@router.delete("")
async def delete_my_account(
    payload: Optional[dict] = Body(default=None),
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    """GDPR right-to-erasure: soft-delete user.

    - Stop tat ca running deployment.
    - Release tat ca login-slot reservation kep.
    - Mark accounts.status='disconnected', clear credentials encrypted blob.
    - Audit log + return summary.

    Body optional: {"reason": "..."}; mac dinh "user_self_delete".
    Khong xoa cung audit_logs/execution_events de giu phap ly + financial trace.
    """
    reason: Optional[str] = None
    if isinstance(payload, dict):
        raw = payload.get("reason")
        if isinstance(raw, str) and raw.strip():
            reason = raw.strip()[:200]
    try:
        result = await service.soft_delete_user(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
            reason=reason,
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc
    result["completed_at"] = int(time.time())
    return result
