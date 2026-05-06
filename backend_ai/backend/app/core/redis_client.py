# -*- coding: utf-8 -*-
"""
Shared Redis clients: explicit write (master) vs optional read replica.

- All mutations and blocking consumers (BLPOP, streams XREADGROUP/XACK/…) MUST use get_redis_write().
- Optional REDIS_READ_URL for GET/XREAD without consumer side-effects; falls back to write pool if unset.
- get_redis() is an alias of get_redis_write() for backward compatibility.
"""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlsplit, urlunsplit
from typing import Any, Optional, Tuple

from app.core.log_hygiene import log_periodic, noisy_log_cooldown_sec

log = logging.getLogger("redis_client")

_redis_write_client: Optional[Any] = None
_redis_write_pool: Optional[Any] = None
_redis_read_client: Optional[Any] = None
_redis_read_pool: Optional[Any] = None
_redis_write_loop: Optional[asyncio.AbstractEventLoop] = None
_redis_read_loop: Optional[asyncio.AbstractEventLoop] = None
_lock_write: Optional[asyncio.Lock] = None
_lock_write_loop: Optional[asyncio.AbstractEventLoop] = None
_lock_read: Optional[asyncio.Lock] = None
_lock_read_loop: Optional[asyncio.AbstractEventLoop] = None

DEFAULT_REDIS_URL = "redis://127.0.0.1:6379/0"

try:
    from redis.exceptions import ReadOnlyError as RedisReadOnlyError
except Exception:  # pragma: no cover
    RedisReadOnlyError = type("RedisReadOnlyError", (Exception,), {})


def is_readonly_redis_error(exc: BaseException) -> bool:
    """True for replica/read-only errors (including message-only variants across redis-py versions)."""
    if exc is None:
        return False
    try:
        if isinstance(exc, RedisReadOnlyError):
            return True
    except Exception:
        pass
    if type(exc).__name__ == "ReadOnlyError":
        return True
    msg = str(exc).lower()
    if "read only" in msg and "replica" in msg:
        return True
    if "can't write against a read only" in msg:
        return True
    return False


def _get_max_connections() -> int:
    try:
        from app.settings import settings

        return max(10, min(500, int(getattr(settings, "REDIS_MAX_CONNECTIONS", 200))))
    except Exception:
        return 200


def is_redis_auth_config_error(exc: BaseException | None) -> bool:
    if exc is None:
        return False
    message = str(exc or "").lower()
    return "without any password configured" in message and "auth" in message


def is_redis_retryable_connection_error(exc: BaseException | None) -> bool:
    if exc is None:
        return False
    message = str(exc or "").lower()
    if is_redis_auth_config_error(exc):
        return True
    retryable_markers = (
        "timeout connecting to server",
        "timeout reading from",
        "timed out",
        "connection reset by peer",
        "connection refused",
        "broken pipe",
        "connection closed by server",
    )
    return any(marker in message for marker in retryable_markers)


def strip_redis_url_credentials(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return raw
    try:
        parsed = urlsplit(raw)
    except Exception:
        return raw
    if "@" not in parsed.netloc:
        return raw
    hostname = parsed.hostname or ""
    if not hostname:
        return raw
    netloc = hostname
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def _resolve_redis_write_url() -> str:
    """
    Master/write endpoint for all mutations and coordination.
    Order: explicit write URL first, then legacy REDIS_URL fallback.
    """
    try:
        from app.settings import settings

        for key in ("REDIS_WRITE_URL", "REDIS_URL"):
            u = (str(getattr(settings, key, "") or "").strip())
            if u:
                return u
        return DEFAULT_REDIS_URL
    except Exception:
        return DEFAULT_REDIS_URL


def _resolve_redis_read_url() -> str:
    """If empty or same as write URL, callers should use write pool only."""
    try:
        from app.settings import settings

        r = (str(getattr(settings, "REDIS_READ_URL", "") or "").strip())
        if not r:
            return ""
        w = _resolve_redis_write_url()
        if r == w:
            return ""
        return r
    except Exception:
        return ""


def get_resolved_redis_write_url() -> str:
    return _resolve_redis_write_url()


def get_resolved_redis_read_url() -> str:
    r = _resolve_redis_read_url()
    return r if r else get_resolved_redis_write_url()


def mask_redis_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return "(empty)"
    if "@" not in u or "://" not in u:
        return u
    try:
        scheme, rest = u.split("://", 1)
        if "@" in rest:
            hostpart = rest.split("@", 1)[-1]
            return f"{scheme}://***@{hostpart}"
    except Exception:
        pass
    return "***"


def _current_loop() -> asyncio.AbstractEventLoop:
    return asyncio.get_running_loop()


def _loop_lock(kind: str) -> asyncio.Lock:
    global _lock_write, _lock_write_loop, _lock_read, _lock_read_loop
    loop = _current_loop()
    if kind == "write":
        if _lock_write is None or _lock_write_loop is not loop:
            _lock_write = asyncio.Lock()
            _lock_write_loop = loop
        return _lock_write
    if _lock_read is None or _lock_read_loop is not loop:
        _lock_read = asyncio.Lock()
        _lock_read_loop = loop
    return _lock_read


async def _build_pool_and_client(*, url: str, decode_responses: bool) -> tuple[Any, Any]:
    from redis import asyncio as redis_asyncio

    max_conn = _get_max_connections()
    pool = redis_asyncio.ConnectionPool.from_url(
        url,
        max_connections=max_conn,
        decode_responses=decode_responses,
        socket_connect_timeout=5.0,
        socket_timeout=5.0,
    )
    client = redis_asyncio.Redis(connection_pool=pool, retry_on_timeout=True)
    return pool, client


async def _close_pool_and_client(pool: Any, client: Any) -> None:
    if client is not None:
        try:
            await client.aclose()
        except Exception:
            pass
    if pool is not None:
        try:
            await pool.disconnect()
        except Exception:
            pass


async def _connect_pool_with_auth_fallback(
    *,
    url: str,
    decode_responses: bool,
    label: str,
) -> tuple[Any, Any, str]:
    attempted_url = str(url or "").strip()
    tried_fallback = False

    while attempted_url:
        pool = None
        client = None
        try:
            pool, client = await _build_pool_and_client(
                url=attempted_url,
                decode_responses=decode_responses,
            )
            # Force an initial round-trip so auth/config errors surface here,
            # not later inside background loops.
            await client.ping()
            return pool, client, attempted_url
        except Exception as exc:
            await _close_pool_and_client(pool, client)
            fallback_url = strip_redis_url_credentials(attempted_url)
            if (
                not tried_fallback
                and fallback_url
                and fallback_url != attempted_url
                and is_redis_auth_config_error(exc)
            ):
                tried_fallback = True
                log_periodic(
                    log,
                    logging.WARNING,
                    "[REDIS_AUTH_FALLBACK] label=%s url=%s fallback_url=%s reason=%s",
                    label,
                    mask_redis_url(attempted_url),
                    mask_redis_url(fallback_url),
                    str(exc)[:200],
                    key=f"redis_auth_fallback:{label}:{mask_redis_url(attempted_url)}",
                    cooldown_sec=noisy_log_cooldown_sec(),
                )
                attempted_url = fallback_url
                continue
            raise

    raise RuntimeError("redis_url_missing")


async def get_redis_write(*, decode_responses: bool = True) -> Optional[Any]:
    global _redis_write_client, _redis_write_pool, _redis_write_loop
    try:
        from redis import asyncio as redis_asyncio
    except ImportError:
        log.warning("redis package not installed; Redis disabled")
        return None

    loop = _current_loop()
    if _redis_write_client is not None and _redis_write_loop is loop:
        return _redis_write_client
    if _redis_write_client is not None and _redis_write_loop is not loop:
        await _close_pool_and_client(_redis_write_pool, _redis_write_client)
        _redis_write_client = None
        _redis_write_pool = None
        _redis_write_loop = None

    async with _loop_lock("write"):
        loop = _current_loop()
        if _redis_write_client is not None and _redis_write_loop is loop:
            return _redis_write_client
        if _redis_write_client is not None and _redis_write_loop is not loop:
            await _close_pool_and_client(_redis_write_pool, _redis_write_client)
            _redis_write_client = None
            _redis_write_pool = None
            _redis_write_loop = None
        url = _resolve_redis_write_url()
        if not url.strip():
            return None
        try:
            _redis_write_pool, _redis_write_client, actual_url = await _connect_pool_with_auth_fallback(
                url=url,
                decode_responses=decode_responses,
                label="write",
            )
            _redis_write_loop = loop
            masked_url = mask_redis_url(actual_url)
            log_periodic(
                log,
                logging.INFO,
                "[REDIS_WRITE_CLIENT] label=write component=shared url=%s max_connections=%s",
                masked_url,
                _get_max_connections(),
                key=f"redis_write_connected:{masked_url}:{decode_responses}",
                cooldown_sec=noisy_log_cooldown_sec(),
            )
            return _redis_write_client
        except Exception as e:
            message = str(e)[:200]
            log_periodic(
                log,
                logging.WARNING,
                "Redis write connection failed (non-fatal): %s",
                message,
                key=f"redis_write_connect_failed:{type(e).__name__}:{message}",
                cooldown_sec=noisy_log_cooldown_sec(),
            )
            return None


async def get_redis_read(*, decode_responses: bool = True) -> Optional[Any]:
    """Read replica when REDIS_READ_URL set and distinct from write; else same as write."""
    read_url = _resolve_redis_read_url()
    if not read_url:
        return await get_redis_write(decode_responses=decode_responses)

    global _redis_read_client, _redis_read_pool, _redis_read_loop
    try:
        from redis import asyncio as redis_asyncio
    except ImportError:
        return None

    loop = _current_loop()
    if _redis_read_client is not None and _redis_read_loop is loop:
        return _redis_read_client
    if _redis_read_client is not None and _redis_read_loop is not loop:
        await _close_pool_and_client(_redis_read_pool, _redis_read_client)
        _redis_read_client = None
        _redis_read_pool = None
        _redis_read_loop = None

    async with _loop_lock("read"):
        loop = _current_loop()
        if _redis_read_client is not None and _redis_read_loop is loop:
            return _redis_read_client
        if _redis_read_client is not None and _redis_read_loop is not loop:
            await _close_pool_and_client(_redis_read_pool, _redis_read_client)
            _redis_read_client = None
            _redis_read_pool = None
            _redis_read_loop = None
        try:
            _redis_read_pool, _redis_read_client, actual_url = await _connect_pool_with_auth_fallback(
                url=read_url,
                decode_responses=decode_responses,
                label="read",
            )
            _redis_read_loop = loop
            masked_url = mask_redis_url(actual_url)
            log_periodic(
                log,
                logging.INFO,
                "[REDIS_READ_CLIENT] label=read component=shared url=%s max_connections=%s",
                masked_url,
                _get_max_connections(),
                key=f"redis_read_connected:{masked_url}:{decode_responses}",
                cooldown_sec=noisy_log_cooldown_sec(),
            )
            return _redis_read_client
        except Exception as e:
            message = str(e)[:200]
            log_periodic(
                log,
                logging.WARNING,
                "Redis read pool failed; falling back to write: %s",
                message,
                key=f"redis_read_connect_failed:{type(e).__name__}:{message}",
                cooldown_sec=noisy_log_cooldown_sec(),
            )
            return await get_redis_write(decode_responses=decode_responses)


async def get_redis(*, decode_responses: bool = True) -> Optional[Any]:
    """Backward compatible: primary / mutation client (master)."""
    return await get_redis_write(decode_responses=decode_responses)


async def redis_instance_role(redis: Any) -> Tuple[bool, str]:
    """
    Returns (is_master_ok, role_label).
    Uses ROLE then INFO replication.
    """
    if redis is None:
        return False, "no_client"
    try:
        role_data = await redis.execute_command("ROLE")
        if role_data:
            primary = role_data[0]
            if isinstance(primary, (bytes, bytearray)):
                primary = primary.decode("utf-8", errors="replace")
            label = str(primary or "").lower()
            if label == "master":
                return True, "master"
            return False, label or "unknown"
    except Exception as exc:
        log.debug("ROLE command failed, trying INFO replication: %s", exc)
    try:
        info = await redis.info("replication")
        rrole = str(info.get("role") or "").lower()
        if rrole == "master":
            return True, "master"
        return False, rrole or "unknown"
    except Exception as exc:
        return False, f"check_failed:{type(exc).__name__}"


async def verify_redis_write_is_master(
    *,
    consumer_name: str = "",
    queue_or_stream: str = "",
    startup_phase: bool = False,
    component: str = "",
) -> Tuple[bool, str]:
    """
    Verify the configured write Redis is a master. Logs [REDIS_ROLE_CHECK] / [REDIS_ROLE_INVALID].
    When startup_phase=True, logs include startup_blocked context for operators.
    """
    url = get_resolved_redis_write_url()
    masked = mask_redis_url(url)
    comp = component or "-"
    redis = await get_redis_write(decode_responses=True)
    if redis is None:
        log.error(
            "[REDIS_ROLE_INVALID] [REDIS_ROLE_CHECK] label=write url=%s role=no_client component=%s consumer=%s "
            "target=%s startup_phase=%s",
            masked,
            comp,
            consumer_name or "-",
            queue_or_stream or "-",
            str(startup_phase).lower(),
        )
        return False, "no_client"
    ok, role = await redis_instance_role(redis)
    log.info(
        "[REDIS_ROLE_CHECK] label=write url=%s role=%s ok=%s component=%s consumer=%s target=%s startup_phase=%s",
        masked,
        role,
        ok,
        comp,
        consumer_name or "-",
        queue_or_stream or "-",
        str(startup_phase).lower(),
    )
    if not ok:
        log.error(
            "[REDIS_ROLE_INVALID] label=write url=%s detected_role=%s component=%s consumer=%s target=%s "
            "startup_phase=%s hint=Set REDIS_WRITE_URL to the Redis master (primary) and use REDIS_READ_URL "
            "only for read replicas.",
            masked,
            role,
            comp,
            consumer_name or "-",
            queue_or_stream or "-",
            str(startup_phase).lower(),
        )
    return ok, role


async def reset_redis_write_client() -> None:
    """Close write pool (e.g. after ReadOnlyError) so next get_redis_write() reconnects."""
    global _redis_write_client, _redis_write_pool, _redis_write_loop
    async with _loop_lock("write"):
        if _redis_write_client is not None:
            try:
                await _redis_write_client.aclose()
            except Exception as e:
                log.debug("reset write client aclose: %s", e)
            _redis_write_client = None
        if _redis_write_pool is not None:
            try:
                await _redis_write_pool.disconnect()
            except Exception as e:
                log.debug("reset write pool disconnect: %s", e)
            _redis_write_pool = None
        _redis_write_loop = None
        log.debug("[REDIS_WRITE_CLIENT] pool reset (reconnect on next use)")


async def reset_redis_read_client() -> None:
    global _redis_read_client, _redis_read_pool, _redis_read_loop
    async with _loop_lock("read"):
        if _redis_read_client is not None:
            try:
                await _redis_read_client.aclose()
            except Exception:
                pass
            _redis_read_client = None
        if _redis_read_pool is not None:
            try:
                await _redis_read_pool.disconnect()
            except Exception:
                pass
            _redis_read_pool = None
        _redis_read_loop = None


async def close_redis() -> None:
    await reset_redis_write_client()
    await reset_redis_read_client()
    log.info("Shared Redis write/read clients closed")
