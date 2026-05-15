from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.v2.control_plane_deps import service_dep, translate_control_plane_error, user_dep
from app.api.v2.terms_deps import require_miniapp_terms_accepted
from app.schemas.control_plane import AccountConnectRequest
from app.services.control_plane_service import MT5ControlPlaneService

router = APIRouter(prefix="/accounts", tags=["mt5-accounts"])

CONNECT_PENDING_RUNTIME_LOGIN = "LOGIN_IN_PROGRESS"


def _legacy_login_slot_response(response: dict) -> dict:
    """Compatibility shim for Mini App bundles deployed before login-slot."""
    payload = dict(response or {})
    reservation_id = payload.get("login_reservation_id") or payload.get("id")
    status = str(payload.get("status") or "").strip().lower()
    login_state = str(payload.get("login_state") or "").strip().upper()
    if login_state == "READY" or status in {"verified", "claimed"}:
        legacy_state = "VERIFIED"
        legacy_status = "verified"
    elif status in {"failed", "expired", "released", "cancelled"} or login_state == "FAILED":
        legacy_state = "FAILED"
        legacy_status = "failed" if status in {"expired", "released"} else (status or "failed")
    else:
        legacy_state = "VERIFYING"
        legacy_status = status or str(payload.get("login_state") or "pending").strip().lower()
    payload["status"] = legacy_status
    payload["job_status"] = legacy_status
    payload["job_id"] = reservation_id
    payload["verification_job_id"] = reservation_id
    payload["verification_state"] = legacy_state
    payload["verification_ui_state"] = legacy_state
    payload["next_action"] = "START_BOT" if legacy_state == "VERIFIED" else (payload.get("next_action") or "POLL_LOGIN_SLOT")
    return payload


def _runtime_login_ready_account(account: dict) -> dict:
    response = dict(account or {})
    response.setdefault("raw_status", response.get("status"))
    response["status"] = "connected"
    response["has_credentials"] = True
    response["connect_status"] = CONNECT_PENDING_RUNTIME_LOGIN
    response["connection_state"] = CONNECT_PENDING_RUNTIME_LOGIN
    response["runtime_login_required"] = True
    response["next_action"] = "POLL_LOGIN_SLOT"
    return response


@router.post("/connect")
async def connect_account(
    payload: AccountConnectRequest,
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
    _terms_accepted: None = Depends(require_miniapp_terms_accepted),
) -> dict:
    del _terms_accepted
    account: dict | None = None
    try:
        account = service.connect_account(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
            **payload.model_dump(),
        )
        login_slot = await service.request_account_login_slot(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
            account_id=int(account["id"]),
        )
    except Exception as exc:
        if account and account.get("id"):
            service.mark_account_login_request_failed(
                telegram_id=str(user["telegram_id"]),
                username=user.get("username"),
                account_id=int(account["id"]),
                reason=str(exc),
            )
        raise translate_control_plane_error(exc) from exc
    account_response = _runtime_login_ready_account(account)
    if isinstance(login_slot.get("account"), dict):
        account_response.update(login_slot["account"])
    return {
        "account_id": account.get("id"),
        "login_reservation_id": login_slot.get("login_reservation_id"),
        "status": login_slot.get("status") or "dispatched",
        "login_state": login_slot.get("login_state"),
        "connect_status": login_slot.get("connect_status") or CONNECT_PENDING_RUNTIME_LOGIN,
        "connection_state": login_slot.get("connection_state") or CONNECT_PENDING_RUNTIME_LOGIN,
        "next_action": login_slot.get("next_action") or "POLL_LOGIN_SLOT",
        "runner_id": login_slot.get("runner_id"),
        "slot_id": login_slot.get("slot_id"),
        "trace_id": login_slot.get("trace_id"),
        "command_id": login_slot.get("command_id"),
        "redis_stream_id": login_slot.get("redis_stream_id"),
        "expires_at": login_slot.get("expires_at"),
        "runtime_login_required": True,
        "account": account_response,
        "login_slot": login_slot,
    }


@router.post("/verify")
async def request_account_login_slot_legacy(
    payload: dict = Body(...),
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    try:
        account_id = int((payload or {}).get("account_id"))
    except (TypeError, ValueError) as exc:
        raise translate_control_plane_error(ValueError("account_not_found")) from exc
    try:
        result = await service.request_account_login_slot(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
            account_id=account_id,
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc
    return _legacy_login_slot_response(result)


@router.post("/{account_id}/login-slot")
async def request_account_login_slot(
    account_id: int,
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    try:
        return await service.request_account_login_slot(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
            account_id=account_id,
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc


@router.patch("/{account_id}")
async def patch_account(
    account_id: int,
    payload: dict = Body(...),
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    """PATCH label / sort_order cho account. Chi update field truyen vao."""
    if not isinstance(payload, dict):
        raise translate_control_plane_error(ValueError("invalid_request"))
    label = payload.get("label") if "label" in payload else None
    sort_order = payload.get("sort_order") if "sort_order" in payload else None
    try:
        account = service.patch_account_label(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
            account_id=account_id,
            label=label,
            sort_order=sort_order,
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc
    return {"account_id": account_id, "account": account}


@router.put("/{account_id}/credentials")
async def update_account_credentials(
    account_id: int,
    payload: dict = Body(...),
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    """Re-key broker password ma KHONG can xoa + tao lai account.

    Body: {"password": "<new password>"} (8-256 chars)

    Behavior:
      - 409 cannot_update_credentials_while_active neu account co bot dang chay.
      - Sau khi rotate: account van san sang; START_BOT se dang nhap MT5 tren runner.
      - Audit log "account.credentials.update".
      - KHONG tra ve plaintext password nao trong response.
    """
    if not isinstance(payload, dict):
        raise translate_control_plane_error(ValueError("invalid_credentials_payload"))
    new_password = payload.get("password")
    try:
        account = service.update_account_credentials(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
            account_id=account_id,
            password=new_password if isinstance(new_password, str) else "",
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc
    return {"account_id": account_id, "account": account, "status": account.get("status")}


@router.get("/login-slots/{reservation_id}")
async def get_account_login_slot(
    reservation_id: int,
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    try:
        return service.get_account_login_slot(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
            reservation_id=reservation_id,
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc


@router.get("/verifications/{job_id}")
async def get_account_login_slot_legacy(
    job_id: int,
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    try:
        result = service.get_account_login_slot(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
            reservation_id=job_id,
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc
    if not result:
        raise translate_control_plane_error(ValueError("login_reservation_not_found"))
    return _legacy_login_slot_response(result)


@router.delete("/{account_id}")
async def delete_account(
    account_id: int,
    payload: Optional[dict] = Body(default=None),
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    """Soft-delete one MT5 account owned by the current Mini App user.

    This does not place runner commands. It blocks while a bot is active,
    releases any pending login-slot hold, marks the account disconnected, and
    scrubs the encrypted credential blob.
    """
    reason: Optional[str] = None
    if isinstance(payload, dict):
        raw_reason = payload.get("reason")
        if isinstance(raw_reason, str) and raw_reason.strip():
            reason = raw_reason.strip()[:200]
    try:
        return await service.delete_account(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
            account_id=account_id,
            reason=reason,
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc


@router.get("/{account_id}")
async def get_account(
    account_id: int,
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    account = service.get_account(
        telegram_id=str(user["telegram_id"]),
        username=user.get("username"),
        account_id=account_id,
    )
    if not account:
        raise HTTPException(status_code=404, detail="account_not_found")
    return account


@router.get("/{account_id}/state")
async def get_account_state(
    account_id: int,
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    state = service.get_account_state(
        telegram_id=str(user["telegram_id"]),
        username=user.get("username"),
        account_id=account_id,
    )
    if not state:
        raise HTTPException(status_code=404, detail="account_not_found")
    return state


@router.get("/{account_id}/positions")
async def get_account_positions(
    account_id: int,
    deployment_id: Optional[int] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    positions = service.list_account_positions(
        telegram_id=str(user["telegram_id"]),
        username=user.get("username"),
        account_id=account_id,
        deployment_id=deployment_id,
        limit=limit,
    )
    return {"items": positions}


@router.get("/{account_id}/scheduler-preview")
async def get_account_scheduler_preview(
    account_id: int,
    bot_code: Optional[str] = Query(default=None, min_length=1),
    bot_name: Optional[str] = Query(default=None, min_length=1),
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    """Read-only preview slot runner/slot se duoc chon neu Start bot.

    Khong tao deployment, khong publish Redis, khong START_BOT.
    """
    requested_bot = (bot_code or bot_name or "").strip()
    if not requested_bot:
        raise translate_control_plane_error(ValueError("invalid_request"))
    try:
        return service.scheduler_preview(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
            account_id=account_id,
            bot_name=requested_bot,
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc


@router.get("/{account_id}/risk-policy")
async def get_account_risk_policy(
    account_id: int,
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    """Tra ve risk_policy_json hien tai cua account.

    Empty {} = chua bat circuit breaker. Field hop le:
      - daily_loss_limit_usd (number, USD, am khong duoc, 0 = disable)
      - daily_loss_limit_percent (number, % balance dau ngay, reserved cho buoc sau)
      - auto_stop_on_breach (bool)
      - timezone_offset_minutes (int, -840..840)
      - notes (str, <=500 chars)
    """
    try:
        policy = service.get_account_risk_policy(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
            account_id=account_id,
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc
    return {"account_id": account_id, "policy": policy}


@router.put("/{account_id}/risk-policy")
async def update_account_risk_policy(
    account_id: int,
    payload: dict = Body(...),
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    """Replace risk_policy_json (full overwrite). 400 invalid_risk_policy neu sai schema."""
    if not isinstance(payload, dict):
        raise translate_control_plane_error(ValueError("invalid_risk_policy"))
    try:
        stored = service.update_account_risk_policy(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
            account_id=account_id,
            policy=payload,
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc
    return {"account_id": account_id, "policy": stored}


@router.post("/{account_id}/risk-policy/evaluate")
async def evaluate_account_circuit_breaker(
    account_id: int,
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    """Tinh realized PnL today + auto-stop neu vuot daily_loss_limit_usd va auto_stop=on.

    Idempotent: goi nhieu lan an toan, chi auto-stop khi co deployment dang chay va thuc su breach.
    """
    try:
        result = await service.evaluate_account_circuit_breaker(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
            account_id=account_id,
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc
    return result
