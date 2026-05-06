"""Per-account risk policy + daily-loss circuit breaker.

Mac dinh disable. User chu dong opt-in qua API GET/PUT.

Khi `auto_stop_on_breach = True` va `realized_pnl_today <= -daily_loss_limit_usd`,
service goi DeploymentManagerService.stop_deployment cho moi deployment dang chay
cua account, voi reason='daily_loss_circuit_breaker'.

Trach nhiem:
- KHONG nam o Windows runner.
- Linux control plane chu dong stop.
- Runner chiu trach nhiem dung worker khi nhan STOP_BOT.
"""
from __future__ import annotations

from typing import Any, Optional

from app.orchestration.deployment_manager import DeploymentManagerService
from app.repositories.control_plane_repository import ControlPlaneRepository
from app.risk.orchestration_policy import OrchestrationPolicyError


# Field hop le trong policy. Caller co the bo sung field tuong lai (eg. max_open_positions).
_ALLOWED_POLICY_KEYS = {
    "daily_loss_limit_usd",
    "daily_loss_limit_percent",
    "auto_stop_on_breach",
    "timezone_offset_minutes",
    "notes",
    "updated_at",
    "updated_by",
}


def _coerce_policy(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Validate + normalize policy input. Raise OrchestrationPolicyError neu sai schema."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise OrchestrationPolicyError("invalid_risk_policy")

    out: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in _ALLOWED_POLICY_KEYS:
            # Bo qua key khong hop le thay vi raise -> de FE evolve them field tuong lai.
            continue
        if key in {"daily_loss_limit_usd", "daily_loss_limit_percent"}:
            if value is None:
                out[key] = None
                continue
            try:
                num = float(value)
            except (TypeError, ValueError) as exc:
                raise OrchestrationPolicyError("invalid_risk_policy") from exc
            if num < 0:
                # Limit phai duong (con neu dat = 0 nghia la disable)
                raise OrchestrationPolicyError("invalid_risk_policy")
            out[key] = num
        elif key == "timezone_offset_minutes":
            if value is None:
                out[key] = 0
                continue
            try:
                tz = int(value)
            except (TypeError, ValueError) as exc:
                raise OrchestrationPolicyError("invalid_risk_policy") from exc
            if tz < -14 * 60 or tz > 14 * 60:
                raise OrchestrationPolicyError("invalid_risk_policy")
            out[key] = tz
        elif key == "auto_stop_on_breach":
            out[key] = bool(value)
        elif key == "notes":
            out[key] = str(value or "").strip()[:500]
        elif key == "updated_at":
            try:
                out[key] = int(value)
            except (TypeError, ValueError):
                pass
        elif key == "updated_by":
            out[key] = str(value or "").strip()[:120]
    return out


def _is_breach(policy: dict[str, Any], realized_pnl_today: float) -> bool:
    """Kiem tra co vuot daily_loss_limit_usd khong (USD-based, percent-based xu ly sau)."""
    limit_usd = policy.get("daily_loss_limit_usd")
    if limit_usd in (None, 0):
        return False
    try:
        limit_val = float(limit_usd)
    except (TypeError, ValueError):
        return False
    if limit_val <= 0:
        return False
    return realized_pnl_today <= -limit_val


class AccountRiskPolicyService:
    def __init__(
        self,
        repo: ControlPlaneRepository,
        *,
        deployment_manager: Optional[DeploymentManagerService] = None,
    ) -> None:
        self._repo = repo
        self._deployment_manager = deployment_manager or DeploymentManagerService(repo)

    def get_policy(self, *, user_id: int, account_id: int) -> dict[str, Any]:
        policy = self._repo.get_account_risk_policy(account_id=account_id, user_id=user_id)
        if policy is None:
            raise OrchestrationPolicyError("account_not_found")
        return policy

    def update_policy(
        self,
        *,
        user_id: int,
        account_id: int,
        policy: dict[str, Any],
        actor: Optional[str] = None,
    ) -> dict[str, Any]:
        normalized = _coerce_policy(policy)
        if actor:
            normalized.setdefault("updated_by", str(actor))
        stored = self._repo.update_account_risk_policy(
            account_id=account_id,
            user_id=user_id,
            policy=normalized,
        )
        if stored is None:
            raise OrchestrationPolicyError("account_not_found")
        return stored

    def compute_daily_pnl(
        self,
        *,
        account_id: int,
        timezone_offset_minutes: int = 0,
    ) -> dict[str, Any]:
        return self._repo.compute_realized_pnl_today_for_account(
            account_id=account_id,
            timezone_offset_minutes=timezone_offset_minutes,
        )

    async def evaluate_circuit_breaker(
        self,
        *,
        user_id: int,
        account_id: int,
        actor: Optional[str] = None,
    ) -> dict[str, Any]:
        """Tinh PnL today + auto-stop deployment neu breach + auto-stop enabled.

        Tra ve aggregated result cho FE/audit.
        """
        policy = self._repo.get_account_risk_policy(account_id=account_id, user_id=user_id)
        if policy is None:
            raise OrchestrationPolicyError("account_not_found")

        tz_offset = int(policy.get("timezone_offset_minutes") or 0)
        pnl_payload = self._repo.compute_realized_pnl_today_for_account(
            account_id=account_id,
            timezone_offset_minutes=tz_offset,
        )
        realized_pnl = float(pnl_payload.get("realized_pnl_today") or 0.0)
        breach = _is_breach(policy, realized_pnl)
        auto_stop_enabled = bool(policy.get("auto_stop_on_breach"))

        deployments_to_stop: list[dict[str, Any]] = []
        deployments_stopped: list[dict[str, Any]] = []
        if breach and auto_stop_enabled:
            deployments_to_stop = list(self._repo.list_running_deployments_for_account(account_id=account_id))
            for deployment in deployments_to_stop:
                # Re-load deployment qua get_deployment de co full row (joins)
                full = self._repo.get_deployment(
                    deployment_id=int(deployment["id"]),
                    user_id=user_id,
                )
                if not full:
                    continue
                try:
                    stop_result = await self._deployment_manager.stop_deployment(
                        deployment=full,
                        reason=f"daily_loss_circuit_breaker:{actor or 'system'}",
                    )
                    deployments_stopped.append(
                        {
                            "deployment_id": int(deployment["id"]),
                            "status": str((stop_result.get("deployment") or {}).get("status") or "stop_requested"),
                            "command_id": str((stop_result.get("command") or {}).get("command_id") or ""),
                        }
                    )
                except Exception as exc:
                    deployments_stopped.append(
                        {
                            "deployment_id": int(deployment["id"]),
                            "status": "stop_failed",
                            "error": str(exc)[:200],
                        }
                    )

        return {
            "account_id": int(account_id),
            "policy": policy,
            "realized_pnl_today": realized_pnl,
            "today_start_ts": int(pnl_payload.get("today_start_ts") or 0),
            "event_count": int(pnl_payload.get("event_count") or 0),
            "breach_detected": breach,
            "auto_stop_enabled": auto_stop_enabled,
            "auto_stop_triggered": bool(breach and auto_stop_enabled),
            "deployments_evaluated_count": len(deployments_to_stop),
            "deployments_stopped": deployments_stopped,
        }
