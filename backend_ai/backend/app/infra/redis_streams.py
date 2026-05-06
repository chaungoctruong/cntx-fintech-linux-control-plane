from __future__ import annotations

import json
from typing import Any

from app.core.redis_client import get_redis_write
from app.settings import settings

COMMAND_STREAM_KEY = "mt5:execution:commands"
EVENT_STREAM_KEY = "mt5:execution:events"
ACCOUNT_VERIFICATION_STREAM_KEY = "mt5:account:verification:requests"
ACCOUNT_VERIFICATION_CANCEL_SET_KEY = "mt5:account:verification:cancelled"
ACCOUNT_VERIFICATION_CANCEL_TTL_SEC = 24 * 60 * 60
COMMAND_PUBLISH_DEDUPE_KEY_PREFIX = "mt5:execution:commands:published:"
ACCOUNT_VERIFICATION_PUBLISH_DEDUPE_KEY_PREFIX = "mt5:account:verification:published:"


_COMMAND_PUBLISH_LUA = """
local existing = redis.call('GET', KEYS[3])
if existing then
  redis.call('EXPIRE', KEYS[3], tonumber(ARGV[14]))
  return {existing, '1'}
end

local stream_id = redis.call(
  'XADD',
  KEYS[1],
  '*',
  'command_id', ARGV[1],
  'command_type', ARGV[2],
  'account_id', ARGV[3],
  'deployment_id', ARGV[4],
  'bot_id', ARGV[5],
  'runner_id', ARGV[6],
  'slot_id', ARGV[7],
  'priority', ARGV[8],
  'payload_json', ARGV[9],
  'trace_id', ARGV[10]
)
redis.call('LPUSH', KEYS[2], ARGV[11])
if ARGV[13] == '1' then
  redis.call('LPUSH', KEYS[4], ARGV[11])
end
redis.call('SET', KEYS[3], stream_id, 'EX', tonumber(ARGV[14]))
return {stream_id, '0'}
"""

_ACCOUNT_VERIFICATION_PUBLISH_LUA = """
local existing = redis.call('GET', KEYS[3])
if existing then
  redis.call('EXPIRE', KEYS[3], tonumber(ARGV[10]))
  return {existing, '1'}
end

local stream_id = redis.call(
  'XADD',
  KEYS[1],
  '*',
  'job_id', ARGV[1],
  'account_id', ARGV[2],
  'runner_id', ARGV[3],
  'slot_id', ARGV[4],
  'trace_id', ARGV[5],
  'payload_json', ARGV[6]
)
redis.call('LPUSH', KEYS[2], ARGV[7])
if ARGV[9] == '1' then
  redis.call('LPUSH', KEYS[4], ARGV[7])
end
redis.call('SET', KEYS[3], stream_id, 'EX', tonumber(ARGV[10]))
return {stream_id, '0'}
"""


def _event_stream_maxlen() -> int:
    try:
        value = int(getattr(settings, "EVENT_STREAM_MAXLEN", 20000) or 20000)
    except (TypeError, ValueError):
        return 20000
    return max(1, value)


def _command_publish_dedupe_ttl_sec() -> int:
    try:
        value = int(getattr(settings, "COMMAND_DELIVERY_DEDUPE_TTL_SEC", 7 * 24 * 60 * 60) or 7 * 24 * 60 * 60)
    except (TypeError, ValueError):
        return 7 * 24 * 60 * 60
    return max(60, value)


def _account_verification_publish_dedupe_ttl_sec() -> int:
    try:
        value = int(getattr(settings, "ACCOUNT_VERIFICATION_PUBLISH_DEDUPE_TTL_SEC", 24 * 60 * 60) or 24 * 60 * 60)
    except (TypeError, ValueError):
        return 24 * 60 * 60
    return max(300, value)


class RedisStreamPublisher:
    async def publish_command_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Publish one runner command exactly once per command_id at Redis level.

        PostgreSQL is still the durable source of truth. The Redis marker only
        prevents physical queue duplication when the DB status update is missed
        after a successful Redis publish.
        """
        redis = await get_redis_write(decode_responses=True)
        if redis is None:
            raise RuntimeError("redis_unavailable")
        command_id = str(payload.get("command_id") or "").strip()
        if not command_id:
            raise ValueError("command_id_required")
        account_id = str(payload.get("account_id") or "").strip()
        runner_id = str(payload.get("runner_id") or "").strip()
        queue_key = f"mt5:account:{account_id}:commands"
        runner_queue_key = f"mt5:runner:{runner_id}:commands" if runner_id else "__mt5_runner_queue_missing__"
        marker_key = f"{COMMAND_PUBLISH_DEDUPE_KEY_PREFIX}{command_id}"
        payload_json = json.dumps(payload.get("payload") or {}, ensure_ascii=False, separators=(",", ":"))
        runner_payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        result = await redis.eval(
            _COMMAND_PUBLISH_LUA,
            4,
            COMMAND_STREAM_KEY,
            queue_key,
            marker_key,
            runner_queue_key,
            command_id,
            str(payload.get("command_type") or ""),
            account_id,
            str(payload.get("deployment_id") or ""),
            str(payload.get("bot_id") or ""),
            runner_id,
            str(payload.get("slot_id") or ""),
            str(payload.get("priority") or 0),
            payload_json,
            str(payload.get("trace_id") or ""),
            runner_payload,
            runner_queue_key,
            "1" if runner_id else "0",
            str(_command_publish_dedupe_ttl_sec()),
        )
        if isinstance(result, (list, tuple)) and result:
            stream_id = str(result[0] or "")
            duplicate = str(result[1] if len(result) > 1 else "0") == "1"
        else:
            stream_id = str(result or "")
            duplicate = False
        return {"stream_id": stream_id, "duplicate": duplicate}

    async def publish_command(self, payload: dict[str, Any]) -> str:
        result = await self.publish_command_result(payload)
        return str(result.get("stream_id") or "")

    async def requeue_runner_command_from_processing(
        self,
        *,
        runner_id: str,
        command_id: str,
        max_items: int = 500,
    ) -> dict[str, Any]:
        """Move one stale runner command from processing back to the live queue.

        Windows runners claim list items with BRPOPLPUSH/LMOVE into
        `mt5:runner:{runner_id}:commands:processing`. PostgreSQL remains the
        source of truth for deciding when a claimed command is stale; Redis is
        only scanned for the physical payload that must be requeued.
        """
        redis = await get_redis_write(decode_responses=True)
        if redis is None:
            raise RuntimeError("redis_unavailable")
        runner_id_s = str(runner_id or "").strip()
        command_id_s = str(command_id or "").strip()
        if not runner_id_s:
            raise ValueError("runner_id_required")
        if not command_id_s:
            raise ValueError("command_id_required")

        scan_limit = max(1, min(int(max_items or 500), 5000))
        processing_key = f"mt5:runner:{runner_id_s}:commands:processing"
        source_key = f"mt5:runner:{runner_id_s}:commands"
        raw_items = await redis.lrange(processing_key, 0, scan_limit - 1)
        scanned = 0
        for raw in raw_items or []:
            scanned += 1
            text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw or "")
            try:
                payload = json.loads(text.strip() or "{}")
            except Exception:
                continue
            if str(payload.get("command_id") or "").strip() != command_id_s:
                continue
            removed = int(await redis.lrem(processing_key, 1, text) or 0)
            if removed <= 0:
                return {
                    "requeued": False,
                    "reason": "processing_item_not_removed",
                    "processing_key": processing_key,
                    "source_key": source_key,
                    "scanned": scanned,
                }
            await redis.rpush(source_key, text)
            return {
                "requeued": True,
                "processing_key": processing_key,
                "source_key": source_key,
                "scanned": scanned,
            }
        return {
            "requeued": False,
            "reason": "command_not_found_in_processing",
            "processing_key": processing_key,
            "source_key": source_key,
            "scanned": scanned,
        }

    async def remove_runner_command(
        self,
        *,
        runner_id: str,
        command_id: str,
        max_items: int = 5000,
    ) -> dict[str, Any]:
        redis = await get_redis_write(decode_responses=True)
        if redis is None:
            raise RuntimeError("redis_unavailable")
        runner_id_s = str(runner_id or "").strip()
        command_id_s = str(command_id or "").strip()
        if not runner_id_s or not command_id_s:
            return {"removed": 0, "scanned": 0}

        scan_limit = max(1, min(int(max_items or 5000), 10000))
        keys = [
            f"mt5:runner:{runner_id_s}:commands",
            f"mt5:runner:{runner_id_s}:commands:processing",
        ]
        removed = 0
        scanned = 0
        for key in keys:
            raw_items = await redis.lrange(key, 0, scan_limit - 1)
            for raw in raw_items or []:
                scanned += 1
                text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw or "")
                try:
                    payload = json.loads(text.strip() or "{}")
                except Exception:
                    continue
                if str(payload.get("command_id") or "").strip() != command_id_s:
                    continue
                removed += int(await redis.lrem(key, 1, text) or 0)
        return {"removed": removed, "scanned": scanned}

    async def publish_event(self, payload: dict[str, Any]) -> str:
        redis = await get_redis_write(decode_responses=True)
        if redis is None:
            raise RuntimeError("redis_unavailable")
        stream_id = await redis.xadd(
            EVENT_STREAM_KEY,
            fields={
                "event_id": str(payload.get("event_id") or ""),
                "event_type": str(payload.get("event_type") or ""),
                "account_id": str(payload.get("account_id") or ""),
                "deployment_id": str(payload.get("deployment_id") or ""),
                "bot_id": str(payload.get("bot_id") or ""),
                "runner_id": str(payload.get("runner_id") or ""),
                "slot_id": str(payload.get("slot_id") or ""),
                "command_id": str(payload.get("command_id") or ""),
                "severity": str(payload.get("severity") or ""),
                "payload_json": json.dumps(payload.get("payload") or {}, ensure_ascii=False, separators=(",", ":")),
                "trace_id": str(payload.get("trace_id") or ""),
            },
            maxlen=_event_stream_maxlen(),
            approximate=True,
        )
        return str(stream_id or "")

    async def publish_account_verification(self, payload: dict[str, Any]) -> str:
        redis = await get_redis_write(decode_responses=True)
        if redis is None:
            raise RuntimeError("redis_unavailable")
        job_id = str(payload.get("job_id") or "").strip()
        if not job_id:
            raise ValueError("job_id_required")
        account_id = str(payload.get("account_id") or "").strip()
        runner_id = str(payload.get("runner_id") or "").strip()
        queue_key = f"mt5:account:{account_id}:verification"
        runner_queue_key = f"mt5:runner:{runner_id}:verification" if runner_id else "__mt5_runner_verification_queue_missing__"
        marker_key = f"{ACCOUNT_VERIFICATION_PUBLISH_DEDUPE_KEY_PREFIX}{job_id}"
        payload_json = json.dumps(payload.get("payload") or {}, ensure_ascii=False, separators=(",", ":"))
        runner_payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        result = await redis.eval(
            _ACCOUNT_VERIFICATION_PUBLISH_LUA,
            4,
            ACCOUNT_VERIFICATION_STREAM_KEY,
            queue_key,
            marker_key,
            runner_queue_key,
            job_id,
            account_id,
            runner_id,
            str(payload.get("slot_id") or ""),
            str(payload.get("trace_id") or ""),
            payload_json,
            runner_payload,
            runner_queue_key,
            "1" if runner_id else "0",
            str(_account_verification_publish_dedupe_ttl_sec()),
        )
        if isinstance(result, (list, tuple)) and result:
            stream_id = str(result[0] or "")
        else:
            stream_id = str(result or "")
        return str(stream_id or "")

    async def publish_account_verification_cancel(self, payload: dict[str, Any]) -> bool:
        """Best-effort signal cho runner skip 1 verification job da bi user cancel.

        DB la source of truth (status='cancelled'); Redis SET nay chi de runner
        bo qua som job dang trong queue ma khong can goi back den control plane.

        - SADD vao SET co TTL 24h (tu cleanup).
        - SADD them rieng vao SET account-scoped de runner co the cleanup khi xu ly xong.
        - Tra True neu da phat tin hieu, False neu Redis unavailable (caller co the bo qua).
        """
        try:
            redis = await get_redis_write(decode_responses=True)
        except Exception:
            return False
        if redis is None:
            return False
        job_id = str(payload.get("job_id") or "").strip()
        account_id = str(payload.get("account_id") or "").strip()
        if not job_id:
            return False
        try:
            await redis.sadd(ACCOUNT_VERIFICATION_CANCEL_SET_KEY, job_id)
            await redis.expire(ACCOUNT_VERIFICATION_CANCEL_SET_KEY, ACCOUNT_VERIFICATION_CANCEL_TTL_SEC)
            if account_id:
                account_set_key = f"mt5:account:{account_id}:verification:cancelled"
                await redis.sadd(account_set_key, job_id)
                await redis.expire(account_set_key, ACCOUNT_VERIFICATION_CANCEL_TTL_SEC)
            return True
        except Exception:
            return False
