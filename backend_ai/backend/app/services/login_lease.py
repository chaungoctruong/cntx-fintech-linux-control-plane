"""Distributed login lease — prevent the same MT5 login from being active on
two runner nodes simultaneously.

Spec: see top-level conversation / docs draft "Spec — Distributed Login Lease".

Design intent:
  * Redis is the single source of truth (low latency, native TTL, atomic SET NX).
  * Backend is the only writer; runners observe by virtue of receiving / not
    receiving a START_BOT command.
  * Two-phase rollout via env flags:
      - LOGIN_LEASE_ENABLED=False  → all ops are no-op, current behavior preserved.
      - LOGIN_LEASE_ENABLED=True
          + LOGIN_LEASE_ENFORCED=False → telemetry only, log conflicts as WARN, never block.
          + LOGIN_LEASE_ENFORCED=True  → conflicts raise LoginLeaseConflict → caller maps to 409.
    This matches the spec's recommended canary timeline.
  * Fail-closed when ENFORCED + Redis unavailable. Fail-open otherwise (with WARN).

Public surface (all async — Redis client is async):
  * acquire(login, runner_id, command_id, broker, server) -> AcquireResult
  * renew(login, runner_id) -> RenewResult
  * release(login, runner_id) -> bool
  * get(login) -> dict | None
  * acquire_for_account / renew_for_account / release_for_account: helpers that look
    up `login` from a reverse-index key set on acquire (avoids DB hit on heartbeat).

The heavy doc sits in CLAUDE.md §6 + the spec; this module is the implementation.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

from app.core.error_log import log_agent_event, log_agent_failure, log_agent_warning
from app.core.redis_client import get_redis_write
from app.settings import settings


_log = logging.getLogger("login_lease")


def _login_lease_enabled() -> bool:
    raw = (
        str(getattr(settings, "LOGIN_LEASE_ENABLED", False))
        if hasattr(settings, "LOGIN_LEASE_ENABLED")
        else os.getenv("LOGIN_LEASE_ENABLED", "0")
    )
    return str(raw).strip().lower() in ("1", "true", "yes", "y", "on")


def _login_lease_enforced() -> bool:
    raw = (
        str(getattr(settings, "LOGIN_LEASE_ENFORCED", False))
        if hasattr(settings, "LOGIN_LEASE_ENFORCED")
        else os.getenv("LOGIN_LEASE_ENFORCED", "0")
    )
    return str(raw).strip().lower() in ("1", "true", "yes", "y", "on")


def _ttl_sec() -> int:
    raw = getattr(settings, "LOGIN_LEASE_TTL_SEC", None)
    if raw is None:
        raw = os.getenv("LOGIN_LEASE_TTL_SEC", "60")
    try:
        return max(15, int(raw))
    except (TypeError, ValueError):
        return 60


def _normalize_login(login: Any) -> str:
    return str(login or "").strip()


def _lease_key(login: str) -> str:
    return f"mt5:login_lease:{login}"


def _account_index_key(account_id: int) -> str:
    return f"mt5:login_lease:account:{int(account_id)}"


@dataclass(frozen=True)
class AcquireResult:
    ok: bool
    reason: str  # acquired | already_owned | conflict | redis_unavailable | disabled | invalid
    owner_runner_id: Optional[str] = None
    owner_command_id: Optional[str] = None
    owner_leased_at: Optional[float] = None
    error_class: Optional[str] = None


@dataclass(frozen=True)
class RenewResult:
    ok: bool
    reason: str  # renewed | not_found | wrong_owner | redis_unavailable | disabled | invalid
    owner_runner_id: Optional[str] = None


class LoginLeaseConflict(Exception):
    """Raised in enforced mode when acquire fails due to existing owner."""

    def __init__(self, result: AcquireResult) -> None:
        super().__init__(
            f"login_lease_conflict reason={result.reason} owner={result.owner_runner_id}"
        )
        self.result = result


class LoginLeaseUnavailable(Exception):
    """Raised in enforced mode when Redis is unavailable (fail-closed)."""

    def __init__(self, error: BaseException | None = None) -> None:
        super().__init__("login_lease_unavailable")
        self.error = error


async def _redis():
    """Get write client; never raises — returns None on failure."""
    try:
        return await get_redis_write(decode_responses=True)
    except Exception as exc:
        log_agent_failure(
            _log,
            "login_lease.redis_unavailable",
            error=exc,
            error_code="redis_write_unavailable",
            operation="login_lease_redis",
            hint=(
                "Login lease cannot reach Redis. If LOGIN_LEASE_ENFORCED=True, all "
                "START_BOT dispatches are blocked (fail-closed) until Redis recovers. "
                "Check REDIS_WRITE_URL and the redis container."
            ),
        )
        return None


async def acquire(
    *,
    login: Any,
    runner_id: str,
    command_id: str,
    broker: Optional[str] = None,
    server: Optional[str] = None,
    account_id: Optional[int] = None,
) -> AcquireResult:
    """Try to claim `login` for `runner_id`. Idempotent: re-acquiring with the
    same runner_id returns `already_owned` ok=True (and refreshes TTL).
    """
    if not _login_lease_enabled():
        return AcquireResult(ok=True, reason="disabled")

    login_s = _normalize_login(login)
    runner_s = str(runner_id or "").strip()
    command_s = str(command_id or "").strip()
    if not login_s or not runner_s:
        return AcquireResult(ok=False, reason="invalid")

    redis = await _redis()
    if redis is None:
        # Fail-closed only when caller decides. Caller checks `_login_lease_enforced()`
        # to translate this into a 503; here we just report.
        return AcquireResult(ok=False, reason="redis_unavailable")

    payload = {
        "runner_id": runner_s,
        "command_id": command_s,
        "broker": broker or "",
        "server": server or "",
        "account_id": int(account_id) if account_id is not None else None,
        "leased_at": time.time(),
    }
    encoded = json.dumps(payload, ensure_ascii=False, default=str)
    ttl = _ttl_sec()
    key = _lease_key(login_s)
    try:
        ok = bool(await redis.set(key, encoded, nx=True, ex=ttl))
    except Exception as exc:
        log_agent_failure(
            _log,
            "login_lease.acquire.redis_error",
            error=exc,
            error_code="redis_set_failed",
            operation="login_lease_acquire",
            hint=(
                "Redis SET NX EX failed during acquire. If ENFORCED, dispatch will be "
                "rejected as 503. Check Redis connectivity and command logs."
            ),
            login=login_s,
            runner_id=runner_s,
        )
        return AcquireResult(ok=False, reason="redis_unavailable", error_class=exc.__class__.__name__)

    if ok:
        if account_id is not None:
            try:
                await redis.set(_account_index_key(int(account_id)), login_s, ex=ttl)
            except Exception:
                pass
        log_agent_event(
            _log,
            logging.INFO,
            "login_lease.acquired",
            operation="login_lease_acquire",
            outcome="ok",
            login=login_s,
            runner_id=runner_s,
            command_id=command_s,
            broker=broker,
            server=server,
            account_id=account_id,
            ttl_sec=ttl,
        )
        return AcquireResult(ok=True, reason="acquired")

    # Key exists — check current owner.
    try:
        existing_raw = await redis.get(key)
    except Exception as exc:
        log_agent_failure(
            _log,
            "login_lease.acquire.lookup_failed",
            error=exc,
            error_code="redis_get_failed",
            operation="login_lease_acquire",
            hint="Could not read existing lease after SET NX failed. Treat as conflict.",
            login=login_s,
        )
        return AcquireResult(ok=False, reason="redis_unavailable", error_class=exc.__class__.__name__)

    owner = _safe_json_load(existing_raw)
    owner_runner = str((owner or {}).get("runner_id") or "")
    if owner_runner == runner_s:
        # Same runner re-acquiring — refresh TTL, treat as success (idempotent).
        try:
            await redis.expire(key, ttl)
            if account_id is not None:
                await redis.expire(_account_index_key(int(account_id)), ttl)
        except Exception:
            pass
        return AcquireResult(
            ok=True,
            reason="already_owned",
            owner_runner_id=runner_s,
            owner_command_id=str((owner or {}).get("command_id") or "") or None,
            owner_leased_at=_safe_float((owner or {}).get("leased_at")),
        )

    log_agent_warning(
        _log,
        "login_lease.conflict",
        hint=(
            "An attempt to dispatch START_BOT for this MT5 login was blocked because "
            "another runner already owns the lease. If LOGIN_LEASE_ENFORCED=True the "
            "dispatch is rejected; otherwise the dispatch proceeds and this is a "
            "telemetry warning. Investigate why two runners targeted the same login: "
            "stale binding, manual force, or runner_id mismatch."
        ),
        error_code="login_busy",
        operation="login_lease_acquire",
        login=login_s,
        attempting_runner_id=runner_s,
        attempting_command_id=command_s,
        owner_runner_id=owner_runner,
        owner_command_id=str((owner or {}).get("command_id") or "") or None,
        owner_leased_at=_safe_float((owner or {}).get("leased_at")),
    )
    return AcquireResult(
        ok=False,
        reason="conflict",
        owner_runner_id=owner_runner or None,
        owner_command_id=str((owner or {}).get("command_id") or "") or None,
        owner_leased_at=_safe_float((owner or {}).get("leased_at")),
    )


async def renew(*, login: Any, runner_id: str) -> RenewResult:
    if not _login_lease_enabled():
        return RenewResult(ok=True, reason="disabled")
    login_s = _normalize_login(login)
    runner_s = str(runner_id or "").strip()
    if not login_s or not runner_s:
        return RenewResult(ok=False, reason="invalid")

    redis = await _redis()
    if redis is None:
        return RenewResult(ok=False, reason="redis_unavailable")

    key = _lease_key(login_s)
    try:
        existing_raw = await redis.get(key)
    except Exception as exc:
        log_agent_failure(
            _log,
            "login_lease.renew.lookup_failed",
            error=exc,
            error_code="redis_get_failed",
            operation="login_lease_renew",
            hint="Renew lookup failed. Treat as not_found; runner may be told to STOP.",
            login=login_s,
            runner_id=runner_s,
        )
        return RenewResult(ok=False, reason="redis_unavailable")

    if not existing_raw:
        log_agent_warning(
            _log,
            "login_lease.renew.expired",
            hint=(
                "Lease for this login has expired but the runner is still sending "
                "heartbeats — orphaned. Backend should send STOP_BOT to this runner. "
                "Likely cause: backend restart + runner heartbeat dropped longer than TTL."
            ),
            error_code="lease_expired",
            operation="login_lease_renew",
            login=login_s,
            runner_id=runner_s,
        )
        return RenewResult(ok=False, reason="not_found")

    owner = _safe_json_load(existing_raw) or {}
    owner_runner = str(owner.get("runner_id") or "")
    if owner_runner != runner_s:
        log_agent_warning(
            _log,
            "login_lease.renew.wrong_owner",
            hint=(
                "Heartbeat came from a runner that does NOT own the login lease. "
                "Backend should send STOP_BOT to this runner immediately to prevent "
                "double-login on the broker side."
            ),
            error_code="lease_wrong_owner",
            operation="login_lease_renew",
            login=login_s,
            heartbeat_runner_id=runner_s,
            owner_runner_id=owner_runner,
        )
        return RenewResult(ok=False, reason="wrong_owner", owner_runner_id=owner_runner or None)

    try:
        await redis.expire(key, _ttl_sec())
        account_raw = owner.get("account_id")
        if account_raw is not None:
            try:
                await redis.expire(_account_index_key(int(account_raw)), _ttl_sec())
            except Exception:
                pass
    except Exception as exc:
        log_agent_failure(
            _log,
            "login_lease.renew.expire_failed",
            error=exc,
            error_code="redis_expire_failed",
            operation="login_lease_renew",
            hint="EXPIRE on lease key failed. Will retry on next heartbeat.",
            login=login_s,
        )
        return RenewResult(ok=False, reason="redis_unavailable")

    return RenewResult(ok=True, reason="renewed", owner_runner_id=runner_s)


async def release(*, login: Any, runner_id: str) -> bool:
    if not _login_lease_enabled():
        return True
    login_s = _normalize_login(login)
    runner_s = str(runner_id or "").strip()
    if not login_s:
        return False

    redis = await _redis()
    if redis is None:
        return False

    key = _lease_key(login_s)
    try:
        existing_raw = await redis.get(key)
        owner = _safe_json_load(existing_raw) or {}
        owner_runner = str(owner.get("runner_id") or "")
        if not existing_raw:
            return True  # already gone, no-op
        if runner_s and owner_runner and owner_runner != runner_s:
            log_agent_warning(
                _log,
                "login_lease.release.wrong_owner",
                hint=(
                    "A release request came from a runner that does not own the lease. "
                    "Refusing to delete to avoid releasing the real owner's lease."
                ),
                error_code="lease_wrong_owner",
                operation="login_lease_release",
                login=login_s,
                requesting_runner_id=runner_s,
                owner_runner_id=owner_runner,
            )
            return False
        await redis.delete(key)
        account_raw = owner.get("account_id")
        if account_raw is not None:
            try:
                await redis.delete(_account_index_key(int(account_raw)))
            except Exception:
                pass
        log_agent_event(
            _log,
            logging.INFO,
            "login_lease.released",
            operation="login_lease_release",
            outcome="ok",
            login=login_s,
            runner_id=runner_s or owner_runner,
            previous_command_id=str(owner.get("command_id") or "") or None,
        )
        return True
    except Exception as exc:
        log_agent_failure(
            _log,
            "login_lease.release.redis_error",
            error=exc,
            error_code="redis_delete_failed",
            operation="login_lease_release",
            hint="Lease delete failed. Lease will TTL-expire within ~60s.",
            login=login_s,
            runner_id=runner_s,
        )
        return False


async def get(*, login: Any) -> Optional[dict]:
    if not _login_lease_enabled():
        return None
    login_s = _normalize_login(login)
    if not login_s:
        return None
    redis = await _redis()
    if redis is None:
        return None
    try:
        raw = await redis.get(_lease_key(login_s))
    except Exception:
        return None
    return _safe_json_load(raw)


# ---- account_id-keyed convenience wrappers (use the reverse index) ----


async def renew_for_account(*, account_id: int, runner_id: str) -> RenewResult:
    if not _login_lease_enabled():
        return RenewResult(ok=True, reason="disabled")
    redis = await _redis()
    if redis is None:
        return RenewResult(ok=False, reason="redis_unavailable")
    try:
        login = await redis.get(_account_index_key(int(account_id)))
    except Exception:
        return RenewResult(ok=False, reason="redis_unavailable")
    if not login:
        # No active lease for this account — heartbeat is benign (e.g. account
        # without a running deployment). Don't log warn.
        return RenewResult(ok=True, reason="not_found")
    return await renew(login=login, runner_id=runner_id)


async def release_for_account(*, account_id: int, runner_id: str) -> bool:
    if not _login_lease_enabled():
        return True
    redis = await _redis()
    if redis is None:
        return False
    try:
        login = await redis.get(_account_index_key(int(account_id)))
    except Exception:
        return False
    if not login:
        return True
    return await release(login=login, runner_id=runner_id)


# ---- Internals ----


def _safe_json_load(raw: Any) -> Optional[dict]:
    if raw is None:
        return None
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        return json.loads(raw)
    except Exception:
        return None


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def is_enabled() -> bool:
    return _login_lease_enabled()


def is_enforced() -> bool:
    return _login_lease_enabled() and _login_lease_enforced()
