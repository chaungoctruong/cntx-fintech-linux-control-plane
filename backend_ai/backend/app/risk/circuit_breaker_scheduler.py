"""Background scheduler cho daily-loss circuit breaker.

Theo cung pattern voi `ControlPlaneReconcilerService`:
- Tick interval mac dinh 60s (env: CIRCUIT_BREAKER_TICK_SEC)
- Moi tick: list account opt-in policy, evaluate tung account, log result
- Idempotent: AccountRiskPolicyService.evaluate_circuit_breaker da safe goi nhieu lan

Tach module rieng de:
- Khong lam to ControlPlaneReconcilerService (separation of concerns)
- De disable rieng qua env CIRCUIT_BREAKER_SCHEDULER_ENABLED
- De test rieng
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from app.repositories.control_plane_repository import ControlPlaneRepository
from app.risk.account_risk_policy_service import AccountRiskPolicyService
from app.services.store_service import get_process_store
from app.settings import settings

log = logging.getLogger("circuit_breaker_scheduler")


class CircuitBreakerSchedulerService:
    def __init__(
        self,
        repo: Optional[ControlPlaneRepository] = None,
        *,
        risk_service: Optional[AccountRiskPolicyService] = None,
    ) -> None:
        self._repo = repo or ControlPlaneRepository(get_process_store())
        self._risk_service = risk_service or AccountRiskPolicyService(self._repo)
        self._run_count = 0
        self._last_started_at = 0
        self._last_success_at = 0
        self._last_error: str | None = None
        self._last_result: dict[str, Any] = {}

    def snapshot(self) -> dict[str, Any]:
        return {
            "run_count": int(self._run_count),
            "last_started_at": int(self._last_started_at),
            "last_success_at": int(self._last_success_at),
            "last_error": self._last_error,
            "last_result": dict(self._last_result),
        }

    async def evaluate_once(self) -> dict[str, Any]:
        self._last_started_at = int(time.time())
        try:
            accounts = await asyncio.to_thread(self._repo.list_accounts_with_active_circuit_breaker)
        except Exception as exc:
            self._last_error = f"list_accounts_failed:{exc.__class__.__name__}"
            log.warning("circuit_breaker_scheduler list_accounts failed: %s", exc)
            raise

        evaluated = 0
        triggered = 0
        deployments_stopped_total = 0
        errors: list[str] = []

        for entry in accounts:
            account_id = int(entry.get("account_id") or 0)
            user_id = int(entry.get("user_id") or 0)
            if account_id <= 0 or user_id <= 0:
                continue
            try:
                result = await self._risk_service.evaluate_circuit_breaker(
                    user_id=user_id,
                    account_id=account_id,
                    actor="scheduler",
                )
                evaluated += 1
                if result.get("auto_stop_triggered"):
                    triggered += 1
                    deployments_stopped_total += len(result.get("deployments_stopped") or [])
                    log.warning(
                        "circuit_breaker triggered account=%s pnl=%s policy=%s stopped=%s",
                        account_id,
                        result.get("realized_pnl_today"),
                        result.get("policy"),
                        len(result.get("deployments_stopped") or []),
                    )
            except Exception as exc:
                errors.append(f"account={account_id}:{exc.__class__.__name__}")
                log.warning(
                    "circuit_breaker_scheduler eval failed account=%s err=%s",
                    account_id,
                    exc,
                )

        result_payload = {
            "scanned_accounts": len(accounts),
            "evaluated": evaluated,
            "triggered": triggered,
            "deployments_stopped_total": deployments_stopped_total,
            "error_count": len(errors),
            "errors_sample": errors[:5],
        }
        self._run_count += 1
        self._last_success_at = int(time.time())
        self._last_error = None
        self._last_result = result_payload
        return result_payload

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        interval = max(15, int(getattr(settings, "CIRCUIT_BREAKER_TICK_SEC", 60) or 60))
        log.info("Circuit breaker scheduler started interval=%ss", interval)
        while not stop_event.is_set():
            try:
                result = await self.evaluate_once()
                if int(result.get("triggered") or 0) > 0:
                    log.warning("circuit_breaker scheduler tick: %s", result)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = str(exc)
                log.warning("Circuit breaker scheduler iteration failed: %s", exc)

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue
        log.info("Circuit breaker scheduler stopped")
