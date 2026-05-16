from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from app.events.command_router import CommandRouterService
from app.models.control_plane import CommandType, DeploymentStatus
from app.orchestration.deployment_manager import DeploymentManagerService
from app.repositories.control_plane_repository import ControlPlaneRepository
from app.risk.orchestration_policy import OrchestrationPolicyError
from app.settings import settings

log = logging.getLogger("runner.failover")


_RETRYABLE_START_REASONS = {
    "account_runtime_duplicate_requires_operator_cleanup",
    "no_available_unreserved_slot",
    "no_healthy_slot_available",
    "runner_full",
    "runner_offline",
    "runner_queue_backlog",
    "start_transition_in_progress",
    "sticky_slot_unavailable",
    "windows_runtime_unhealthy",
}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _dict_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _failover_grace_sec() -> int:
    configured = int(getattr(settings, "RUNNER_OFFLINE_FAILOVER_GRACE_SEC", 240) or 240)
    runner_stale = int(getattr(settings, "CONTROL_PLANE_RUNNER_STALE_SEC", 180) or 180)
    runtime_guard = int(getattr(settings, "ACCOUNT_RUNTIME_START_GUARD_STALE_SEC", 180) or 180)
    # Wait beyond the account runtime duplicate guard so a dead runner's last
    # snapshot does not block the replacement START_BOT on a healthy runner.
    return max(60, configured, runner_stale, runtime_guard + 30)


def _retry_wait_sec() -> int:
    return max(30, int(getattr(settings, "RUNNER_OFFLINE_FAILOVER_RETRY_SEC", 60) or 60))


def _batch_size() -> int:
    return max(1, min(int(getattr(settings, "RUNNER_OFFLINE_FAILOVER_BATCH_SIZE", 5) or 5), 50))


def _is_enabled() -> bool:
    return bool(getattr(settings, "RUNNER_OFFLINE_FAILOVER_ENABLED", True))


def _fence_stop_enabled() -> bool:
    return bool(getattr(settings, "RUNNER_OFFLINE_FAILOVER_FENCE_STOP_ENABLED", True))


def _retryable_start_error(exc: Exception) -> bool:
    text = str(exc).strip()
    if isinstance(exc, OrchestrationPolicyError) and text in _RETRYABLE_START_REASONS:
        return True
    return any(reason in text for reason in _RETRYABLE_START_REASONS)


class RunnerFailoverService:
    """Recover active deployments whose Windows runner disappeared.

    The service deliberately lives outside the normal login/start/stop code
    path. Reconciler calls it in the background after runner heartbeat has gone
    stale. It fences the old slot and creates a fresh START_BOT through the
    regular scheduler, so multi-node routing and slot capacity rules stay in
    one place.
    """

    def __init__(
        self,
        repo: ControlPlaneRepository,
        *,
        deployment_manager: Optional[DeploymentManagerService] = None,
        command_router: Optional[CommandRouterService] = None,
    ) -> None:
        self._repo = repo
        self._deployment_manager = deployment_manager or DeploymentManagerService(repo)
        self._command_router = command_router or CommandRouterService(repo)

    async def recover_once(self, *, limit: Optional[int] = None) -> dict[str, int]:
        if not _is_enabled():
            return {"enabled": 0}

        grace_sec = _failover_grace_sec()
        candidates = self._repo.list_runner_offline_failover_candidates(
            stale_sec=grace_sec,
            waiting_retry_sec=_retry_wait_sec(),
            limit=limit or _batch_size(),
        )
        result = {
            "enabled": 1,
            "scanned": len(candidates),
            "claimed": 0,
            "started": 0,
            "waiting_capacity": 0,
            "failed": 0,
            "skipped": 0,
            "fenced_slots": 0,
            "fence_stop_queued": 0,
        }
        for candidate in candidates:
            try:
                outcome = await self._recover_candidate(candidate, grace_sec=grace_sec)
            except Exception as exc:
                result["failed"] += 1
                log.warning(
                    "Runner offline failover candidate failed deployment_id=%s account_id=%s error=%s",
                    candidate.get("id"),
                    candidate.get("account_id"),
                    exc,
                )
                continue
            for key in ("claimed", "started", "waiting_capacity", "failed", "skipped", "fenced_slots", "fence_stop_queued"):
                result[key] += int(outcome.get(key) or 0)
        return result

    async def _recover_candidate(self, candidate: dict[str, Any], *, grace_sec: int) -> dict[str, int]:
        deployment_id = _safe_int(candidate.get("id"))
        account_id = _safe_int(candidate.get("account_id"))
        user_id = _safe_int(candidate.get("user_id"))
        runner_id = str(candidate.get("runner_id") or "").strip()
        slot_id = str(candidate.get("slot_id") or "").strip()
        if deployment_id <= 0 or account_id <= 0 or user_id <= 0 or not runner_id or not slot_id:
            return {"skipped": 1}

        reason = "runner_offline_failover"
        claimed = self._repo.claim_runner_offline_failover_candidate(
            deployment_id=deployment_id,
            runner_id=runner_id,
            slot_id=slot_id,
            stale_sec=grace_sec,
            reason=reason,
        )
        if not claimed:
            return {"skipped": 1}

        outcome = {
            "claimed": 1,
            "started": 0,
            "waiting_capacity": 0,
            "failed": 0,
            "skipped": 0,
            "fenced_slots": 0,
            "fence_stop_queued": 0,
        }
        fence_token = f"runner-offline-failover:{deployment_id}:{uuid.uuid4().hex}"
        handoff = self._prepare_old_slot_handoff(
            runner_id=runner_id,
            slot_id=slot_id,
            reason=reason,
            deployment_id=deployment_id,
            account_id=account_id,
            fence_token=fence_token,
        )
        if handoff:
            outcome["fenced_slots"] = 1

        account = self._repo.get_account(account_id=account_id, user_id=user_id)
        start_extra = {
            "control_flow": "runner_offline_failover",
            "failover_token": fence_token,
            "failover_from_deployment_id": deployment_id,
            "failover_from_runner_id": runner_id,
            "failover_from_slot_id": slot_id,
            "previous_deployment_id": deployment_id,
            "previous_trace_id": candidate.get("trace_id"),
        }
        try:
            replacement = await self._deployment_manager.start_deployment(
                user_id=user_id,
                account=account or {},
                bot_name=str(candidate.get("bot_code") or candidate.get("bot_name") or "").strip(),
                bot_config_overrides=_dict_payload(candidate.get("config_json")),
                mode=str(candidate.get("mode") or "live"),
                start_payload_extra=start_extra,
            )
            command = replacement.get("command") if isinstance(replacement, dict) else {}
            if isinstance(command, dict) and str(command.get("delivery_status") or "").strip().lower() == "failed":
                raise OrchestrationPolicyError(str(command.get("drop_reason") or "start_dispatch_failed"))
        except Exception as exc:
            await self._mark_replacement_unavailable(
                deployment_id=deployment_id,
                account_id=account_id,
                error=exc,
                fence_token=fence_token,
            )
            if _retryable_start_error(exc):
                outcome["waiting_capacity"] = 1
            else:
                outcome["failed"] = 1
            return outcome

        new_deployment = replacement.get("deployment") if isinstance(replacement, dict) else {}
        new_deployment_id = _safe_int((new_deployment or {}).get("id"))
        new_runner_id = str((new_deployment or {}).get("runner_id") or "").strip()
        new_slot_id = str((new_deployment or {}).get("slot_id") or "").strip()
        self._repo.update_deployment_status(
            deployment_id=deployment_id,
            status=DeploymentStatus.FAILED.value,
            desired_state="stopped",
            is_active=False,
            health_status="runner_offline_failover_replaced",
            last_error=f"replaced_by_deployment:{new_deployment_id}",
            stopped=True,
        )
        self._audit(
            deployment_id=deployment_id,
            action="deployment.runner_offline_failover",
            payload={
                "deployment_id": deployment_id,
                "account_id": account_id,
                "old_runner_id": runner_id,
                "old_slot_id": slot_id,
                "replacement_deployment_id": new_deployment_id,
                "replacement_runner_id": new_runner_id,
                "replacement_slot_id": new_slot_id,
                "fence_token": fence_token,
            },
            result="replacement_started",
            trace_id=fence_token,
        )
        if new_deployment_id > 0:
            self._audit(
                deployment_id=new_deployment_id,
                action="deployment.runner_offline_failover_started",
                payload={
                    "previous_deployment_id": deployment_id,
                    "old_runner_id": runner_id,
                    "old_slot_id": slot_id,
                    "new_runner_id": new_runner_id,
                    "new_slot_id": new_slot_id,
                    "fence_token": fence_token,
                },
                result="start_dispatched",
                trace_id=fence_token,
            )
        if _fence_stop_enabled():
            queued = await self._dispatch_old_runner_fence_stop(
                deployment=claimed,
                runner_id=runner_id,
                slot_id=slot_id,
                fence_token=fence_token,
            )
            if queued:
                outcome["fence_stop_queued"] = 1
        outcome["started"] = 1
        return outcome

    def _prepare_old_slot_handoff(
        self,
        *,
        runner_id: str,
        slot_id: str,
        reason: str,
        deployment_id: int,
        account_id: int,
        fence_token: str,
    ) -> dict[str, Any]:
        try:
            handoff = self._repo.prepare_orphaned_slot_handoff(
                runner_id=runner_id,
                slot_id=slot_id,
                reason=reason,
                actor="control_plane_failover",
            )
        except Exception as exc:
            log.warning(
                "Runner offline failover could not fence old slot deployment_id=%s account_id=%s runner_id=%s slot_id=%s error=%s",
                deployment_id,
                account_id,
                runner_id,
                slot_id,
                exc,
            )
            return {}
        self._audit(
            deployment_id=deployment_id,
            action="deployment.runner_offline_slot_fenced",
            payload={
                "deployment_id": deployment_id,
                "account_id": account_id,
                "runner_id": runner_id,
                "slot_id": slot_id,
                "fence_token": fence_token,
                "handoff": handoff or {},
            },
            result="fenced" if handoff else "slot_missing",
            trace_id=fence_token,
        )
        return handoff or {}

    async def _mark_replacement_unavailable(
        self,
        *,
        deployment_id: int,
        account_id: int,
        error: Exception,
        fence_token: str,
    ) -> None:
        reason = str(error).strip() or error.__class__.__name__
        if _retryable_start_error(error):
            self._repo.update_deployment_status(
                deployment_id=deployment_id,
                status=DeploymentStatus.BLOCKED.value,
                desired_state="running",
                is_active=False,
                health_status="runner_offline_waiting_capacity",
                last_error=reason[:200],
            )
            result = "waiting_capacity"
        else:
            self._repo.update_deployment_status(
                deployment_id=deployment_id,
                status=DeploymentStatus.FAILED.value,
                desired_state="stopped",
                is_active=False,
                health_status="runner_offline_failover_failed",
                last_error=reason[:200],
                stopped=True,
            )
            result = "failed"
        self._audit(
            deployment_id=deployment_id,
            action="deployment.runner_offline_failover_unavailable",
            payload={
                "deployment_id": deployment_id,
                "account_id": account_id,
                "reason": reason[:200],
                "retryable": _retryable_start_error(error),
                "fence_token": fence_token,
            },
            result=result,
            trace_id=fence_token,
        )

    async def _dispatch_old_runner_fence_stop(
        self,
        *,
        deployment: dict[str, Any],
        runner_id: str,
        slot_id: str,
        fence_token: str,
    ) -> bool:
        account_id = _safe_int(deployment.get("account_id"))
        deployment_id = _safe_int(deployment.get("id"))
        if account_id <= 0 or deployment_id <= 0:
            return False
        try:
            await self._command_router.dispatch(
                command_type=CommandType.STOP_BOT,
                account_id=account_id,
                deployment_id=deployment_id,
                bot_id=str(deployment.get("bot_code") or ""),
                runner_id=runner_id,
                slot_id=slot_id,
                priority=120,
                payload={
                    "control_flow": "runner_offline_failover_fence",
                    "reason": "runner_offline_failover_fence",
                    "failover_token": fence_token,
                    "force": True,
                    "kill_mt5": True,
                    "terminate_mt5": True,
                    "cleanup_slot": True,
                },
                trace_id=f"{fence_token}:stop-old",
            )
            return True
        except Exception as exc:
            log.warning(
                "Runner offline failover could not queue fence STOP deployment_id=%s runner_id=%s slot_id=%s error=%s",
                deployment_id,
                runner_id,
                slot_id,
                exc,
            )
            return False

    def _audit(
        self,
        *,
        deployment_id: int,
        action: str,
        payload: dict[str, Any],
        result: str,
        trace_id: str,
    ) -> None:
        try:
            self._repo.insert_deployment_audit(
                deployment_id=deployment_id,
                action=action,
                payload=payload,
                result=result,
                trace_id=trace_id,
            )
        except Exception as exc:
            log.debug("Runner failover audit skipped deployment_id=%s action=%s error=%s", deployment_id, action, exc)
