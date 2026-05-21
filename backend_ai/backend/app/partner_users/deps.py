from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

import jwt
import redis as redis_lib
from fastapi import Header, HTTPException, status

from app.settings import settings

from .schemas import PartnerUserContext


log = logging.getLogger("partner-user.deps")


@lru_cache(maxsize=1)
def _redis_client() -> redis_lib.Redis | None:
    url = getattr(settings, "REDIS_URL", None) or getattr(settings, "redis_url", None)
    if not url:
        log.warning("REDIS_URL chưa cấu hình — partner-user auth fail-closed")
        return None
    try:
        client = redis_lib.Redis.from_url(url, decode_responses=True, socket_timeout=2)
        client.ping()
        return client
    except Exception:
        log.exception("redis_connect_failed url=%s", str(url).split("@")[-1])
        return None


def _jwt_secret() -> str | None:
    secret = (
        getattr(settings, "PARTNER_USER_JWT_SECRET", None)
        or getattr(settings, "partner_user_jwt_secret", None)
    )
    return secret


def _key_prefix() -> str:
    return (
        getattr(settings, "PARTNER_USER_REDIS_KEY_PREFIX", None)
        or getattr(settings, "partner_user_redis_key_prefix", None)
        or "tokenbot:jti:"
    )


def verify_jwt_and_state(token: str) -> PartnerUserContext:
    """Decode + signature check + Redis state lookup.

    Mọi nhánh lỗi raise HTTPException với `errorInfo.public_code` rõ ràng
    để frontend dịch.
    """
    secret = _jwt_secret()
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"public_code": "partner_user_not_configured", "message": "PARTNER_USER_JWT_SECRET chưa cấu hình"},
        )

    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            issuer="token-bot",
            options={"require": ["exp", "iat", "iss", "sub", "jti"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"public_code": "token_expired", "message": "Token đã hết hạn"},
        )
    except jwt.InvalidIssuerError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"public_code": "token_issuer_invalid", "message": "Token không phải do hệ thống cấp"},
        )
    except jwt.InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"public_code": "token_invalid", "message": f"Token không hợp lệ: {type(e).__name__}"},
        )

    jti = claims.get("jti")
    if not isinstance(jti, str):
        raise HTTPException(status_code=401, detail={"public_code": "token_invalid", "message": "JTI sai"})

    rc = _redis_client()
    if rc is None:
        raise HTTPException(
            status_code=503,
            detail={"public_code": "redis_unavailable", "message": "State store offline — fail closed"},
        )
    raw = None
    try:
        raw = rc.get(f"{_key_prefix()}{jti}")
    except Exception:
        log.exception("redis_get_failed jti=%s", jti)
        raise HTTPException(
            status_code=503,
            detail={"public_code": "redis_unavailable", "message": "State store query lỗi"},
        )
    if not raw:
        raise HTTPException(
            status_code=403,
            detail={"public_code": "token_not_registered", "message": "Token không tồn tại trong hệ thống (đã hết grace hoặc chưa được cấp)"},
        )
    try:
        state = json.loads(raw)
    except Exception:
        raise HTTPException(status_code=500, detail={"public_code": "state_corrupt", "message": "State JSON sai"})

    st = str(state.get("state") or "")
    if st == "revoked":
        raise HTTPException(
            status_code=403,
            detail={"public_code": "token_revoked", "message": "Token đã bị thu hồi. Liên hệ đối tác."},
        )
    if st == "locked":
        raise HTTPException(
            status_code=403,
            detail={"public_code": "token_locked", "message": "Token đã hết hạn — bot bị khóa. Liên hệ đối tác để cấp mã mới."},
        )
    if st != "valid":
        raise HTTPException(
            status_code=403,
            detail={"public_code": "token_state_unknown", "message": f"State token bất thường: {st}"},
        )

    bot_ids = (claims.get("scope") or {}).get("bot_ids") or []
    bot_id = bot_ids[0] if bot_ids else state.get("bot_id")
    if not bot_id:
        raise HTTPException(
            status_code=403,
            detail={"public_code": "token_missing_scope", "message": "Token thiếu bot_id"},
        )

    # account_id resolve từ link-account mapping (lazy import tránh vòng).
    from .service import get_linked_account_id
    account_id = get_linked_account_id(jti)

    exp_ts = int(claims["exp"])
    iat_ts = int(claims["iat"])
    now = int(datetime.now(timezone.utc).timestamp())
    return PartnerUserContext(
        jti=jti,
        partner_id=str(claims["sub"]),
        account_id=account_id,
        bot_id=str(bot_id),
        end_user_label=state.get("end_user_label") or claims.get("end_user_label"),
        issued_at=datetime.fromtimestamp(iat_ts, tz=timezone.utc),
        expires_at=datetime.fromtimestamp(exp_ts, tz=timezone.utc),
        state=st,
        remaining_seconds=max(0, exp_ts - now),
    )


def _extract_bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail={"public_code": "token_missing", "message": "Thiếu header Authorization: Bearer <jwt>"},
        )
    raw = authorization.split(" ", 1)[1].strip()
    if not raw:
        raise HTTPException(
            status_code=401,
            detail={"public_code": "token_missing", "message": "Bearer token rỗng"},
        )
    return raw


def current_partner_user(authorization: str | None = Header(default=None)) -> PartnerUserContext:
    """FastAPI dependency: verify JWT + Redis → trả PartnerUserContext."""
    token = _extract_bearer(authorization)
    return verify_jwt_and_state(token)


def require_internal_key(
    x_token_bot_key: str | None = Header(default=None, alias="X-Token-Bot-Key"),
) -> None:
    """Auth nội bộ cho token-bot gọi backend (force-stop, …).

    Constant-time compare để chống timing attack.
    """
    expected = (getattr(settings, "PARTNER_USER_INTERNAL_KEY", "") or "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail={"public_code": "internal_not_configured", "message": "PARTNER_USER_INTERNAL_KEY chưa cấu hình"},
        )
    if not x_token_bot_key:
        raise HTTPException(
            status_code=401,
            detail={"public_code": "missing_internal_key", "message": "Thiếu X-Token-Bot-Key"},
        )
    if not secrets.compare_digest(x_token_bot_key, expected):
        raise HTTPException(
            status_code=401,
            detail={"public_code": "invalid_internal_key", "message": "Internal key sai"},
        )
