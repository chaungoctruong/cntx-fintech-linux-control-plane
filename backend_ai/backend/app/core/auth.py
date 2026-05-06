"""
Telegram WebApp initData verification (HMAC-SHA256) and FastAPI dependency.
Only requests with valid initData in Authorization: tma <initData> are accepted.
"""
from __future__ import annotations

import hmac
import hashlib
import logging
from typing import Any, Optional
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException, status

from app.settings import settings

log = logging.getLogger(__name__)


def verify_telegram_webapp_data(init_data: str, bot_token: str) -> bool:
    """
    Validate Telegram Mini App initData using HMAC-SHA256.
    Algorithm: secret_key = HMAC-SHA256("WebAppData", bot_token);
               data_check_string = sorted params (excluding hash) joined as "key=value" with newline;
               computed_hash = HMAC-SHA256(secret_key, data_check_string).hexdigest();
               return computed_hash == init_data hash.
    """
    if not (init_data and bot_token):
        return False
    try:
        parsed = parse_qsl(init_data, keep_blank_values=True)
        params = dict(parsed)
        hash_from_tg = params.pop("hash", None)
        if not hash_from_tg:
            return False
        # Sort by key and build data_check_string: key=value per line
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
        secret_key = hmac.new(
            b"WebAppData",
            bot_token.encode(),
            hashlib.sha256,
        ).digest()
        computed = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(computed, hash_from_tg)
    except Exception as e:
        log.debug("verify_telegram_webapp_data failed: %s", e)
        return False


def _parse_user_from_init_data(init_data: str) -> Optional[dict[str, Any]]:
    """Parse 'user' JSON from init_data query string. Returns dict with id, username, etc. or None."""
    try:
        from urllib.parse import parse_qs
        import json
        parsed = parse_qs(init_data, keep_blank_values=True)
        user_raw = parsed.get("user")
        if not user_raw or not user_raw[0]:
            return None
        return json.loads(user_raw[0])
    except Exception:
        return None


async def get_tg_user(
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> dict[str, Any]:
    """
    FastAPI dependency: extract initData from Authorization: tma <initData>,
    verify with BOT_TOKEN, return { "telegram_id": str, "username": str | None }.
    """
    bot_token = (getattr(settings, "TELEGRAM_BOT_TOKEN", None) or "").strip()
    if not bot_token:
        log.warning("TELEGRAM_BOT_TOKEN not set; rejecting all tma auth")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server auth not configured",
        )
    if not authorization or not authorization.strip().lower().startswith("tma "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization (expected: tma <initData>)",
        )
    init_data = authorization.strip()[4:].strip()
    if not init_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Empty initData",
        )
    if not verify_telegram_webapp_data(init_data, bot_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid initData signature",
        )
    user = _parse_user_from_init_data(init_data)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found in initData",
        )
    telegram_id = str(user.get("id") or "").strip()
    if not telegram_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user id in initData",
        )
    username = (user.get("username") or "").strip() or None
    return {"telegram_id": telegram_id, "username": username}
