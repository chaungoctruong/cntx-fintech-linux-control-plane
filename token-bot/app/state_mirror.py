from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

import redis


log = logging.getLogger("token-bot.mirror")

KEY_PREFIX = "tokenbot:jti:"

STATE_VALID = "valid"
STATE_REVOKED = "revoked"
STATE_LOCKED = "locked"


def make_client(url: str | None) -> redis.Redis | None:
    if not url:
        log.warning("REDIS_URL chưa cấu hình — state mirror tắt (backend sẽ không thấy token)")
        return None
    try:
        client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2)
        client.ping()
        log.info("redis mirror connected url=%s", url.split("@")[-1])
        return client
    except Exception:
        log.exception("redis_connect_failed url=%s", url)
        return None


def mirror(
    client: redis.Redis | None,
    *,
    jti: str,
    state: str,
    partner_id: str,
    bot_id: str,
    account_id: int | None,
    end_user_label: str | None,
    expires_at: datetime,
    grace_sec: int = 7 * 86400,
) -> bool:
    """Ghi token state vào Redis để backend chính có thể đọc.

    Mỗi state change (issue/revoke/lock) đều ghi lại với cùng key,
    TTL = max(remaining_until_expiry, 1) + grace_sec (mặc định 7 ngày). Sau khi
    TTL hết, key tự xóa → backend coi như "unknown token" → reject.
    """
    if client is None:
        return False
    key = f"{KEY_PREFIX}{jti}"
    payload: dict[str, Any] = {
        "state": state,
        "partner_id": partner_id,
        "bot_id": bot_id,
        "account_id": account_id,
        "end_user_label": end_user_label,
        "expires_at": expires_at.isoformat(timespec="seconds"),
        "updated_at": datetime.utcnow().isoformat(timespec="seconds"),
    }
    now = datetime.utcnow()
    remain = int((expires_at - now).total_seconds())
    ttl = max(remain, 1) + grace_sec
    try:
        client.set(key, json.dumps(payload, ensure_ascii=False), ex=ttl)
        log.info("mirror jti=%s state=%s ttl=%ds", jti, state, ttl)
        return True
    except Exception:
        log.exception("redis_mirror_failed jti=%s state=%s", jti, state)
        return False


def get_state(client: redis.Redis | None, jti: str) -> dict[str, Any] | None:
    if client is None:
        return None
    try:
        raw = client.get(f"{KEY_PREFIX}{jti}")
        return json.loads(raw) if raw else None
    except Exception:
        log.exception("redis_get_state_failed jti=%s", jti)
        return None
