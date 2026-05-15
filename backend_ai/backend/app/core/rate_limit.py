"""Redis-based rate limiter cho FastAPI.

Pattern: fixed window per minute (don gian + chay tot voi spam pattern thuc te).

Identity resolution:
  - tma initData header -> telegram_id (key="user:{tg_id}")
  - fallback: client IP (key="ip:{ip}")

Fail-open: neu Redis down/timeout -> KHONG block (log warning), uu tien availability hon.

Usage:
  rate_limiter = RateLimiter()
  result = await rate_limiter.check(identity="user:123", endpoint="POST:/accounts", limit=60, window_sec=60)
  if not result["allowed"]:
      raise HTTPException(status_code=429, ...)
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from app.core.redis_client import get_redis_write

log = logging.getLogger("rate_limit")

_KEY_PREFIX = "ratelimit:"


def _bucket_key(*, identity: str, endpoint: str, window_sec: int) -> str:
    bucket = int(time.time() // max(1, int(window_sec)))
    return f"{_KEY_PREFIX}{identity}:{endpoint}:{bucket}"


class RateLimiter:
    """Sliding-by-bucket rate limiter (1 bucket per window)."""

    def __init__(self) -> None:
        self._fail_open = True  # Default: do not block khi Redis fail

    async def check(
        self,
        *,
        identity: str,
        endpoint: str,
        limit: int,
        window_sec: int = 60,
    ) -> dict[str, Any]:
        """Check va increment counter. Tra:
          {allowed, count, limit, remaining, reset_in, identity, endpoint}
        """
        if not identity or limit <= 0:
            return {
                "allowed": True,
                "count": 0,
                "limit": limit,
                "remaining": limit,
                "reset_in": window_sec,
                "identity": identity,
                "endpoint": endpoint,
            }
        key = _bucket_key(identity=identity, endpoint=endpoint, window_sec=window_sec)
        try:
            redis = await get_redis_write(decode_responses=True)
        except Exception as exc:
            if self._fail_open:
                log.warning("rate_limit redis_unavailable; fail_open key=%s err=%s", key, exc)
                return self._fail_open_result(identity, endpoint, limit, window_sec)
            raise
        if redis is None:
            return self._fail_open_result(identity, endpoint, limit, window_sec)
        try:
            pipe = redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, int(window_sec) + 5)  # +5s grace cho clock skew
            results = await pipe.execute()
            count = int(results[0] or 0)
        except Exception as exc:
            if self._fail_open:
                log.warning("rate_limit redis_error; fail_open key=%s err=%s", key, exc)
                return self._fail_open_result(identity, endpoint, limit, window_sec)
            raise

        # reset_in: thoi gian con lai trong bucket hien tai (approx)
        now = time.time()
        bucket_start = (int(now) // max(1, window_sec)) * window_sec
        reset_in = max(1, int(bucket_start + window_sec - now))
        return {
            "allowed": count <= int(limit),
            "count": count,
            "limit": int(limit),
            "remaining": max(0, int(limit) - count),
            "reset_in": reset_in,
            "identity": identity,
            "endpoint": endpoint,
        }

    def _fail_open_result(self, identity: str, endpoint: str, limit: int, window_sec: int) -> dict[str, Any]:
        return {
            "allowed": True,
            "count": 0,
            "limit": int(limit),
            "remaining": int(limit),
            "reset_in": int(window_sec),
            "identity": identity,
            "endpoint": endpoint,
            "fail_open": True,
        }


# ---------------------------------------------------------------------
# Endpoint policy table
# ---------------------------------------------------------------------
# Per-endpoint override; key = "<METHOD>:<path-pattern-prefix>"
# Path-prefix matching: longest prefix wins.
# Cac endpoint cost cao -> limit thap.

DEFAULT_LIMITS_PER_MIN = {
    "default": 60,
    "heavy": 10,  # cancel-all, start, evaluate, delete account, rotate password
    # Start/stop remains protected by service-level cooldown/idempotency. Keep
    # enough headroom for mobile retries so control actions are not blocked by
    # stale UI refresh loops.
    "deployment_action": 60,
    # Mini App polls account/deployment read models while start/stop is settling.
    # Too small a bucket leaves the UI with stale active_deployment_id after STOP.
    "miniapp_read": 600,
    "public": 300,  # /public/status (cached)
    # FE poll login slot mat 90s @ 500ms => max 180 calls. Cap 240/min cho
    # endpoint GET /api/v2/accounts/login-slots/{reservation_id} de FE
    # khong an 429 trong khi van giu bucket "default" (60/min) cho moi GET
    # khac.
    "login_slot_poll": 240,
    "ai_chat": 20,
    "ai_job_poll": 120,
}

# Order theo do uu tien (longest prefix first)
ENDPOINT_POLICIES: tuple[tuple[str, str], ...] = (
    # heavy
    ("POST:/api/v2/accounts/", "heavy"),  # connect, cancel-all, evaluate
    ("PUT:/api/v2/accounts/", "heavy"),  # credentials rotate, risk-policy
    ("DELETE:/api/v2/accounts/", "heavy"),
    ("POST:/api/v2/deployments/", "deployment_action"),  # start, stop, cancel, command
    ("DELETE:/api/v2/me", "heavy"),
    ("POST:/api/v2/me/webhooks", "heavy"),
    # mobile control-plane read model polling
    ("GET:/api/v2/miniapp/", "miniapp_read"),
    ("GET:/api/v2/mini/bots", "miniapp_read"),
    ("GET:/api/v2/accounts/login-slots/", "login_slot_poll"),
    # public
    ("GET:/api/v2/public/", "public"),
    ("GET:/api/v2/system/healthz", "public"),
    # legacy hubbot AI path outside /api/v2
    ("POST:/ai/chat", "ai_chat"),
    ("GET:/ai/chat/jobs/", "ai_job_poll"),
    # default
    ("GET:/api/v2/", "default"),
    ("POST:/api/v2/", "default"),
    ("PUT:/api/v2/", "default"),
    ("DELETE:/api/v2/", "default"),
)

# Cac path KHONG apply rate limit (internal/runner/admin/static)
SKIP_PATH_PREFIXES: tuple[str, ...] = (
    "/api/v2/runner/",
    "/api/v2/admin/",
    "/static/",
    "/_next/",
    "/healthz",  # legacy nginx probe
)


def resolve_policy(method: str, path: str) -> Optional[str]:
    """Tra ve policy name (default/heavy/public) hoac None neu skip."""
    for skip in SKIP_PATH_PREFIXES:
        if path.startswith(skip):
            return None
    method_path = f"{method.upper()}:{path}"
    # longest prefix wins
    best: tuple[int, str] | None = None
    for prefix, name in ENDPOINT_POLICIES:
        if method_path.startswith(prefix):
            if best is None or len(prefix) > best[0]:
                best = (len(prefix), name)
    return best[1] if best else None


def resolve_limit(policy_name: str, *, per_min_overrides: Optional[dict[str, int]] = None) -> int:
    overrides = per_min_overrides or {}
    return int(overrides.get(policy_name) or DEFAULT_LIMITS_PER_MIN.get(policy_name) or DEFAULT_LIMITS_PER_MIN["default"])


def resolve_identity(request: Any) -> str:
    """Tra ve identity string (user:{tg_id} hoac ip:{ip}).

    Khong parse tma initData (tốn cost va da co user_dep o downstream).
    Dung header `X-Telegram-User-Id` neu hubbot/proxy fill, hoac client IP.
    """
    headers = request.headers if hasattr(request, "headers") else {}
    tg_id = ""
    try:
        tg_id = str(headers.get("x-telegram-user-id") or "").strip()
    except Exception:
        tg_id = ""
    if tg_id:
        return f"user:{tg_id}"
    # Try parse from Authorization tma header (cheap regex, KHONG verify signature)
    try:
        auth = str(headers.get("authorization") or "").strip()
        if auth.lower().startswith("tma "):
            from urllib.parse import parse_qs, unquote_plus

            init_data = unquote_plus(auth[4:])
            qs = parse_qs(init_data, keep_blank_values=True)
            user_field = qs.get("user", [""])[0]
            if user_field:
                import json as _json

                try:
                    user_obj = _json.loads(user_field)
                    if isinstance(user_obj, dict) and user_obj.get("id"):
                        return f"user:{user_obj['id']}"
                except Exception:
                    pass
    except Exception:
        pass
    # Fallback: client IP
    try:
        client = getattr(request, "client", None)
        ip = str(getattr(client, "host", "") or "")
        if ip:
            return f"ip:{ip}"
    except Exception:
        pass
    return "anonymous"
