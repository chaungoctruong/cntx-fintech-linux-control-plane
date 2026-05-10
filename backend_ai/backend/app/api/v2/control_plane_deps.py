from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, status

from app.api.v2.error_catalog import to_http_exception
from app.core.auth import get_tg_user
from app.risk.orchestration_policy import OrchestrationPolicyError
from app.services import login_lease
from app.services.control_plane_service import MT5ControlPlaneService, get_control_plane_service


def service_dep() -> MT5ControlPlaneService:
    return get_control_plane_service()


def user_dep(
    user: dict[str, Any] = Depends(get_tg_user),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict[str, Any]:
    try:
        user_row = service.ensure_user(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc
    if isinstance(user_row, dict) and user_row.get("id") is not None:
        return {**user, "user_id": user_row.get("id")}
    return user


# Map cac UNIQUE constraint name -> control-plane error code public.
# Khi runner/control plane race nhau (vi du double-click start bot), Postgres se raise
# UniqueViolation; ta convert thanh public_code thay vi tra 500.
_UNIQUE_CONSTRAINT_TO_CODE: dict[str, str] = {
    "uq_bot_deployments_active_account": "account_has_active_deployment",
    "uq_bot_deployments_active_user": "telegram_user_has_active_bot",
    "uq_account_verification_jobs_active_account": "verification_already_pending",
    "uq_account_slot_bindings_current_account": "account_has_active_deployment",
    "uq_account_slot_bindings_current_slot": "no_available_unreserved_slot",
}


def _detect_unique_violation_code(exc: Exception) -> str | None:
    """Phat hien psycopg2.errors.UniqueViolation va map sang code public.

    Khong import psycopg2 cung de tranh hard dependency tai test environment;
    dung duck typing tren attribute `pgcode` + `diag.constraint_name`.
    """
    pgcode = getattr(exc, "pgcode", None)
    if str(pgcode or "") != "23505":
        return None
    diag = getattr(exc, "diag", None)
    constraint = getattr(diag, "constraint_name", None) if diag is not None else None
    if constraint and constraint in _UNIQUE_CONSTRAINT_TO_CODE:
        return _UNIQUE_CONSTRAINT_TO_CODE[constraint]
    text = str(exc).lower()
    for name, code in _UNIQUE_CONSTRAINT_TO_CODE.items():
        if name in text:
            return code
    return None


def translate_control_plane_error(exc: Exception) -> HTTPException:
    """Chuan hoa moi exception tu service layer -> HTTPException voi payload chuan.

    Quy tac:
    - Neu da la HTTPException -> tra nguyen, route handler tu lo.
    - Neu la LoginLeaseConflict -> 409 LOGIN_BUSY voi owner info trong error_info.
    - Neu la RuntimeError("login_lease_unavailable") -> 503 (fail-closed).
    - Neu la UniqueViolation tu Postgres -> map theo constraint name.
    - Con lai dung error_catalog.to_http_exception (co fallback 400 + payload chuan).
    """
    if isinstance(exc, HTTPException):
        return exc

    if isinstance(exc, login_lease.LoginLeaseConflict):
        http_exc = to_http_exception("login_busy")
        try:
            http_exc.error_info["owner_runner_id"] = exc.result.owner_runner_id
            http_exc.error_info["owner_command_id"] = exc.result.owner_command_id
            http_exc.error_info["owner_leased_at"] = exc.result.owner_leased_at
        except Exception:
            pass
        return http_exc

    detail = str(exc) or exc.__class__.__name__

    if detail == "login_lease_unavailable":
        return to_http_exception("login_lease_unavailable")

    unique_code = _detect_unique_violation_code(exc)
    if unique_code is not None:
        return to_http_exception(unique_code)

    if isinstance(exc, OrchestrationPolicyError):
        return to_http_exception(detail, fallback_status=status.HTTP_400_BAD_REQUEST)

    return to_http_exception(detail, fallback_status=status.HTTP_400_BAD_REQUEST)
