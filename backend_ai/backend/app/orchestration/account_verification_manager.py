from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.infra.redis_streams import RedisStreamPublisher
from app.orchestration.scheduler import choose_slot_for_account, rank_slots_for_account
from app.repositories.control_plane_repository import ControlPlaneRepository
from app.risk.orchestration_policy import OrchestrationPolicyError, validate_no_active_deployment

_VERIFICATION_SCHEDULER_BOT = {"profile_class": "light", "strategy_tags": [], "resource_hints": {}}
_DEFAULT_DISPATCH_STALE_RETRY_SEC = 180


def _bypass_bot_start_runtime_checks(metadata: dict[str, Any]) -> dict[str, Any]:
    projected = dict(metadata)
    projected["available_for_new_account"] = True
    projected["start_eligible"] = True
    projected["can_start"] = True
    projected["allow_start"] = True
    projected["available_for_start"] = True
    projected["requires_ipc_ready_before_start"] = False
    projected["require_ipc_ready_for_start"] = False
    projected["ipc_ready"] = True
    projected["requires_resident_worker_certification"] = False
    projected["resident_worker_required"] = False
    projected["require_resident_worker_for_start"] = False
    projected["start_block_reason"] = ""
    return projected


def _dispatch_stale_retry_sec() -> int:
    raw = os.getenv("ACCOUNT_VERIFICATION_DISPATCH_STALE_RETRY_SEC", "").strip()
    try:
        value = int(raw or _DEFAULT_DISPATCH_STALE_RETRY_SEC)
    except (TypeError, ValueError):
        value = _DEFAULT_DISPATCH_STALE_RETRY_SEC
    return max(30, value)


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _verification_job_stale_for_retry(job: dict[str, Any]) -> bool:
    status = str(job.get("status") or "").strip().lower()
    if status != "dispatched":
        return False
    dt = (
        _parse_dt(job.get("updated_at"))
        or _parse_dt(job.get("dispatched_at"))
        or _parse_dt(job.get("requested_at"))
    )
    if dt is None:
        return False
    return dt <= datetime.now(timezone.utc) - timedelta(seconds=_dispatch_stale_retry_sec())


def _verification_lane_scheduler_slots(slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project runner slots for verification-lane dispatch.

    Windows owns the verification MT5 lane. Linux still sends runner_id/slot_id
    for backward-compatible correlation, but this must not consume or require a
    free bot slot.
    """
    projected: list[dict[str, Any]] = []
    hard_slot_states = {"broken", "degraded", "disabled", "offline"}
    for slot in slots:
        item = dict(slot or {})
        status = str(item.get("status") or "").strip().lower()
        if status not in hard_slot_states:
            item["status"] = "ready"
            item["current_account_id"] = None
        item.pop("sticky_account_id", None)
        item.pop("reserved_account_id", None)
        item = _bypass_bot_start_runtime_checks(item)
        metadata = item.get("metadata_json") or item.get("metadata")
        if isinstance(metadata, dict):
            metadata = dict(metadata)
            for key in (
                "sticky_account_id",
                "reserved_account_id",
                "verification_status",
                "verification_account_id",
            ):
                metadata.pop(key, None)
            metadata = _bypass_bot_start_runtime_checks(metadata)
            inventory = metadata.get("slot_inventory")
            if isinstance(inventory, list):
                metadata["slot_inventory"] = [
                    _bypass_bot_start_runtime_checks(entry) if isinstance(entry, dict) else entry
                    for entry in inventory
                ]
            inventory_entry = metadata.get("slot_inventory_entry")
            if isinstance(inventory_entry, dict):
                metadata["slot_inventory_entry"] = _bypass_bot_start_runtime_checks(inventory_entry)
            item["metadata_json"] = metadata
        projected.append(item)
    return projected


class AccountVerificationManagerService:
    def __init__(
        self,
        repo: ControlPlaneRepository,
        *,
        publisher: Optional[RedisStreamPublisher] = None,
    ) -> None:
        self._repo = repo
        self._publisher = publisher or RedisStreamPublisher()

    def _find_mt5_identity_conflict(self, *, user_id: int, account: dict[str, Any]) -> dict[str, Any] | None:
        finder = getattr(self._repo, "find_mt5_account_identity_conflict", None)
        if not callable(finder):
            return None
        return finder(
            user_id=int(user_id),
            broker=str(account.get("broker") or ""),
            server=str(account.get("server") or ""),
            login=str(account.get("login") or ""),
            exclude_account_id=int(account["id"]),
        )

    async def _publish_and_mark_dispatched(
        self,
        *,
        job: dict[str, Any],
        account: dict[str, Any],
        trace_id: str,
    ) -> dict[str, Any]:
        resolved_runner_id = str(job.get("runner_id") or "").strip()
        resolved_slot_id = str(job.get("slot_id") or "").strip()
        if not resolved_runner_id or not resolved_slot_id:
            raise OrchestrationPolicyError("no_healthy_slot_available")
        payload_for_runner = {
            "mode": "verify_account",
            "broker": account.get("broker"),
            "server": account.get("server"),
            "login": account.get("login"),
            **dict(job.get("payload_json") or {}),
            "account_id": int(account["id"]),
            "runner_id": resolved_runner_id,
            "slot_id": resolved_slot_id,
        }
        envelope = {
            "job_id": int(job["id"]),
            "account_id": int(account["id"]),
            "runner_id": resolved_runner_id,
            "slot_id": resolved_slot_id,
            "trace_id": trace_id,
            "payload": payload_for_runner,
        }
        stream_id = await self._publisher.publish_account_verification(envelope)
        dispatched = self._repo.mark_account_verification_dispatched(
            job_id=int(job["id"]),
            runner_id=resolved_runner_id,
            slot_id=resolved_slot_id,
            redis_stream_id=stream_id,
        )
        return dispatched or job

    async def _repair_active_job_if_publish_missing(self, *, active_job: dict[str, Any], account: dict[str, Any]) -> dict[str, Any]:
        status = str(active_job.get("status") or "").strip().lower()
        redis_stream_id = str(active_job.get("redis_stream_id") or "").strip()
        if status == "pending" and not redis_stream_id:
            trace_id = str(active_job.get("trace_id") or uuid.uuid4().hex).strip()
            return await self._publish_and_mark_dispatched(
                job=active_job,
                account=account,
                trace_id=trace_id,
            )
        return active_job

    async def request_verification(self, *, user_id: int, account: dict[str, Any]) -> dict[str, Any]:
        if not account:
            raise OrchestrationPolicyError("account_not_found")

        active_deployment = self._repo.get_active_deployment_for_account(account_id=int(account["id"]))
        validate_no_active_deployment(active_deployment)

        active_job = self._repo.get_active_account_verification_job(account_id=int(account["id"]))
        if active_job:
            if _verification_job_stale_for_retry(active_job):
                canceller = getattr(self._repo, "cancel_account_verification_job", None)
                if callable(canceller):
                    outcome = canceller(
                        job_id=int(active_job["id"]),
                        user_id=int(user_id),
                        reason="verification_callback_timeout",
                    )
                    if str((outcome or {}).get("status") or "").strip().lower() != "cancelled":
                        return await self._repair_active_job_if_publish_missing(active_job=active_job, account=account)
                else:
                    return await self._repair_active_job_if_publish_missing(active_job=active_job, account=account)
            else:
                return await self._repair_active_job_if_publish_missing(active_job=active_job, account=account)

        identity_conflict = self._find_mt5_identity_conflict(user_id=user_id, account=account)
        if identity_conflict:
            if int(identity_conflict.get("user_id") or 0) == int(user_id):
                raise OrchestrationPolicyError("mt5_account_already_added")
            raise OrchestrationPolicyError("mt5_account_already_used")

        slots = self._repo.list_slots()
        verification_slots = _verification_lane_scheduler_slots(slots)
        candidates = rank_slots_for_account(
            account_id=int(account["id"]),
            bot=_VERIFICATION_SCHEDULER_BOT,
            slots=verification_slots,
            sticky_binding=None,
        )
        if not candidates:
            decision = choose_slot_for_account(
                account_id=int(account["id"]),
                bot=_VERIFICATION_SCHEDULER_BOT,
                slots=verification_slots,
                sticky_binding=None,
            )
            raise OrchestrationPolicyError(decision.reason or "no_healthy_slot_available")

        trace_id = uuid.uuid4().hex
        job_payload = {
            "mode": "verify_account",
            "account_id": int(account["id"]),
            "broker": account.get("broker"),
            "server": account.get("server"),
            "login": account.get("login"),
        }
        try:
            job = self._repo.create_account_verification_job(
                user_id=user_id,
                account_id=int(account["id"]),
                payload=job_payload,
                trace_id=trace_id,
                slot_candidates=[
                    {
                        "runner_id": candidate.runner_id,
                        "slot_id": candidate.slot_id,
                        "reason": candidate.reason,
                        "sticky_reused": candidate.sticky_reused,
                    }
                    for candidate in candidates
                    if candidate.ok
                ],
            )
            if str(job.get("trace_id") or "").strip() != trace_id:
                return job
            return await self._publish_and_mark_dispatched(
                job=job,
                account=account,
                trace_id=trace_id,
            )
        except Exception:
            raise

    def complete_verification(
        self,
        *,
        job_id: int,
        ok: bool,
        error_text: Optional[str],
        runner_id: Optional[str],
        slot_id: Optional[str],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        result = self._repo.complete_account_verification_job(
            job_id=job_id,
            ok=ok,
            error_text=error_text,
            runner_id=runner_id,
            slot_id=slot_id,
            payload=payload,
        )
        if not result:
            raise OrchestrationPolicyError("verification_job_not_found")
        return result

    async def cancel_all_verifications_for_account(
        self,
        *,
        user_id: int,
        account_id: int,
        reason: Optional[str] = None,
    ) -> dict[str, Any]:
        """Bulk-cancel moi verification job dang pending/dispatched cua 1 account.

        Dung khi user co backlog kep (vi du 14 job kep cua user 27).
        Re-use cancel_verification cho tung job de giu nguyen logic free slot + signal.

        Tra ve:
          {
            "account_id": int,
            "requested_at": int,
            "cancelled": list[{job_id, status, signal_emitted}],
            "skipped": list[{job_id, reason}],
            "cancelled_count": int,
            "signal_emitted_count": int,
            "scanned_count": int,
          }
        """
        try:
            ids = self._repo.list_active_verification_job_ids_for_account(
                account_id=int(account_id),
                user_id=int(user_id),
            )
        except Exception:
            ids = []

        cancelled: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        signal_count = 0
        for job_id in ids:
            try:
                result = await self.cancel_verification(
                    user_id=int(user_id),
                    job_id=int(job_id),
                    reason=reason,
                )
            except OrchestrationPolicyError as exc:
                skipped.append({"job_id": int(job_id), "reason": str(exc)})
                continue
            except Exception as exc:
                skipped.append({"job_id": int(job_id), "reason": f"unexpected:{exc.__class__.__name__}"})
                continue
            signal_emitted = bool(result.get("cancel_signal_emitted"))
            if signal_emitted:
                signal_count += 1
            cancelled.append(
                {
                    "job_id": int(job_id),
                    "status": str(result.get("status") or "cancelled"),
                    "previous_status": result.get("cancel_outcome"),
                    "signal_emitted": signal_emitted,
                }
            )

        return {
            "account_id": int(account_id),
            "scanned_count": len(ids),
            "cancelled_count": len(cancelled),
            "signal_emitted_count": signal_count,
            "cancelled": cancelled,
            "skipped": skipped,
        }

    async def cancel_verification(
        self,
        *,
        user_id: int,
        job_id: int,
        reason: Optional[str] = None,
    ) -> dict[str, Any]:
        """Huy 1 verification job theo yeu cau cua user.

        - Validate ownership (user_id) o repository layer
        - Update DB: status='cancelled', free slot binding
        - Best-effort: phat signal cancel vao Redis cho runner skip som
        - Idempotent: neu job da terminal (verified/failed/cancelled), raise OrchestrationPolicyError
          ('verification_already_completed' khi da verified/failed; tra ve job khi da cancelled)
        """
        outcome = self._repo.cancel_account_verification_job(
            job_id=int(job_id),
            user_id=int(user_id),
            reason=reason,
        )
        outcome_status = str(outcome.get("status") or "").strip().lower()
        if outcome_status == "not_found":
            raise OrchestrationPolicyError("verification_job_not_found")
        job = outcome.get("job") or {}
        previous_status = str(outcome.get("previous_status") or "").strip().lower()
        if outcome_status == "already_completed" and previous_status in {"verified", "failed"}:
            raise OrchestrationPolicyError("verification_already_completed")

        cancel_signal_emitted = False
        if outcome_status == "cancelled" and job:
            try:
                cancel_signal_emitted = await self._publisher.publish_account_verification_cancel(
                    {
                        "job_id": int(job["id"]),
                        "account_id": int(job.get("account_id") or 0),
                        "runner_id": str(job.get("runner_id") or ""),
                        "slot_id": str(job.get("slot_id") or ""),
                        "trace_id": str(job.get("trace_id") or ""),
                        "reason": reason or "cancelled_by_user",
                    }
                )
            except Exception:
                cancel_signal_emitted = False

        result = dict(job)
        result["cancel_signal_emitted"] = cancel_signal_emitted
        result["cancel_outcome"] = outcome_status
        return result
