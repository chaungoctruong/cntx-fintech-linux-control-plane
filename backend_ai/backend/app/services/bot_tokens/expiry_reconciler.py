from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from app.services.bot_token_license import BotTokenLicenseService
from app.services.control_plane_service import get_control_plane_service
from app.services.store_service import get_process_store
from app.settings import settings

log = logging.getLogger(__name__)


class BotTokenExpiryReconciler:
    """Expire entitlements and stop deployments covered only by expired tokens."""

    def __init__(self, *, license_service: Optional[BotTokenLicenseService] = None) -> None:
        self._license_service = license_service

    def _licenses(self) -> BotTokenLicenseService:
        if self._license_service is None:
            self._license_service = BotTokenLicenseService(get_process_store())
        return self._license_service

    async def run_once(self) -> dict[str, Any]:
        batch_size = max(1, int(getattr(settings, "BOT_TOKEN_EXPIRY_BATCH_SIZE", 100) or 100))
        licenses = self._licenses()
        expired = await asyncio.to_thread(licenses.expire_due_entitlements, limit=batch_size)
        needing_stop = await asyncio.to_thread(licenses.list_expired_deployments_needing_stop, limit=batch_size)

        stopped = 0
        failed = 0
        for entitlement in needing_stop:
            deployment_id = entitlement.get("deployment_id")
            telegram_id = str(entitlement.get("telegram_id") or "").strip()
            entitlement_id = str(entitlement.get("entitlement_id") or "").strip()
            if not deployment_id or not telegram_id or not entitlement_id:
                continue
            try:
                result = await get_control_plane_service().stop_deployment(
                    telegram_id=telegram_id,
                    username=None,
                    deployment_id=int(deployment_id),
                    reason="bot_token_expired",
                )
                command = result.get("command") if isinstance(result, dict) else {}
                stop_command_id = command.get("command_id") if isinstance(command, dict) else None
                await asyncio.to_thread(
                    licenses.record_entitlement_stop_requested,
                    entitlement_id=entitlement_id,
                    stop_command_id=stop_command_id,
                    reason="bot_token_expired",
                )
                stopped += 1
            except Exception as exc:
                failed += 1
                log.warning(
                    "bot_token_expiry_stop_failed entitlement_id=%s deployment_id=%s err=%s",
                    entitlement_id,
                    deployment_id,
                    str(exc)[:240],
                )
        return {
            "expired_count": len(expired),
            "stop_candidates": len(needing_stop),
            "stopped_count": stopped,
            "failed_count": failed,
        }

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        interval_sec = max(10, int(getattr(settings, "BOT_TOKEN_EXPIRY_RECONCILE_INTERVAL_SEC", 60) or 60))
        while not stop_event.is_set():
            try:
                result = await self.run_once()
                if result.get("expired_count") or result.get("stopped_count") or result.get("failed_count"):
                    log.info("bot_token_expiry_reconciled result=%s", result)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("bot_token_expiry_reconciler_failed err=%s", str(exc)[:240])
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_sec)
            except asyncio.TimeoutError:
                pass
