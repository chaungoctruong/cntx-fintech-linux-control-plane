from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from app.orchestration.runner_failover import RunnerFailoverService
from app.monitoring.runtime_housekeeping import RuntimeHousekeepingService
from app.repositories.control_plane_repository import ControlPlaneRepository
from app.services.store_service import get_process_store
from app.settings import settings

log = logging.getLogger("control_plane_reconciler")


def _has_stale_runtime(result: dict[str, int]) -> bool:
    return any(
        int(result.get(key) or 0) > 0
        for key in (
            "stale_runners",
            "stale_deployments",
            "stale_accounts",
            "reconciled_stop_requested_deployments",
            "failed_stale_start_commands",
            "failed_acknowledged_start_commands",
            "acknowledged_stale_stop_commands",
            "reconciled_orphan_allocated_slots",
            "reconciled_active_zero_runtime_deployments",
            "failed_zero_runtime_start_commands",
            "acknowledged_zero_runtime_stop_commands",
            "expired_login_reservations",
            "runner_failover_claimed",
            "runner_failover_started",
            "runner_failover_waiting_capacity",
            "runner_failover_failed",
        )
    )


def _sticky_midnight_release_enabled() -> bool:
    return bool(getattr(settings, "STICKY_SLOT_MIDNIGHT_RELEASE_ENABLED", True))


class ControlPlaneReconcilerService:
    def __init__(self, repo: Optional[ControlPlaneRepository] = None) -> None:
        self._repo = repo or ControlPlaneRepository(get_process_store())
        self._runner_failover = RunnerFailoverService(self._repo)
        self._housekeeping = RuntimeHousekeepingService(self._repo)
        self._run_count = 0
        self._last_started_at = 0
        self._last_success_at = 0
        self._last_error: str | None = None
        self._last_result: dict[str, int] = {}
        self._last_housekeeping_at = 0

    def snapshot(self) -> dict[str, Any]:
        return {
            "run_count": int(self._run_count),
            "last_started_at": int(self._last_started_at),
            "last_success_at": int(self._last_success_at),
            "last_error": self._last_error,
            "last_result": dict(self._last_result),
        }

    def reconcile_once(self) -> dict[str, int]:
        self._last_started_at = int(time.time())
        try:
            result = self._repo.reconcile_runtime_health(
                runner_stale_sec=int(getattr(settings, "CONTROL_PLANE_RUNNER_STALE_SEC", 180) or 180),
                deployment_stale_sec=int(getattr(settings, "CONTROL_PLANE_DEPLOYMENT_STALE_SEC", 180) or 180),
                stop_reconcile_sec=int(getattr(settings, "CONTROL_PLANE_STOP_RECONCILE_SEC", 30) or 30),
                slot_projection_lookback_sec=int(
                    getattr(settings, "RUNNER_SLOT_PROJECTION_EVENT_LOOKBACK_SEC", 21600) or 21600
                ),
            )
            if hasattr(self._repo, "reconcile_start_bootstrap_failures"):
                bootstrap_result = self._repo.reconcile_start_bootstrap_failures()
                result = {**dict(result), **dict(bootstrap_result or {})}
            if hasattr(self._repo, "release_expired_login_reservations"):
                expired_login_reservations = self._repo.release_expired_login_reservations()
                result = {**dict(result), "expired_login_reservations": int(expired_login_reservations or 0)}
            if _sticky_midnight_release_enabled() and hasattr(self._repo, "release_expired_sticky_slot_bindings"):
                release_result = self._repo.release_expired_sticky_slot_bindings(
                    timezone_name=str(
                        getattr(settings, "STICKY_SLOT_MIDNIGHT_RELEASE_TIMEZONE", "Asia/Ho_Chi_Minh")
                        or "Asia/Ho_Chi_Minh"
                    ),
                    batch_size=int(getattr(settings, "STICKY_SLOT_MIDNIGHT_RELEASE_BATCH_SIZE", 500) or 500),
                )
                result = {**dict(result), **dict(release_result or {})}
                if int(result.get("expired_sticky_bindings") or 0) > 0:
                    log.info("Released expired sticky slot bindings: %s", release_result)
            if hasattr(self._repo, "fail_stale_config_restart_commands"):
                failed_config_restarts = self._repo.fail_stale_config_restart_commands(
                    timeout_sec=int(getattr(settings, "CONFIG_RESTART_COMMAND_TIMEOUT_SEC", 180) or 180),
                )
                result = {**dict(result), "failed_config_restart_commands": int(failed_config_restarts or 0)}
            if hasattr(self._repo, "fail_stale_config_hot_update_commands"):
                failed_config_hot_updates = self._repo.fail_stale_config_hot_update_commands(
                    timeout_sec=int(getattr(settings, "CONFIG_HOT_UPDATE_COMMAND_TIMEOUT_SEC", 180) or 180),
                )
                result = {
                    **dict(result),
                    "failed_config_hot_update_commands": int(failed_config_hot_updates or 0),
                }
            if hasattr(self._repo, "fail_stale_acknowledged_start_commands"):
                failed_acknowledged_starts = self._repo.fail_stale_acknowledged_start_commands(
                    timeout_sec=int(getattr(settings, "START_BOT_ACK_TIMEOUT_SEC", 120) or 120),
                    reason="start_bot_ack_timeout_no_runtime_event",
                )
                result = {
                    **dict(result),
                    "failed_acknowledged_start_commands": int(failed_acknowledged_starts or 0),
                }
        except Exception as exc:
            self._last_error = str(exc)
            raise

        self._run_count += 1
        self._last_success_at = int(time.time())
        self._last_error = None
        self._last_result = dict(result)
        return result

    async def reconcile_once_async(self) -> dict[str, int]:
        result = await asyncio.to_thread(self.reconcile_once)
        try:
            failover = await self._runner_failover.recover_once()
        except Exception as exc:
            self._last_error = str(exc)
            log.warning("Runner offline failover iteration failed: %s", exc)
            failover = {"enabled": 1, "scanned": 0, "claimed": 0, "started": 0, "waiting_capacity": 0, "failed": 1}
        if failover:
            failover_result = {f"runner_failover_{key}": int(value or 0) for key, value in failover.items()}
            result = {**dict(result), **failover_result}
            self._last_result = dict(result)
        if bool(getattr(settings, "RUNTIME_HOUSEKEEPING_ENABLED", True)):
            now = int(time.time())
            interval = max(60, int(getattr(settings, "RUNTIME_HOUSEKEEPING_INTERVAL_SEC", 3600) or 3600))
            if now - int(self._last_housekeeping_at or 0) >= interval:
                self._last_housekeeping_at = now
                try:
                    housekeeping = await self._housekeeping.run_once()
                    housekeeping_result = {
                        f"housekeeping_{key}": int(value or 0)
                        for key, value in housekeeping.items()
                        if isinstance(value, (int, bool))
                    }
                    result = {**dict(result), **housekeeping_result}
                    self._last_result = dict(result)
                except Exception as exc:
                    self._last_error = str(exc)
                    log.warning("Runtime housekeeping iteration failed: %s", exc)
        return result

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        interval = max(10, int(getattr(settings, "CONTROL_PLANE_RECONCILE_INTERVAL_SEC", 30) or 30))
        log.info(
            "Control plane reconciler started interval=%ss runner_stale=%ss deployment_stale=%ss",
            interval,
            int(getattr(settings, "CONTROL_PLANE_RUNNER_STALE_SEC", 180) or 180),
            int(getattr(settings, "CONTROL_PLANE_DEPLOYMENT_STALE_SEC", 180) or 180),
        )
        while not stop_event.is_set():
            try:
                result = await self.reconcile_once_async()
                if _has_stale_runtime(result):
                    log.warning("Control plane reconciler detected stale runtime: %s", result)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = str(exc)
                log.warning("Control plane reconciler iteration failed: %s", exc)

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue
        log.info("Control plane reconciler stopped")
