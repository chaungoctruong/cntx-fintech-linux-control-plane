from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.v2.control_plane_deps import service_dep, translate_control_plane_error, user_dep
from app.api.v2.terms_deps import require_miniapp_terms_accepted
from app.schemas.control_plane import AccountConnectRequest, AccountVerifyRequest
from app.services.control_plane_service import MT5ControlPlaneService

router = APIRouter(prefix="/accounts", tags=["mt5-accounts"])

CONNECT_PENDING_RUNTIME_LOGIN = "PENDING_RUNTIME_LOGIN"


def _runtime_login_ready_account(account: dict) -> dict:
    response = dict(account or {})
    response.setdefault("raw_status", response.get("status"))
    response["status"] = "connected"
    response["has_credentials"] = True
    response["connect_status"] = CONNECT_PENDING_RUNTIME_LOGIN
    response["connection_state"] = CONNECT_PENDING_RUNTIME_LOGIN
    # DB row is pending_verification until runner proves MT5 login (verify job or START_BOT).
    response["verification_state"] = "VERIFYING"
    response["verification_ui_state"] = "SUBMITTED"
    response["runtime_login_required"] = True
    response["next_action"] = "REQUEST_VERIFY_OR_START_BOT"
    return response


@router.post("/connect")
async def connect_account(
    payload: AccountConnectRequest,
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
    _terms_accepted: None = Depends(require_miniapp_terms_accepted),
) -> dict:
    del _terms_accepted
    try:
        account = service.connect_account(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
            **payload.model_dump(),
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc
    account_response = _runtime_login_ready_account(account)
    return {
        "account_id": account.get("id"),
        "verification_job_id": None,
        "job_id": None,
        "status": "connected",
        "connect_status": CONNECT_PENDING_RUNTIME_LOGIN,
        "connection_state": CONNECT_PENDING_RUNTIME_LOGIN,
        "verification_state": "VERIFYING",
        "verification_ui_state": "SUBMITTED",
        "next_action": "REQUEST_VERIFY_OR_START_BOT",
        "runtime_login_required": True,
        "account": account_response,
    }


@router.post("/verify")
async def verify_account(
    payload: AccountVerifyRequest,
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    try:
        result = await service.request_account_verification(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
            account_id=payload.account_id,
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc
    return result


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


@router.get("/verifications/{job_id}")
async def get_account_verification(
    job_id: str,
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    raw_job_id = str(job_id or "").strip()
    if raw_job_id.lower() in {"", "none", "null", "undefined"}:
        return {
            "job_id": None,
            "verification_job_id": None,
            "status": "verified",
            "job_status": "verified",
            "verification_state": "VERIFIED",
            "verification_ui_state": "VERIFIED",
            "connect_status": CONNECT_PENDING_RUNTIME_LOGIN,
            "connection_state": CONNECT_PENDING_RUNTIME_LOGIN,
            "next_action": "START_BOT",
            "runtime_login_required": True,
            "runner_id": None,
            "slot_id": None,
            "trace_id": None,
            "redis_stream_id": None,
            "job": None,
        }
    try:
        job_id_i = int(raw_job_id)
    except (TypeError, ValueError) as exc:
        raise translate_control_plane_error(ValueError("verification_job_not_found")) from exc
    job = service.get_account_verification(
        telegram_id=str(user["telegram_id"]),
        username=user.get("username"),
        job_id=job_id_i,
    )
    if not job:
        raise translate_control_plane_error(ValueError("verification_job_not_found"))
    response = dict(job)
    response["job_id"] = response.get("id")
    return response


@router.delete("/verifications/{job_id}")
async def cancel_account_verification(
    job_id: int,
    payload: Optional[dict] = Body(default=None),
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    """Huy 1 verification job dang cho/dispatched cua user.

    - Idempotent: goi lai khi da cancelled tra ve job hien tai (200).
    - Khong cho cancel khi job da verified/failed -> 409 verification_already_completed.
    - Best-effort phat signal Redis cho runner skip som; flag tra ve `cancel_signal_emitted`.
    """
    reason = None
    if isinstance(payload, dict):
        raw_reason = payload.get("reason")
        if isinstance(raw_reason, str) and raw_reason.strip():
            reason = raw_reason.strip()[:200]
    try:
        result = await service.cancel_account_verification(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
            job_id=job_id,
            reason=reason,
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc
    response = dict(result)
    response["job_id"] = response.get("id")
    return response


@router.delete("/{account_id}")
async def delete_account(
    account_id: int,
    payload: Optional[dict] = Body(default=None),
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    """Soft-delete one MT5 account owned by the current Mini App user.

    This does not place runner commands. It blocks while a bot is active, cancels
    pending verification jobs best-effort, marks the account disconnected, and
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


@router.get("/{account_id}/verifications")
async def get_account_verifications(
    account_id: int,
    limit: int = Query(default=50, ge=1, le=500),
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    jobs = service.list_account_verifications(
        telegram_id=str(user["telegram_id"]),
        username=user.get("username"),
        account_id=account_id,
        limit=limit,
    )
    return {"items": jobs}


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


@router.post("/{account_id}/verifications/cancel-all")
async def cancel_all_account_verifications(
    account_id: int,
    payload: Optional[dict] = Body(default=None),
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    """Huy moi verification job dang pending/dispatched cua 1 account.

    Idempotent: goi lai khi khong con job nao tra ve scanned_count=cancelled_count=0.
    Owner check: 404 account_not_found neu account khong thuoc user hien tai.
    """
    reason: Optional[str] = None
    if isinstance(payload, dict):
        raw_reason = payload.get("reason")
        if isinstance(raw_reason, str) and raw_reason.strip():
            reason = raw_reason.strip()[:200]
    try:
        result = await service.cancel_all_account_verifications(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
            account_id=account_id,
            reason=reason,
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc
    return result
