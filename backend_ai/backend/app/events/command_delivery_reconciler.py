from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from app.infra.redis_streams import RedisStreamPublisher
from app.repositories.control_plane_repository import ControlPlaneRepository
from app.runner.protocol import build_runner_command_from_row
from app.services.store_service import get_process_store
from app.settings import settings
from ops_telegram_alerts import schedule_error_alert

log = logging.getLogger("command_delivery_reconciler")

_REPLAY_COMMAND_TYPES = ("START_BOT", "STOP_BOT", "UPDATE_BOT_CONFIG")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_error_label(exc: BaseException) -> str:
    return f"command_replay_failed:{exc.__class__.__name__}"[:200]


class CommandDeliveryReconcilerService:
    def __init__(
        self,
        repo: Optional[ControlPlaneRepository] = None,
        *,
        publisher: Optional[RedisStreamPublisher] = None,
    ) -> None:
        self._repo = repo or ControlPlaneRepository(get_process_store())
        self._publisher = publisher or RedisStreamPublisher()
        self._run_count = 0
        self._last_started_at = 0
        self._last_success_at = 0
        self._last_error: str | None = None
        self._last_error_at = 0
        self._last_error_class: str | None = None
        self._last_result: dict[str, int] = {}

    def snapshot(self) -> dict[str, Any]:
        return {
            "run_count": int(self._run_count),
            "last_started_at": int(self._last_started_at),
            "last_success_at": int(self._last_success_at),
            "last_error": self._last_error,
            "last_error_at": int(self._last_error_at),
            "last_error_class": self._last_error_class,
            "last_result": dict(self._last_result),
        }

    async def reconcile_once(self) -> dict[str, int]:
        self._last_started_at = int(time.time())
        result = {
            "replay_attempted": 0,
            "replay_success": 0,
            "replay_skipped_duplicate": 0,
            "replay_failed": 0,
            "replay_lock_skipped": 0,
            "processing_requeue_checked": 0,
            "processing_requeue_success": 0,
            "processing_requeue_not_found": 0,
            "processing_requeue_failed": 0,
            "http_claim_requeued": 0,
            "http_claim_requeue_error": 0,
            "terminal_failed_start": 0,
            "terminal_acknowledged_stop": 0,
            "stale_queued_start_checked": 0,
            "stale_queued_start_failed": 0,
            "stale_queued_start_error": 0,
        }
        lock_handle: Any | None = None
        acquire_lock = getattr(self._repo, "try_acquire_command_delivery_replay_lock", None)
        release_lock = getattr(self._repo, "release_command_delivery_replay_lock", None)
        try:
            if callable(acquire_lock):
                lock_handle = acquire_lock()
                if lock_handle is None:
                    result["replay_lock_skipped"] = 1
                    self._last_result = dict(result)
                    self._last_success_at = int(time.time())
                    self._last_error = None
                    self._run_count += 1
                    return result

            terminal_reconciler = getattr(self._repo, "reconcile_terminal_bot_control_commands", None)
            if callable(terminal_reconciler):
                terminal_result = terminal_reconciler()
                result["terminal_failed_start"] = int(terminal_result.get("failed_start_commands") or 0)
                result["terminal_acknowledged_stop"] = int(terminal_result.get("acknowledged_stop_commands") or 0)

            rows = self._repo.list_replayable_execution_commands(
                limit=max(1, int(getattr(settings, "COMMAND_DELIVERY_REPLAY_BATCH_SIZE", 100) or 100)),
                statuses=["pending", "queued"],
                command_types=list(_REPLAY_COMMAND_TYPES),
                require_missing_stream=True,
                older_than_sec=max(0, int(getattr(settings, "COMMAND_DELIVERY_REPLAY_OLDER_THAN_SEC", 10) or 10)),
            )
            for row in rows:
                command_id = str(row.get("command_id") or "").strip()
                result["replay_attempted"] += 1
                try:
                    envelope = build_runner_command_from_row(row)
                    publish_result = await self._publisher.publish_command_result(envelope.model_dump(mode="json"))
                    stream_id = str(publish_result.get("stream_id") or "").strip()
                    if not stream_id:
                        raise RuntimeError("redis_stream_id_missing")
                    self._repo.mark_command_delivery(
                        command_id=envelope.command_id,
                        status="queued",
                        redis_stream_id=stream_id,
                    )
                    if bool(publish_result.get("duplicate")):
                        result["replay_skipped_duplicate"] += 1
                    else:
                        result["replay_success"] += 1
                except Exception as exc:
                    result["replay_failed"] += 1
                    self._last_error_at = int(time.time())
                    self._last_error_class = exc.__class__.__name__
                    if command_id:
                        marker = getattr(self._repo, "mark_command_replay_failure", None)
                        if callable(marker):
                            marker(command_id=command_id, error_text=_safe_error_label(exc))
                    log.warning(
                        "command_delivery_replay_failed command_id=%s command_type=%s runner_id=%s slot_id=%s error=%s",
                        command_id or "-",
                        str(row.get("command_type") or "").strip() or "-",
                        str(row.get("runner_id") or "").strip() or "-",
                        str(row.get("slot_id") or "").strip() or "-",
                        exc.__class__.__name__,
                    )
                    schedule_error_alert(
                        area="Lệnh runner",
                        summary="Backend không gửi lại được lệnh xuống Windows runner.",
                        exc=exc,
                        runner_id=str(row.get("runner_id") or "").strip() or None,
                        slot_id=str(row.get("slot_id") or "").strip() or None,
                        account_id=row.get("account_id"),
                        deployment_id=row.get("deployment_id"),
                        impact="Một lệnh bật/tắt/cập nhật bot có thể bị chậm hoặc chưa tới runner.",
                        action="Kiểm tra Redis queue, runner online và command delivery backlog.",
                        detail={
                            "command_id": command_id or "-",
                            "command_type": str(row.get("command_type") or "").strip() or "-",
                        },
                        alert_key=(
                            "command_replay_failed:"
                            f"{str(row.get('command_type') or '').strip()}:"
                            f"{str(row.get('runner_id') or '').strip()}:"
                            f"{exc.__class__.__name__}"
                        ),
                        cooldown_sec=180,
                    )

            if bool(getattr(settings, "COMMAND_DELIVERY_PROCESSING_REQUEUE_ENABLED", True)):
                lister = getattr(self._repo, "list_stale_processing_execution_commands", None)
                marker = getattr(self._repo, "mark_command_processing_requeued", None)
                requeue = getattr(self._publisher, "requeue_runner_command_from_processing", None)
                if callable(lister) and callable(requeue):
                    stale_rows = lister(
                        limit=max(
                            1,
                            int(getattr(settings, "COMMAND_DELIVERY_PROCESSING_REQUEUE_BATCH_SIZE", 100) or 100),
                        ),
                        statuses=["queued", "dispatched"],
                        command_types=list(_REPLAY_COMMAND_TYPES),
                        older_than_sec=max(
                            1,
                            int(getattr(settings, "COMMAND_DELIVERY_PROCESSING_REQUEUE_TIMEOUT_SEC", 180) or 180),
                        ),
                    )
                    for row in stale_rows:
                        command_id = str(row.get("command_id") or "").strip()
                        runner_id = str(row.get("runner_id") or "").strip()
                        if not command_id or not runner_id:
                            continue
                        result["processing_requeue_checked"] += 1
                        try:
                            requeue_result = await requeue(
                                runner_id=runner_id,
                                command_id=command_id,
                                max_items=max(
                                    1,
                                    int(
                                        getattr(
                                            settings,
                                            "COMMAND_DELIVERY_PROCESSING_REQUEUE_SCAN_LIMIT",
                                            500,
                                        )
                                        or 500
                                    ),
                                ),
                            )
                            if bool(requeue_result.get("requeued")):
                                if callable(marker):
                                    marker(
                                        command_id=command_id,
                                        reason="runner_processing_requeued_after_timeout",
                                    )
                                result["processing_requeue_success"] += 1
                            else:
                                result["processing_requeue_not_found"] += 1
                        except Exception as exc:
                            result["processing_requeue_failed"] += 1
                            self._last_error_at = int(time.time())
                            self._last_error_class = exc.__class__.__name__
                            log.warning(
                                "command_processing_requeue_failed command_id=%s command_type=%s runner_id=%s error=%s",
                                command_id or "-",
                                str(row.get("command_type") or "").strip() or "-",
                                runner_id or "-",
                                exc.__class__.__name__,
                            )
                            schedule_error_alert(
                                area="Lệnh runner",
                                summary="Backend không khôi phục được lệnh đang kẹt ở runner.",
                                exc=exc,
                                runner_id=runner_id or None,
                                account_id=row.get("account_id"),
                                deployment_id=row.get("deployment_id"),
                                impact="Một lệnh runner có thể đang bị treo ở trạng thái xử lý.",
                                action="Kiểm tra Redis processing queue và trạng thái Windows runner.",
                                detail={
                                    "command_id": command_id or "-",
                                    "command_type": str(row.get("command_type") or "").strip() or "-",
                                },
                                alert_key=(
                                    "command_processing_requeue_failed:"
                                    f"{str(row.get('command_type') or '').strip()}:"
                                    f"{runner_id}:{exc.__class__.__name__}"
                                ),
                                cooldown_sec=180,
                            )

            http_claim_requeue = getattr(self._repo, "requeue_stale_http_claimed_execution_commands", None)
            if callable(http_claim_requeue):
                try:
                    result["http_claim_requeued"] = int(
                        http_claim_requeue(
                            limit=max(
                                1,
                                int(getattr(settings, "COMMAND_DELIVERY_PROCESSING_REQUEUE_BATCH_SIZE", 100) or 100),
                            ),
                            older_than_sec=max(
                                30,
                                int(getattr(settings, "COMMAND_DELIVERY_PROCESSING_REQUEUE_TIMEOUT_SEC", 180) or 180),
                            ),
                            command_types=list(_REPLAY_COMMAND_TYPES),
                        )
                        or 0
                    )
                except Exception as exc:
                    result["http_claim_requeue_error"] += 1
                    self._last_error_at = int(time.time())
                    self._last_error_class = exc.__class__.__name__
                    log.warning("http_claim_requeue_failed error=%s", exc.__class__.__name__)
                    schedule_error_alert(
                        area="Lệnh runner",
                        summary="Backend không thu hồi được lệnh HTTP-claim bị quá hạn.",
                        exc=exc,
                        impact="Một lệnh runner có thể kẹt ở trạng thái dispatched.",
                        action="Kiểm tra execution_commands và command_delivery_reconciler.",
                        alert_key=f"http_claim_requeue_failed:{exc.__class__.__name__}",
                        cooldown_sec=180,
                    )

            queued_start_lister = getattr(self._repo, "list_stale_queued_start_commands", None)
            queued_start_remover = getattr(self._publisher, "remove_runner_command", None)
            if callable(queued_start_lister):
                stale_start_rows = queued_start_lister(
                    limit=max(1, int(getattr(settings, "COMMAND_DELIVERY_PROCESSING_REQUEUE_BATCH_SIZE", 100) or 100)),
                    older_than_sec=max(
                        10,
                        int(getattr(settings, "COMMAND_DELIVERY_START_QUEUE_TIMEOUT_SEC", 60) or 60),
                    ),
                )
                for row in stale_start_rows:
                    command_id = str(row.get("command_id") or "").strip()
                    runner_id = str(row.get("runner_id") or "").strip()
                    deployment_id = _safe_int(row.get("deployment_id"))
                    if not command_id or deployment_id <= 0:
                        continue
                    result["stale_queued_start_checked"] += 1
                    reason = "start_command_queue_timeout"
                    try:
                        remove_result: dict[str, Any] = {}
                        if callable(queued_start_remover) and runner_id:
                            remove_result = await queued_start_remover(
                                runner_id=runner_id,
                                command_id=command_id,
                            )
                        self._repo.update_execution_command_delivery(
                            command_id=command_id,
                            status="failed",
                            error_text=reason,
                            payload={
                                "last_event_type": "COMMAND_DELIVERY_TIMEOUT",
                                "failure_reason": reason,
                                "redis_removed": int(remove_result.get("removed") or 0),
                            },
                        )
                        self._repo.update_deployment_status(
                            deployment_id=deployment_id,
                            status="failed",
                            desired_state="stopped",
                            is_active=False,
                            health_status="command_delivery_timeout",
                            last_error=reason,
                            stopped=True,
                            runner_id=runner_id or None,
                            slot_id=str(row.get("slot_id") or "").strip() or None,
                        )
                        self._repo.release_deployment_slot(deployment_id=deployment_id, keep_sticky=True)
                        result["stale_queued_start_failed"] += 1
                    except Exception as exc:
                        result["stale_queued_start_error"] += 1
                        self._last_error_at = int(time.time())
                        self._last_error_class = exc.__class__.__name__
                        log.warning(
                            "stale_queued_start_reconcile_failed command_id=%s runner_id=%s deployment_id=%s error=%s",
                            command_id or "-",
                            runner_id or "-",
                            deployment_id,
                            exc.__class__.__name__,
                        )
        except Exception as exc:
            self._last_error = _safe_error_label(exc)
            self._last_error_at = int(time.time())
            self._last_error_class = exc.__class__.__name__
            raise
        finally:
            if lock_handle is not None and callable(release_lock):
                try:
                    release_lock(lock_handle)
                except Exception as exc:
                    log.warning("command_delivery_replay_lock_release_failed error=%s", exc.__class__.__name__)

        self._run_count += 1
        self._last_success_at = int(time.time())
        self._last_error = None
        counter = getattr(self._repo, "count_command_delivery_replay_backlog", None)
        if callable(counter):
            try:
                result["backlog_count"] = int(counter())
            except Exception as exc:
                self._last_error_at = int(time.time())
                self._last_error_class = exc.__class__.__name__
                result["backlog_count"] = -1
        self._last_result = dict(result)
        if any(
            int(result.get(key) or 0) > 0
            for key in (
                "replay_attempted",
                "replay_failed",
                "replay_skipped_duplicate",
                "processing_requeue_checked",
                "processing_requeue_failed",
                "http_claim_requeued",
                "http_claim_requeue_error",
                "terminal_failed_start",
                "terminal_acknowledged_stop",
                "stale_queued_start_checked",
                "stale_queued_start_error",
            )
        ):
            log.info(
                "command_delivery_replay replay_attempted=%d replay_success=%d replay_skipped_duplicate=%d replay_failed=%d processing_requeue_checked=%d processing_requeue_success=%d processing_requeue_not_found=%d processing_requeue_failed=%d http_claim_requeued=%d http_claim_requeue_error=%d terminal_failed_start=%d terminal_acknowledged_stop=%d stale_queued_start_checked=%d stale_queued_start_failed=%d stale_queued_start_error=%d",
                result["replay_attempted"],
                result["replay_success"],
                result["replay_skipped_duplicate"],
                result["replay_failed"],
                result["processing_requeue_checked"],
                result["processing_requeue_success"],
                result["processing_requeue_not_found"],
                result["processing_requeue_failed"],
                result["http_claim_requeued"],
                result["http_claim_requeue_error"],
                result["terminal_failed_start"],
                result["terminal_acknowledged_stop"],
                result["stale_queued_start_checked"],
                result["stale_queued_start_failed"],
                result["stale_queued_start_error"],
            )
        return result

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        interval = max(5, int(getattr(settings, "COMMAND_DELIVERY_REPLAY_INTERVAL_SEC", 15) or 15))
        log.info(
            "Command delivery reconciler started interval=%ss older_than=%ss batch=%s",
            interval,
            int(getattr(settings, "COMMAND_DELIVERY_REPLAY_OLDER_THAN_SEC", 10) or 10),
            int(getattr(settings, "COMMAND_DELIVERY_REPLAY_BATCH_SIZE", 100) or 100),
        )
        while not stop_event.is_set():
            try:
                await self.reconcile_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = _safe_error_label(exc)
                self._last_error_at = int(time.time())
                self._last_error_class = exc.__class__.__name__
                log.warning("command_delivery_reconciler_iteration_failed error=%s", exc.__class__.__name__)
                schedule_error_alert(
                    area="Lệnh runner",
                    summary="Vòng kiểm tra lệnh runner bị lỗi.",
                    exc=exc,
                    impact="Backend có thể không tự khôi phục lệnh bị kẹt.",
                    action="Kiểm tra log command_delivery_reconciler và Redis.",
                    alert_key=f"command_delivery_reconciler_iteration:{exc.__class__.__name__}",
                    cooldown_sec=300,
                )

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue
        log.info("Command delivery reconciler stopped")
