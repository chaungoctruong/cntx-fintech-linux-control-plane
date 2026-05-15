from __future__ import annotations

import json
import time
from typing import Any

from app.core.redis_client import get_redis_read, get_redis_write
from app.infra.redis_streams import EVENT_STREAM_KEY
from app.repositories.control_plane_repository import ControlPlaneRepository
from app.services.store_service import get_process_store
from app.settings import settings


COMMAND_DLQ_KEY = "mt5:execution:commands:dlq"
COMMAND_DLQ_STREAM_KEY = "mt5:execution:commands:dlq:events"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return int(default)
        return int(value)
    except Exception:
        return int(default)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def _row_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _command_dlq_key_for_runner(runner_id: str) -> str:
    return f"mt5:runner:{str(runner_id or '').strip()}:commands:dlq"


class CommandDeliveryObservabilityService:
    """Read-only delivery/DLQ view for ops.

    This intentionally lives outside `CommandDeliveryReconcilerService` so the
    retry/DLQ dashboard can evolve without making command dispatch more complex.
    PostgreSQL remains the durable truth; Redis DLQ keys are an operational
    mailbox for payload samples that require manual inspection.
    """

    def __init__(self, repo: ControlPlaneRepository | None = None) -> None:
        self._repo = repo or ControlPlaneRepository(get_process_store())

    def _db_status_counts(self, *, window_sec: int) -> list[dict[str, Any]]:
        window_i = max(60, int(window_sec or 3600))

        def _do(con: Any, cur: Any) -> list[dict[str, Any]]:
            cur.execute(
                """
                SELECT
                    command_type,
                    delivery_status,
                    COUNT(*)::int AS count,
                    COALESCE(MAX(EXTRACT(EPOCH FROM (NOW() - created_at))), 0)::float AS oldest_age_sec
                FROM execution_commands
                WHERE created_at >= (NOW() - (%s * INTERVAL '1 second'))
                GROUP BY command_type, delivery_status
                ORDER BY command_type, delivery_status
                """,
                (window_i,),
            )
            return [dict(row) for row in (cur.fetchall() or [])]

        return self._repo._store._with_retry_read(_do)

    def _db_stale_commands(self, *, stale_sec: int, limit: int) -> list[dict[str, Any]]:
        stale_i = max(10, int(stale_sec or 300))
        limit_i = max(1, min(int(limit or 50), 500))

        def _do(con: Any, cur: Any) -> list[dict[str, Any]]:
            cur.execute(
                """
                SELECT
                    command_id,
                    command_type,
                    account_id,
                    deployment_id,
                    runner_id,
                    slot_id,
                    delivery_status,
                    last_error,
                    created_at,
                    updated_at,
                    COALESCE(EXTRACT(EPOCH FROM (NOW() - created_at)), 0)::float AS age_sec,
                    COALESCE(EXTRACT(EPOCH FROM (NOW() - updated_at)), 0)::float AS idle_sec,
                    CASE
                        WHEN COALESCE(payload_json->>'delivery_replay_failures', '') ~ '^[0-9]+$'
                            THEN (payload_json->>'delivery_replay_failures')::int
                        ELSE 0
                    END AS delivery_replay_failures,
                    CASE
                        WHEN COALESCE(payload_json->>'processing_requeue_count', '') ~ '^[0-9]+$'
                            THEN (payload_json->>'processing_requeue_count')::int
                        ELSE 0
                    END AS processing_requeue_count
                FROM execution_commands
                WHERE delivery_status IN ('pending', 'queued', 'dispatched')
                  AND updated_at < (NOW() - (%s * INTERVAL '1 second'))
                ORDER BY updated_at ASC, created_at ASC
                LIMIT %s
                """,
                (stale_i, limit_i),
            )
            return [dict(row) for row in (cur.fetchall() or [])]

        return self._repo._store._with_retry_read(_do)

    def _db_failed_summary(self, *, window_sec: int) -> list[dict[str, Any]]:
        window_i = max(60, int(window_sec or 3600))

        def _do(con: Any, cur: Any) -> list[dict[str, Any]]:
            cur.execute(
                """
                SELECT
                    command_type,
                    COALESCE(NULLIF(last_error, ''), 'unknown') AS reason,
                    COUNT(*)::int AS count,
                    COALESCE(MAX(EXTRACT(EPOCH FROM (NOW() - updated_at))), 0)::float AS newest_age_sec
                FROM execution_commands
                WHERE delivery_status = 'failed'
                  AND updated_at >= (NOW() - (%s * INTERVAL '1 second'))
                GROUP BY command_type, COALESCE(NULLIF(last_error, ''), 'unknown')
                ORDER BY count DESC, command_type ASC
                LIMIT 50
                """,
                (window_i,),
            )
            return [dict(row) for row in (cur.fetchall() or [])]

        return self._repo._store._with_retry_read(_do)

    async def _redis_snapshot(self, *, runner_ids: list[str]) -> dict[str, Any]:
        out: dict[str, Any] = {
            "available": False,
            "global_dlq_depth": 0,
            "dlq_stream_length": 0,
            "event_stream_length": 0,
            "event_pending": 0,
            "runner_queues": [],
            "error": None,
        }
        try:
            redis = await get_redis_read(decode_responses=True)
            if redis is None:
                out["error"] = "redis_unavailable"
                return out
            out["available"] = True
            out["global_dlq_depth"] = _safe_int(await redis.llen(COMMAND_DLQ_KEY))
            out["dlq_stream_length"] = _safe_int(await redis.xlen(COMMAND_DLQ_STREAM_KEY))
            out["event_stream_length"] = _safe_int(await redis.xlen(EVENT_STREAM_KEY))
            try:
                pending = await redis.xpending(
                    EVENT_STREAM_KEY,
                    str(getattr(settings, "CONTROL_PLANE_EVENT_CONSUMER_GROUP", "control-plane-event-audit") or "control-plane-event-audit"),
                )
                if isinstance(pending, dict):
                    out["event_pending"] = _safe_int(pending.get("pending") or pending.get("count"))
                elif isinstance(pending, (list, tuple)) and pending:
                    out["event_pending"] = _safe_int(pending[0])
            except Exception:
                out["event_pending"] = 0

            ids = sorted({str(item or "").strip() for item in runner_ids if str(item or "").strip()})
            for runner_id in ids:
                live_key = f"mt5:runner:{runner_id}:commands"
                processing_key = f"{live_key}:processing"
                dlq_key = _command_dlq_key_for_runner(runner_id)
                out["runner_queues"].append(
                    {
                        "runner_id": runner_id,
                        "commands": _safe_int(await redis.llen(live_key)),
                        "commands_processing": _safe_int(await redis.llen(processing_key)),
                        "commands_dlq": _safe_int(await redis.llen(dlq_key)),
                    }
                )
        except Exception as exc:
            out["available"] = False
            out["error"] = f"{exc.__class__.__name__}:{str(exc)[:160]}"
        return out

    async def snapshot(
        self,
        *,
        runner_ids: list[str],
        window_sec: int | None = None,
        stale_sec: int | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        window_i = max(
            60,
            int(window_sec or getattr(settings, "COMMAND_DELIVERY_OBSERVABILITY_WINDOW_SEC", 3600) or 3600),
        )
        stale_i = max(
            10,
            int(stale_sec or getattr(settings, "COMMAND_DELIVERY_OBSERVABILITY_STALE_SEC", 300) or 300),
        )
        stale_commands = self._db_stale_commands(stale_sec=stale_i, limit=limit)
        status_counts = self._db_status_counts(window_sec=window_i)
        failed_summary = self._db_failed_summary(window_sec=window_i)
        redis = await self._redis_snapshot(runner_ids=runner_ids)
        redis_dlq_total = _safe_int(redis.get("global_dlq_depth")) + sum(
            _safe_int(item.get("commands_dlq"))
            for item in list(redis.get("runner_queues") or [])
            if isinstance(item, dict)
        )
        failed_total = sum(_safe_int(item.get("count")) for item in failed_summary)
        stale_total = len(stale_commands)
        level = "ok"
        blockers: list[str] = []
        warnings: list[str] = []
        if redis_dlq_total > 0:
            level = "critical"
            blockers.append("command_dlq_not_empty")
        if stale_total > 0:
            level = "critical" if level == "critical" else "degraded"
            warnings.append("stale_delivery_commands")
        if failed_total > 0:
            if level == "ok":
                level = "degraded"
            warnings.append("recent_failed_commands")
        if not redis.get("available"):
            level = "critical"
            blockers.append("redis_unavailable")
        return {
            "level": level,
            "generated_at": int(time.time()),
            "window_sec": window_i,
            "stale_sec": stale_i,
            "summary": {
                "stale_commands": stale_total,
                "recent_failed_commands": failed_total,
                "redis_dlq_total": redis_dlq_total,
                "event_pending": _safe_int(redis.get("event_pending")),
            },
            "blockers": blockers,
            "warnings": warnings,
            "status_counts": status_counts,
            "stale_commands": stale_commands,
            "failed_summary": failed_summary,
            "redis": redis,
        }

    async def dead_letter_command(
        self,
        *,
        command_id: str,
        reason: str,
        source: str = "manual",
    ) -> dict[str, Any]:
        command_id_s = str(command_id or "").strip()
        if not command_id_s:
            raise ValueError("command_id_required")
        row = self._repo.get_execution_command(command_id=command_id_s)
        if not row:
            raise ValueError("command_not_found")
        redis = await get_redis_write(decode_responses=True)
        if redis is None:
            raise RuntimeError("redis_unavailable")
        payload = {
            "command_id": command_id_s,
            "command_type": row.get("command_type"),
            "account_id": row.get("account_id"),
            "deployment_id": row.get("deployment_id"),
            "runner_id": row.get("runner_id"),
            "slot_id": row.get("slot_id"),
            "delivery_status": row.get("delivery_status"),
            "last_error": row.get("last_error"),
            "reason": str(reason or "dead_lettered").strip()[:240],
            "source": str(source or "manual").strip()[:80],
            "payload": _row_payload(row.get("payload_json")),
            "dead_lettered_at": int(time.time()),
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        runner_id = str(row.get("runner_id") or "").strip()
        maxlen = max(100, int(getattr(settings, "COMMAND_DELIVERY_DLQ_MAXLEN", 5000) or 5000))
        pipe = redis.pipeline(transaction=False)
        pipe.lpush(COMMAND_DLQ_KEY, encoded)
        pipe.ltrim(COMMAND_DLQ_KEY, 0, maxlen - 1)
        if runner_id:
            runner_key = _command_dlq_key_for_runner(runner_id)
            pipe.lpush(runner_key, encoded)
            pipe.ltrim(runner_key, 0, maxlen - 1)
        pipe.xadd(
            COMMAND_DLQ_STREAM_KEY,
            fields={
                "command_id": command_id_s,
                "command_type": str(row.get("command_type") or ""),
                "runner_id": runner_id,
                "slot_id": str(row.get("slot_id") or ""),
                "reason": payload["reason"],
                "payload_json": encoded,
            },
            maxlen=maxlen,
            approximate=True,
        )
        await pipe.execute()
        self._repo.update_execution_command_delivery(
            command_id=command_id_s,
            status="failed",
            error_text=f"dead_lettered:{payload['reason']}"[:200],
            payload={
                "dead_lettered": True,
                "dead_letter_reason": payload["reason"],
                "dead_letter_source": payload["source"],
            },
        )
        return {"ok": True, "command_id": command_id_s, "runner_id": runner_id, "dlq_key": COMMAND_DLQ_KEY}
