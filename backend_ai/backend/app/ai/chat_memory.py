from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from app.core.redis_client import get_redis_read, get_redis_write
from app.ai.persistent_chat_memory import persist_chat_exchange
from app.settings import settings

log = logging.getLogger("ai_chat_memory")

_LOCAL_MEMORY: dict[str, list[dict[str, Any]]] = {}


def _enabled() -> bool:
    return bool(getattr(settings, "AI_CHAT_MEMORY_ENABLED", True))


def _max_messages() -> int:
    return max(2, int(getattr(settings, "AI_CHAT_MEMORY_MAX_MESSAGES", 10) or 10))


def _ttl_sec() -> int:
    return max(300, int(getattr(settings, "AI_CHAT_MEMORY_TTL_SEC", 21600) or 21600))


def _key_prefix() -> str:
    return str(getattr(settings, "AI_CHAT_MEMORY_KEY_PREFIX", "ai:chat:memory:") or "ai:chat:memory:").strip()


def _user_scope(user_id: str) -> str:
    value = str(user_id or "").strip()
    if not value or value.lower() in {"guest", "unknown", "anonymous"}:
        return ""
    return value[:120]


def _redis_key(user_id: str) -> str:
    scope = _user_scope(user_id)
    if not scope:
        return ""
    return f"{_key_prefix()}{scope}"


def _normalize_message(item: Any) -> Optional[dict[str, Any]]:
    if isinstance(item, str):
        content = item.strip()
        if not content:
            return None
        return {"role": "user", "content": content[:500], "ts": int(time.time())}
    if not isinstance(item, dict):
        return None

    raw_role = str(item.get("role") or item.get("sender") or item.get("author") or "").strip().lower()
    if raw_role in {"assistant", "bot", "ai", "cntx"}:
        role = "assistant"
    elif raw_role == "system":
        role = "system"
    else:
        role = "user"

    content = str(
        item.get("content")
        or item.get("message")
        or item.get("text")
        or item.get("body")
        or ""
    ).strip()
    if not content:
        return None
    return {"role": role, "content": content[:500], "ts": int(item.get("ts") or time.time())}


async def load_recent_messages(user_id: str) -> list[dict[str, Any]]:
    if not _enabled():
        return []

    key = _redis_key(user_id)
    if not key:
        return []

    max_messages = _max_messages()
    redis = await get_redis_read(decode_responses=True)
    if redis is not None:
        try:
            rows = await redis.lrange(key, -max_messages, -1)
            messages: list[dict[str, Any]] = []
            for raw in rows or []:
                try:
                    parsed = json.loads(raw)
                except Exception:
                    continue
                msg = _normalize_message(parsed)
                if msg:
                    messages.append(msg)
            if messages:
                _LOCAL_MEMORY[key] = list(messages[-max_messages:])
                return messages[-max_messages:]
        except Exception as exc:
            log.warning("AI chat memory read failed for %s: %s", key, str(exc)[:180])

    return list(_LOCAL_MEMORY.get(key, []))[-max_messages:]


async def enrich_context_with_memory(user_id: str, context: Optional[dict[str, Any]]) -> dict[str, Any]:
    base = dict(context or {}) if isinstance(context, dict) else {}

    if any(base.get(name) for name in ("recent_messages", "chat_history", "history", "messages", "conversation", "thread")):
        return base

    recent_messages = await load_recent_messages(user_id)
    if recent_messages:
        base["recent_messages"] = recent_messages
    return base


async def append_chat_exchange(
    user_id: str,
    user_msg: str,
    assistant_reply: str,
    *,
    mode: str = "chat",
    status: str = "done",
    source: str = "executor",
    context: Optional[dict[str, Any]] = None,
    use_search: bool = False,
) -> None:
    await persist_chat_exchange(
        user_id=user_id,
        user_msg=user_msg,
        assistant_reply=assistant_reply,
        mode=mode,
        status=status,
        source=source,
        context=context,
        use_search=use_search,
    )

    key = _redis_key(user_id)
    if not _enabled() or not key:
        return

    now_ts = int(time.time())
    items = [
        {"role": "user", "content": str(user_msg or "").strip()[:500], "ts": now_ts},
        {"role": "assistant", "content": str(assistant_reply or "").strip()[:700], "ts": now_ts},
    ]
    items = [item for item in (_normalize_message(x) for x in items) if item]
    if not items:
        return

    max_messages = _max_messages()
    redis = await get_redis_write(decode_responses=True)
    if redis is not None:
        try:
            await redis.rpush(key, *[json.dumps(item, ensure_ascii=False) for item in items])
            await redis.ltrim(key, -max_messages, -1)
            await redis.expire(key, _ttl_sec())
        except Exception as exc:
            log.warning("AI chat memory write failed for %s: %s", key, str(exc)[:180])

    merged = [*_LOCAL_MEMORY.get(key, []), *items]
    _LOCAL_MEMORY[key] = merged[-max_messages:]
