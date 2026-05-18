from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.infra.redis_streams import COMMAND_STREAM_KEY, EVENT_STREAM_KEY
from app.repositories.control_plane_repository import ControlPlaneRepository
from app.services.store_service import get_process_store
from app.core.redis_client import get_redis_write
from app.settings import settings

log = logging.getLogger("runtime_housekeeping")


def _safe_int_setting(name: str, default: int, *, minimum: int = 1, maximum: int | None = None) -> int:
    try:
        value = int(getattr(settings, name, default) or default)
    except (TypeError, ValueError):
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(value, maximum)
    return value


class RuntimeHousekeepingService:
    """Bounded retention for transient control-plane data.

    PostgreSQL remains the source of truth for account/deployment/order audit.
    This service only trims transient coordination records and Redis streams so
    long-running production nodes do not grow silently.
    """

    def __init__(self, repo: ControlPlaneRepository | None = None) -> None:
        self._repo = repo or ControlPlaneRepository(get_process_store())
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

    def _cleanup_database_once(self) -> dict[str, int]:
        batch_size = _safe_int_setting("RUNTIME_HOUSEKEEPING_BATCH_SIZE", 5000, maximum=50000)
        reservation_retention_days = _safe_int_setting("LOGIN_RESERVATION_HISTORY_RETENTION_DAYS", 30)
        out = {
            "expired_login_reservations": 0,
            "deleted_old_login_reservations": 0,
        }
        if hasattr(self._repo, "release_expired_login_reservations"):
            out["expired_login_reservations"] = int(self._repo.release_expired_login_reservations() or 0)
        if hasattr(self._repo, "delete_old_login_reservations"):
            out["deleted_old_login_reservations"] = int(
                self._repo.delete_old_login_reservations(
                    retention_days=reservation_retention_days,
                    batch_size=batch_size,
                )
                or 0
            )
        return out

    async def _trim_redis_once(self) -> dict[str, int]:
        redis = await get_redis_write(decode_responses=True)
        if redis is None:
            return {"redis_available": 0, "command_stream_len": -1, "event_stream_len": -1}

        command_maxlen = _safe_int_setting("COMMAND_STREAM_MAXLEN", 50000, maximum=1_000_000)
        event_maxlen = _safe_int_setting("EVENT_STREAM_MAXLEN", 20000, maximum=1_000_000)
        out: dict[str, int] = {"redis_available": 1}

        for key, maxlen, prefix in (
            (COMMAND_STREAM_KEY, command_maxlen, "command"),
            (EVENT_STREAM_KEY, event_maxlen, "event"),
        ):
            try:
                before = int(await redis.xlen(key) or 0)
                await redis.execute_command("XTRIM", key, "MAXLEN", "~", maxlen)
                after = int(await redis.xlen(key) or 0)
                out[f"{prefix}_stream_len"] = after
                out[f"{prefix}_stream_trimmed_estimate"] = max(0, before - after)
            except Exception as exc:
                out[f"{prefix}_stream_trim_failed"] = 1
                log.warning("runtime_housekeeping.redis_trim_failed key=%s error=%s", key, exc)
        return out

    async def run_once(self) -> dict[str, int]:
        self._last_started_at = int(time.time())
        try:
            db_result = await asyncio.to_thread(self._cleanup_database_once)
            redis_result = await self._trim_redis_once()
            result = {**db_result, **redis_result}
        except Exception as exc:
            self._last_error = str(exc)
            raise

        self._run_count += 1
        self._last_success_at = int(time.time())
        self._last_error = None
        self._last_result = dict(result)
        return result
