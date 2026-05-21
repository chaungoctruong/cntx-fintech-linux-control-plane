from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Optional

from app.services.bot_tokens.catalog import BotTradingLicenseCatalog, LicensedBotPackage, normalize_bot_identity
from app.services.bot_tokens.crypto import (
    BotTokenCryptoError,
    generate_raw_token,
    hash_token,
    hash_token_candidates,
)
from app.store import Store


class BotTokenLicenseError(ValueError):
    """Public error code for Mini App bot-token entitlement checks."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_token(raw_token: str) -> str:
    try:
        return hash_token(raw_token)
    except BotTokenCryptoError as exc:
        raise BotTokenLicenseError(str(exc) or "bot_token_hash_failed") from exc
    except Exception as exc:
        raise BotTokenLicenseError(str(exc) or "bot_token_hash_failed") from exc


def _norm_identity(value: Any) -> str:
    return normalize_bot_identity(value)


def _as_aware(value: Any) -> Optional[datetime]:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except Exception:
            return []
        if isinstance(parsed, list):
            return parsed
    return []


def _json_int_set(value: Any) -> set[int]:
    out: set[int] = set()
    for item in _json_list(value):
        try:
            out.add(int(item))
        except Exception:
            continue
    return out


def _format_entitlement(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "entitlement_id": str(row.get("entitlement_id") or ""),
        "token_id": str(row.get("token_id") or ""),
        "partner_id": str(row.get("partner_id") or ""),
        "telegram_id": str(row.get("telegram_id") or ""),
        "user_id": row.get("user_id"),
        "account_id": row.get("account_id"),
        "deployment_id": row.get("deployment_id"),
        "bot_code": str(row.get("bot_code") or ""),
        "status": str(row.get("status") or ""),
        "starts_at": row.get("starts_at").isoformat() if isinstance(row.get("starts_at"), datetime) else None,
        "expires_at": row.get("expires_at").isoformat() if isinstance(row.get("expires_at"), datetime) else None,
        "stop_command_id": row.get("stop_command_id"),
        "stop_reason": row.get("stop_reason"),
    }


def _iso_dt(value: Any) -> Optional[str]:
    dt = _as_aware(value)
    return dt.isoformat() if dt is not None else None


def _token_customer_label(row: dict[str, Any]) -> str:
    note = str(row.get("issued_to_note") or "").strip()
    if note:
        return note
    metadata = _json_dict(row.get("metadata_json"))
    label = str(metadata.get("customer_label") or "").strip()
    return label or "Không tên"


def _ceil_positive_days(start: Optional[datetime], end: Optional[datetime]) -> int:
    start_dt = _as_aware(start)
    end_dt = _as_aware(end)
    if start_dt is None or end_dt is None or end_dt <= start_dt:
        return 0
    seconds = int((end_dt - start_dt).total_seconds())
    return max(1, (seconds + 86_399) // 86_400)


def _billing_period_bounds(scope: str, *, now: datetime) -> tuple[datetime, datetime]:
    if str(scope or "").strip().lower() == "month":
        return datetime(now.year, now.month, 1, tzinfo=timezone.utc), now
    return datetime(1970, 1, 1, tzinfo=timezone.utc), now


def _token_billing_window(
    row: dict[str, Any],
    *,
    period_start: datetime,
    period_end: datetime,
) -> tuple[Optional[datetime], Optional[datetime], int]:
    # Billing starts only after the customer activates the code in Mini App.
    entitlement_start = _as_aware(row.get("entitlement_starts_at")) or _as_aware(row.get("redeemed_at"))
    if entitlement_start is None:
        return None, None, 0

    end_candidates = [
        period_end,
        _as_aware(row.get("entitlement_expires_at")),
        _as_aware(row.get("entitlement_stopped_at")),
        _as_aware(row.get("revoked_at")),
    ]
    valid_ends = [dt for dt in end_candidates if dt is not None]
    billing_start = max(entitlement_start, period_start)
    billing_end = min(valid_ends) if valid_ends else period_end
    return billing_start, billing_end, _ceil_positive_days(billing_start, billing_end)


def _product_token_runtime_status(row: dict[str, Any], *, now: datetime) -> tuple[str, str]:
    token_status = str(row.get("status") or "").strip().lower()
    entitlement_status = str(row.get("entitlement_status") or "").strip().lower()
    entitlement_expires_at = _as_aware(row.get("entitlement_expires_at"))
    deployment_status = str(row.get("deployment_status") or "").strip().lower()
    desired_state = str(row.get("deployment_desired_state") or "").strip().lower()
    health_status = str(row.get("deployment_health_status") or "").strip().lower()
    redeem_expires_at = _as_aware(row.get("redeem_expires_at"))

    if token_status == "revoked" or entitlement_status == "revoked":
        return "revoked", "Đã khóa"
    if token_status == "expired" or entitlement_status == "expired":
        return "expired", "Hết hạn"
    if token_status == "issued":
        if redeem_expires_at is not None and redeem_expires_at <= now:
            return "expired", "Hết hạn"
        return "issued", "Chưa kích hoạt"
    if entitlement_expires_at is not None and entitlement_expires_at <= now:
        return "expired", "Hết hạn"
    if token_status == "redeemed" or row.get("redeemed_at") is not None:
        running_states = {"start_requested", "starting", "running", "listening"}
        running_health = {"running", "executor_ready", "recovering", "degraded"}
        if desired_state == "running" and (
            deployment_status in running_states or health_status in running_health
        ):
            return "running", "Đang dùng bot"
        return "redeemed", "Đã kích hoạt"
    return "unknown", "Chưa rõ trạng thái"


def _format_partner_token_report_row(
    row: dict[str, Any],
    *,
    now: datetime,
    period_start: datetime,
    period_end: datetime,
) -> dict[str, Any]:
    status_code, status_label = _product_token_runtime_status(row, now=now)
    duration_days = int(row.get("duration_days") or 0)
    customer_label = _token_customer_label(row)
    entitlement_expires_at = _as_aware(row.get("entitlement_expires_at"))
    entitlement_starts_at = _as_aware(row.get("entitlement_starts_at"))
    entitlement_stopped_at = _as_aware(row.get("entitlement_stopped_at"))
    issued_at = _as_aware(row.get("issued_at"))
    billing_start, billing_end, billing_days = _token_billing_window(
        row,
        period_start=period_start,
        period_end=period_end,
    )
    return {
        "token_id": str(row.get("token_id") or ""),
        "partner_id": str(row.get("partner_id") or ""),
        "bot_code": str(row.get("bot_code") or ""),
        "duration_days": duration_days,
        "billing_days": max(0, billing_days),
        "billing_start_at": billing_start.isoformat() if billing_start is not None else None,
        "billing_end_at": billing_end.isoformat() if billing_end is not None else None,
        "customer_label": customer_label,
        "token_status": str(row.get("status") or ""),
        "status_code": status_code,
        "status_label": status_label,
        "issued_by_telegram_id": row.get("issued_by_telegram_id"),
        "issued_at": issued_at.isoformat() if issued_at is not None else None,
        "redeem_expires_at": _iso_dt(row.get("redeem_expires_at")),
        "redeemed_at": _iso_dt(row.get("redeemed_at")),
        "redeemed_by_telegram_id": row.get("redeemed_by_telegram_id"),
        "bound_user_id": row.get("bound_user_id"),
        "bound_account_id": row.get("bound_account_id"),
        "bound_deployment_id": row.get("bound_deployment_id"),
        "revoked_at": _iso_dt(row.get("revoked_at")),
        "revoke_reason": row.get("revoke_reason"),
        "entitlement_id": row.get("entitlement_id"),
        "entitlement_status": row.get("entitlement_status"),
        "entitlement_starts_at": entitlement_starts_at.isoformat()
        if entitlement_starts_at is not None
        else None,
        "entitlement_expires_at": entitlement_expires_at.isoformat()
        if entitlement_expires_at is not None
        else None,
        "entitlement_stopped_at": entitlement_stopped_at.isoformat()
        if entitlement_stopped_at is not None
        else None,
        "deployment_id": row.get("entitlement_deployment_id") or row.get("bound_deployment_id"),
        "deployment_status": row.get("deployment_status"),
        "deployment_desired_state": row.get("deployment_desired_state"),
        "deployment_health_status": row.get("deployment_health_status"),
        "is_activated": bool(row.get("redeemed_at") is not None or row.get("entitlement_id")),
        "is_running": status_code == "running",
        "is_revoked": status_code == "revoked",
        "is_expired": status_code == "expired",
    }


def _partner_token_report_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {
        "issued": 0,
        "redeemed": 0,
        "running": 0,
        "expired": 0,
        "revoked": 0,
        "unknown": 0,
    }
    by_customer: dict[str, dict[str, Any]] = {}
    total_days = 0
    for item in items:
        status_code = str(item.get("status_code") or "unknown")
        counts[status_code] = int(counts.get(status_code, 0)) + 1
        days = int(item.get("billing_days") or 0)
        total_days += days
        customer = str(item.get("customer_label") or "Không tên").strip() or "Không tên"
        bucket = by_customer.setdefault(
            customer,
            {
                "customer_label": customer,
                "token_count": 0,
                "total_days": 0,
                "issued": 0,
                "redeemed": 0,
                "running": 0,
                "expired": 0,
                "revoked": 0,
                "unknown": 0,
            },
        )
        bucket["token_count"] = int(bucket["token_count"]) + 1
        bucket["total_days"] = int(bucket["total_days"]) + days
        bucket[status_code] = int(bucket.get(status_code, 0)) + 1
    return {
        "total_tokens": len(items),
        "total_customers": len(by_customer),
        "billable_customers": sum(
            1 for item in by_customer.values() if int(item.get("total_days") or 0) > 0
        ),
        "total_days": total_days,
        "status_counts": counts,
        "by_customer": sorted(
            by_customer.values(),
            key=lambda item: (-int(item.get("total_days") or 0), str(item.get("customer_label") or "").lower()),
        ),
    }


class BotTokenLicenseService:
    """PostgreSQL-backed token entitlement bridge for Mini App MT5 bot access."""

    _schema_ready = False
    _schema_lock = Lock()

    def __init__(self, store: Store) -> None:
        self.store = store
        self._catalog = BotTradingLicenseCatalog(store=store)

    def ensure_schema(self) -> None:
        if self.__class__._schema_ready:
            return
        with self.__class__._schema_lock:
            if self.__class__._schema_ready:
                return

            self._ensure_schema_uncached()
            self.__class__._schema_ready = True

    def list_available_bots(self) -> list[dict[str, Any]]:
        """Return bot codes that can be used for product activation tokens."""
        self.ensure_schema()
        return self._catalog.list_packages()

    def _ensure_schema_uncached(self) -> None:
        def _do(_con: Any, cur: Any) -> None:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_token_partners (
                    id BIGSERIAL PRIMARY KEY,
                    partner_id TEXT NOT NULL UNIQUE,
                    partner_code TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    telegram_id TEXT NULL UNIQUE,
                    status TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'suspended', 'revoked')),
                    allowed_bot_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
                    allowed_duration_days JSONB NOT NULL DEFAULT '[1,3,7,30]'::jsonb,
                    max_active_tokens INTEGER NULL,
                    max_tokens_per_day INTEGER NULL,
                    expires_at TIMESTAMPTZ NULL,
                    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_by_admin_telegram_id TEXT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_access_tokens (
                    id BIGSERIAL PRIMARY KEY,
                    token_id TEXT NOT NULL UNIQUE,
                    token_hash TEXT NOT NULL UNIQUE,
                    partner_id TEXT NOT NULL REFERENCES bot_token_partners(partner_id) ON DELETE RESTRICT,
                    bot_code TEXT NOT NULL,
                    duration_days INTEGER NOT NULL CHECK (duration_days IN (1, 3, 7, 30)),
                    status TEXT NOT NULL DEFAULT 'issued'
                        CHECK (status IN ('issued', 'redeemed', 'revoked', 'expired')),
                    issued_by_telegram_id TEXT NULL,
                    issued_to_note TEXT NULL,
                    issued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    redeem_expires_at TIMESTAMPTZ NULL,
                    redeemed_at TIMESTAMPTZ NULL,
                    redeemed_by_telegram_id TEXT NULL,
                    bound_user_id BIGINT NULL,
                    bound_account_id BIGINT NULL,
                    bound_deployment_id BIGINT NULL,
                    revoked_at TIMESTAMPTZ NULL,
                    revoked_by_telegram_id TEXT NULL,
                    revoke_reason TEXT NULL,
                    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_token_entitlements (
                    id BIGSERIAL PRIMARY KEY,
                    entitlement_id TEXT NOT NULL UNIQUE,
                    token_id TEXT NOT NULL REFERENCES bot_access_tokens(token_id) ON DELETE RESTRICT,
                    partner_id TEXT NOT NULL REFERENCES bot_token_partners(partner_id) ON DELETE RESTRICT,
                    telegram_id TEXT NOT NULL,
                    user_id BIGINT NULL,
                    account_id BIGINT NULL,
                    deployment_id BIGINT NULL,
                    bot_code TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'expired', 'revoked')),
                    starts_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    expires_at TIMESTAMPTZ NOT NULL,
                    stopped_at TIMESTAMPTZ NULL,
                    stop_command_id TEXT NULL,
                    stop_reason TEXT NULL,
                    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_bot_token_entitlements_active_expiry "
                "ON bot_token_entitlements(expires_at, id) WHERE status = 'active'"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_bot_token_entitlements_telegram "
                "ON bot_token_entitlements(telegram_id, status, expires_at DESC)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_bot_token_entitlements_deployment "
                "ON bot_token_entitlements(deployment_id) WHERE deployment_id IS NOT NULL"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_bot_access_tokens_partner_status "
                "ON bot_access_tokens(partner_id, status, issued_at DESC)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_bot_token_partners_status "
                "ON bot_token_partners(status, partner_id)"
            )

        self.store._with_retry_locked(_do)

    def _resolve_requested_bot(self, *, bot_name: str, bot_code: Optional[str]) -> LicensedBotPackage:
        package = self._catalog.resolve(bot_code, bot_name)
        if package is None:
            raise BotTokenLicenseError("bot_token_bot_package_not_found")
        return package

    def _resolve_token_bot(self, token_bot_code: Any) -> LicensedBotPackage:
        package = self._catalog.resolve(token_bot_code)
        if package is None:
            raise BotTokenLicenseError("bot_token_bot_package_not_found")
        return package

    def _bot_matches(self, token_bot_code: Any, *, bot_name: str, bot_code: Optional[str]) -> bool:
        try:
            token_bot = self._resolve_token_bot(token_bot_code)
            requested_bot = self._resolve_requested_bot(bot_name=bot_name, bot_code=bot_code)
        except BotTokenLicenseError:
            return False
        return _norm_identity(token_bot.code) == _norm_identity(requested_bot.code)

    def _partner_allows_bot(self, partner: dict[str, Any], package: LicensedBotPackage) -> bool:
        allowed = {_norm_identity(item) for item in _json_list(partner.get("allowed_bot_codes"))}
        allowed.discard("")
        if not allowed:
            return True
        return any(identity in allowed for identity in package.identities)

    def _partner_allows_duration(self, partner: dict[str, Any], duration_days: int) -> bool:
        allowed = _json_int_set(partner.get("allowed_duration_days")) or {1, 3, 7, 30}
        return int(duration_days) in allowed

    def claim_token(
        self,
        *,
        telegram_id: str,
        user_id: int,
        account_id: int,
        bot_name: str,
        bot_code: Optional[str],
        raw_token: str,
    ) -> dict[str, Any]:
        self.ensure_schema()
        checked_at = _utc_now()
        requested_bot = self._resolve_requested_bot(bot_name=bot_name, bot_code=bot_code)
        try:
            token_hashes = hash_token_candidates(raw_token)
        except BotTokenCryptoError as exc:
            raise BotTokenLicenseError(str(exc) or "bot_token_required") from exc
        account_id = int(account_id)
        user_id = int(user_id)
        telegram_id = str(telegram_id)

        def _do(_con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                "SELECT * FROM bot_access_tokens WHERE token_hash = ANY(%s::text[]) LIMIT 1 FOR UPDATE",
                (token_hashes,),
            )
            token = dict(cur.fetchone() or {})
            if not token:
                raise BotTokenLicenseError("bot_token_not_found")

            status = str(token.get("status") or "").strip().lower()
            if status == "redeemed":
                raise BotTokenLicenseError("bot_token_already_used")
            if status == "revoked":
                raise BotTokenLicenseError("bot_token_revoked")
            if status == "expired":
                raise BotTokenLicenseError("bot_token_expired")
            if status != "issued":
                raise BotTokenLicenseError("bot_token_invalid_status")

            redeem_expires_at = _as_aware(token.get("redeem_expires_at"))
            if redeem_expires_at is not None and redeem_expires_at <= checked_at:
                cur.execute(
                    "UPDATE bot_access_tokens SET status = 'expired', updated_at = NOW() WHERE token_id = %s",
                    (token.get("token_id"),),
                )
                raise BotTokenLicenseError("bot_token_expired")

            token_bot = self._resolve_token_bot(token.get("bot_code"))
            if _norm_identity(token_bot.code) != _norm_identity(requested_bot.code):
                raise BotTokenLicenseError("bot_token_wrong_bot")

            cur.execute(
                """
                SELECT bot_code
                FROM bot_token_entitlements
                WHERE telegram_id = %s
                  AND user_id = %s
                  AND status = 'active'
                  AND expires_at > %s
                ORDER BY expires_at DESC, id DESC
                LIMIT 1
                """,
                (telegram_id, user_id, checked_at),
            )
            active_entitlement = dict(cur.fetchone() or {})
            if active_entitlement and not self._bot_matches(
                active_entitlement.get("bot_code"),
                bot_name=bot_name,
                bot_code=bot_code,
            ):
                raise BotTokenLicenseError("telegram_user_has_active_bot")

            cur.execute(
                "SELECT * FROM bot_token_partners WHERE partner_id = %s LIMIT 1 FOR UPDATE",
                (token.get("partner_id"),),
            )
            partner = dict(cur.fetchone() or {})
            if not partner:
                raise BotTokenLicenseError("bot_token_partner_not_found")
            if str(partner.get("status") or "").strip().lower() != "active":
                raise BotTokenLicenseError("bot_token_partner_locked")
            partner_expires_at = _as_aware(partner.get("expires_at"))
            if partner_expires_at is not None and partner_expires_at <= checked_at:
                raise BotTokenLicenseError("bot_token_partner_expired")

            duration_days = int(token.get("duration_days") or 0)
            if duration_days not in {1, 3, 7, 30}:
                raise BotTokenLicenseError("bot_token_duration_invalid")
            if not self._partner_allows_bot(partner, requested_bot):
                raise BotTokenLicenseError("bot_token_partner_bot_not_allowed")
            if not self._partner_allows_duration(partner, duration_days):
                raise BotTokenLicenseError("bot_token_partner_duration_not_allowed")

            max_active_tokens = partner.get("max_active_tokens")
            if max_active_tokens is not None:
                cur.execute(
                    """
                    SELECT COUNT(*)::INT AS active_count
                    FROM bot_token_entitlements
                    WHERE partner_id = %s
                      AND status = 'active'
                      AND expires_at > %s
                    """,
                    (partner.get("partner_id"), checked_at),
                )
                active_count = int((cur.fetchone() or {}).get("active_count") or 0)
                if active_count >= int(max_active_tokens):
                    raise BotTokenLicenseError("bot_token_partner_active_limit_reached")

            max_tokens_per_day = partner.get("max_tokens_per_day")
            if max_tokens_per_day is not None:
                cur.execute(
                    """
                    SELECT COUNT(*)::INT AS redeemed_today
                    FROM bot_access_tokens
                    WHERE partner_id = %s
                      AND redeemed_at >= date_trunc('day', NOW())
                    """,
                    (partner.get("partner_id"),),
                )
                redeemed_today = int((cur.fetchone() or {}).get("redeemed_today") or 0)
                if redeemed_today >= int(max_tokens_per_day):
                    raise BotTokenLicenseError("bot_token_partner_daily_limit_reached")

            entitlement_id = f"ent_{secrets.token_urlsafe(12)}"
            expires_at = checked_at + timedelta(days=duration_days)
            metadata = {
                "source": "miniapp",
                "bot_name": bot_name,
                "bot_code": bot_code,
                "licensed_bot_code": requested_bot.code,
                "licensed_bot_version": requested_bot.version,
                "package_path": requested_bot.package_path,
            }

            cur.execute(
                """
                UPDATE bot_access_tokens
                SET status = 'redeemed',
                    redeemed_at = %s,
                    redeemed_by_telegram_id = %s,
                    bound_user_id = %s,
                    bound_account_id = %s,
                    updated_at = NOW()
                WHERE token_id = %s
                """,
                (checked_at, telegram_id, user_id, account_id, token.get("token_id")),
            )
            cur.execute(
                """
                INSERT INTO bot_token_entitlements (
                    entitlement_id, token_id, partner_id, telegram_id,
                    user_id, account_id, bot_code, status,
                    starts_at, expires_at, metadata_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'active', %s, %s, %s)
                RETURNING *
                """,
                (
                    entitlement_id,
                    token.get("token_id"),
                    token.get("partner_id"),
                    telegram_id,
                    user_id,
                    account_id,
                    requested_bot.code,
                    checked_at,
                    expires_at,
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )
            return _format_entitlement(dict(cur.fetchone() or {}))

        return self.store._with_retry_locked(_do, tries=5)

    def list_active_entitlements(
        self,
        *,
        telegram_id: str,
        user_id: int,
        account_id: int,
    ) -> list[dict[str, Any]]:
        self.ensure_schema()
        checked_at = _utc_now()

        def _do(_con: Any, cur: Any) -> list[dict[str, Any]]:
            cur.execute(
                """
                SELECT e.*
                FROM bot_token_entitlements e
                JOIN bot_token_partners p ON p.partner_id = e.partner_id
                WHERE e.telegram_id = %s
                  AND e.user_id = %s
                  AND e.account_id = %s
                  AND e.status = 'active'
                  AND e.expires_at > %s
                  AND p.status = 'active'
                ORDER BY e.expires_at DESC, e.id DESC
                """,
                (str(telegram_id), int(user_id), int(account_id), checked_at),
            )
            return [_format_entitlement(dict(row)) for row in cur.fetchall()]

        return self.store._with_retry_read(_do, tries=1)

    def assert_active_entitlement(
        self,
        *,
        entitlement_id: str,
        telegram_id: str,
        user_id: int,
        account_id: int,
        bot_name: str,
        bot_code: Optional[str],
    ) -> dict[str, Any]:
        self.ensure_schema()
        checked_at = _utc_now()

        def _do(_con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                """
                SELECT e.*, p.status AS partner_status, p.expires_at AS partner_expires_at
                FROM bot_token_entitlements e
                JOIN bot_token_partners p ON p.partner_id = e.partner_id
                WHERE e.entitlement_id = %s
                  AND e.telegram_id = %s
                  AND e.user_id = %s
                  AND e.account_id = %s
                LIMIT 1
                """,
                (str(entitlement_id), str(telegram_id), int(user_id), int(account_id)),
            )
            row = dict(cur.fetchone() or {})
            if not row:
                raise BotTokenLicenseError("bot_token_entitlement_not_found")
            if str(row.get("status") or "").strip().lower() != "active":
                raise BotTokenLicenseError("bot_token_entitlement_inactive")
            if _as_aware(row.get("expires_at")) is None or _as_aware(row.get("expires_at")) <= checked_at:
                raise BotTokenLicenseError("bot_token_entitlement_expired")
            if str(row.get("partner_status") or "").strip().lower() != "active":
                raise BotTokenLicenseError("bot_token_partner_locked")
            partner_expires_at = _as_aware(row.get("partner_expires_at"))
            if partner_expires_at is not None and partner_expires_at <= checked_at:
                raise BotTokenLicenseError("bot_token_partner_expired")
            if not self._bot_matches(row.get("bot_code"), bot_name=bot_name, bot_code=bot_code):
                raise BotTokenLicenseError("bot_token_wrong_bot")
            return _format_entitlement(row)

        return self.store._with_retry_read(_do, tries=1)

    def bind_deployment(self, *, entitlement_id: str, deployment_id: int) -> None:
        self.ensure_schema()

        def _do(_con: Any, cur: Any) -> None:
            cur.execute(
                """
                UPDATE bot_token_entitlements
                SET deployment_id = %s,
                    metadata_json = COALESCE(metadata_json, '{}'::jsonb) || %s::jsonb,
                    updated_at = NOW()
                WHERE entitlement_id = %s
                  AND status = 'active'
                  AND expires_at > NOW()
                  AND (deployment_id IS NULL OR deployment_id = %s)
                """,
                (
                    int(deployment_id),
                    json.dumps({"deployment_bound_at": _utc_now().isoformat()}, ensure_ascii=False),
                    str(entitlement_id),
                    int(deployment_id),
                ),
            )
            if int(cur.rowcount or 0) < 1:
                raise BotTokenLicenseError("bot_token_entitlement_bind_failed")

        self.store._with_retry_locked(_do, tries=1)

    def upsert_partner(
        self,
        *,
        partner_code: str,
        display_name: str,
        partner_id: Optional[str] = None,
        telegram_id: Optional[str] = None,
        allowed_bot_codes: Optional[list[str]] = None,
        allowed_duration_days: Optional[list[int]] = None,
        max_active_tokens: Optional[int] = None,
        max_tokens_per_day: Optional[int] = None,
        expires_at: Optional[datetime] = None,
        created_by_admin_telegram_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        self.ensure_schema()
        code = re.sub(r"[^a-z0-9_]+", "_", str(partner_code or "").strip().lower()).strip("_")
        if not code:
            raise BotTokenLicenseError("bot_token_partner_code_required")
        partner_id_s = str(partner_id or f"partner_{code}").strip()
        display_name_s = str(display_name or code).strip()
        requested_codes = allowed_bot_codes if allowed_bot_codes is not None else []
        canonical_allowed: list[str] = []
        for item in requested_codes:
            package = self._catalog.resolve(item)
            if package is None:
                raise BotTokenLicenseError("bot_token_bot_package_not_found")
            if package.code not in canonical_allowed:
                canonical_allowed.append(package.code)
        durations_set: set[int] = set()
        for day in allowed_duration_days or [1, 3, 7, 30]:
            try:
                day_i = int(day)
            except Exception:
                continue
            if day_i in {1, 3, 7, 30}:
                durations_set.add(day_i)
        durations = sorted(durations_set)
        if not durations:
            raise BotTokenLicenseError("bot_token_duration_invalid")

        def _do(_con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                """
                INSERT INTO bot_token_partners (
                    partner_id, partner_code, display_name, telegram_id, status,
                    allowed_bot_codes, allowed_duration_days,
                    max_active_tokens, max_tokens_per_day, expires_at,
                    metadata_json, created_by_admin_telegram_id, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, 'active', %s::jsonb, %s::jsonb, %s, %s, %s, %s::jsonb, %s, NOW(), NOW())
                ON CONFLICT (partner_id) DO UPDATE SET
                    partner_code = EXCLUDED.partner_code,
                    display_name = EXCLUDED.display_name,
                    telegram_id = EXCLUDED.telegram_id,
                    allowed_bot_codes = EXCLUDED.allowed_bot_codes,
                    allowed_duration_days = EXCLUDED.allowed_duration_days,
                    max_active_tokens = EXCLUDED.max_active_tokens,
                    max_tokens_per_day = EXCLUDED.max_tokens_per_day,
                    expires_at = EXCLUDED.expires_at,
                    metadata_json = EXCLUDED.metadata_json,
                    updated_at = NOW()
                RETURNING *
                """,
                (
                    partner_id_s,
                    code,
                    display_name_s,
                    str(telegram_id).strip() if telegram_id else None,
                    json.dumps(canonical_allowed, ensure_ascii=False),
                    json.dumps(durations, ensure_ascii=False),
                    max_active_tokens,
                    max_tokens_per_day,
                    expires_at,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    str(created_by_admin_telegram_id).strip() if created_by_admin_telegram_id else None,
                ),
            )
            return dict(cur.fetchone() or {})

        return self.store._with_retry_locked(_do, tries=1)

    def issue_token(
        self,
        *,
        partner_id: str,
        bot_code: str,
        duration_days: int,
        issued_by_telegram_id: Optional[str] = None,
        issued_to_note: Optional[str] = None,
        redeem_expires_at: Optional[datetime] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        self.ensure_schema()
        package = self._resolve_token_bot(bot_code)
        duration = int(duration_days)
        if duration not in {1, 3, 7, 30}:
            raise BotTokenLicenseError("bot_token_duration_invalid")

        raw_token = generate_raw_token(bot_code=package.code, duration_days=duration)
        token_hash = _hash_token(raw_token)
        token_id = f"tok_{secrets.token_urlsafe(12)}"

        def _do(_con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                "SELECT * FROM bot_token_partners WHERE partner_id = %s LIMIT 1 FOR UPDATE",
                (str(partner_id),),
            )
            partner = dict(cur.fetchone() or {})
            if not partner:
                raise BotTokenLicenseError("bot_token_partner_not_found")
            if str(partner.get("status") or "").strip().lower() != "active":
                raise BotTokenLicenseError("bot_token_partner_locked")
            if not self._partner_allows_bot(partner, package):
                raise BotTokenLicenseError("bot_token_partner_bot_not_allowed")
            if not self._partner_allows_duration(partner, duration):
                raise BotTokenLicenseError("bot_token_partner_duration_not_allowed")

            cur.execute(
                """
                INSERT INTO bot_access_tokens (
                    token_id, token_hash, partner_id, bot_code, duration_days,
                    status, issued_by_telegram_id, issued_to_note,
                    redeem_expires_at, metadata_json, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, 'issued', %s, %s, %s, %s::jsonb, NOW(), NOW())
                RETURNING token_id, partner_id, bot_code, duration_days, status, issued_at, redeem_expires_at
                """,
                (
                    token_id,
                    token_hash,
                    str(partner_id),
                    package.code,
                    duration,
                    str(issued_by_telegram_id).strip() if issued_by_telegram_id else None,
                    str(issued_to_note or "").strip() or None,
                    redeem_expires_at,
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
            row = dict(cur.fetchone() or {})
            row["raw_token"] = raw_token
            row["bot_name"] = package.name
            row["package_path"] = package.package_path
            return row

        return self.store._with_retry_locked(_do, tries=1)

    def expire_due_entitlements(self, *, limit: int = 100) -> list[dict[str, Any]]:
        self.ensure_schema()
        batch_size = max(1, min(int(limit or 100), 1000))

        def _do(_con: Any, cur: Any) -> list[dict[str, Any]]:
            cur.execute(
                """
                WITH due AS (
                    SELECT id
                    FROM bot_token_entitlements
                    WHERE status = 'active'
                      AND expires_at <= NOW()
                    ORDER BY expires_at ASC, id ASC
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE bot_token_entitlements e
                SET status = 'expired',
                    stop_reason = COALESCE(stop_reason, 'bot_token_expired'),
                    metadata_json = COALESCE(metadata_json, '{}'::jsonb) || %s::jsonb,
                    updated_at = NOW()
                FROM due
                WHERE e.id = due.id
                RETURNING e.*
                """,
                (
                    batch_size,
                    json.dumps({"expired_by": "backend_reconciler", "expired_at": _utc_now().isoformat()}),
                ),
            )
            return [_format_entitlement(dict(row)) for row in cur.fetchall()]

        return self.store._with_retry_locked(_do, tries=1)

    def list_expired_deployments_needing_stop(self, *, limit: int = 100) -> list[dict[str, Any]]:
        self.ensure_schema()
        batch_size = max(1, min(int(limit or 100), 1000))

        def _do(_con: Any, cur: Any) -> list[dict[str, Any]]:
            cur.execute(
                """
                SELECT e.*
                FROM bot_token_entitlements e
                JOIN bot_deployments d ON d.id = e.deployment_id
                WHERE e.status = 'expired'
                  AND e.deployment_id IS NOT NULL
                  AND e.stop_command_id IS NULL
                  AND e.expires_at <= NOW()
                  AND LOWER(COALESCE(d.desired_state, '')) <> 'stopped'
                  AND LOWER(COALESCE(d.status, '')) NOT IN (
                      'stopped',
                      'failed',
                      'blocked',
                      'cancelled',
                      'deleted'
                  )
                ORDER BY e.expires_at ASC, e.id ASC
                LIMIT %s
                """,
                (batch_size,),
            )
            return [_format_entitlement(dict(row)) for row in cur.fetchall()]

        return self.store._with_retry_read(_do, tries=1)

    def record_entitlement_stop_requested(
        self,
        *,
        entitlement_id: str,
        stop_command_id: Optional[str],
        reason: str = "bot_token_expired",
    ) -> None:
        self.ensure_schema()

        def _do(_con: Any, cur: Any) -> None:
            cur.execute(
                """
                UPDATE bot_token_entitlements
                SET stop_command_id = COALESCE(%s, stop_command_id),
                    stopped_at = COALESCE(stopped_at, NOW()),
                    stop_reason = %s,
                    updated_at = NOW()
                WHERE entitlement_id = %s
                """,
                (str(stop_command_id).strip() if stop_command_id else None, str(reason or "bot_token_expired"), str(entitlement_id)),
            )

        self.store._with_retry_locked(_do, tries=1)

    def revoke_token(
        self,
        *,
        token_id: str,
        revoked_by_telegram_id: Optional[str] = None,
        reason: str = "partner_revoke",
        expected_partner_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Revoke a product activation token and any active entitlements it created.

        This method only mutates license state. The caller is responsible for
        dispatching STOP_BOT for returned entitlements that are bound to a
        running deployment.
        """

        self.ensure_schema()
        token_id_s = str(token_id or "").strip()
        if not token_id_s:
            raise BotTokenLicenseError("bot_token_required")
        expected_partner_id_s = str(expected_partner_id or "").strip()
        reason_s = str(reason or "partner_revoke").strip()[:200]
        revoked_by_s = str(revoked_by_telegram_id).strip() if revoked_by_telegram_id else None
        metadata_patch = {
            "revoked_by": "token_bot",
            "revoked_reason": reason_s,
            "revoked_at": _utc_now().isoformat(),
        }

        def _do(_con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                """
                SELECT *
                FROM bot_access_tokens
                WHERE token_id = %s
                LIMIT 1
                FOR UPDATE
                """,
                (token_id_s,),
            )
            token = dict(cur.fetchone() or {})
            if not token:
                raise BotTokenLicenseError("bot_token_not_found")
            if expected_partner_id_s and str(token.get("partner_id") or "") != expected_partner_id_s:
                raise BotTokenLicenseError("bot_token_partner_mismatch")

            cur.execute(
                """
                UPDATE bot_access_tokens
                SET status = 'revoked',
                    revoked_at = COALESCE(revoked_at, NOW()),
                    revoked_by_telegram_id = COALESCE(%s, revoked_by_telegram_id),
                    revoke_reason = COALESCE(%s, revoke_reason),
                    metadata_json = COALESCE(metadata_json, '{}'::jsonb) || %s::jsonb,
                    updated_at = NOW()
                WHERE token_id = %s
                  AND status <> 'revoked'
                """,
                (
                    revoked_by_s,
                    reason_s,
                    json.dumps(metadata_patch, ensure_ascii=False),
                    token_id_s,
                ),
            )

            cur.execute(
                """
                UPDATE bot_token_entitlements
                SET status = 'revoked',
                    stopped_at = COALESCE(stopped_at, NOW()),
                    stop_reason = COALESCE(stop_reason, %s),
                    metadata_json = COALESCE(metadata_json, '{}'::jsonb) || %s::jsonb,
                    updated_at = NOW()
                WHERE token_id = %s
                  AND status = 'active'
                RETURNING *
                """,
                (
                    reason_s,
                    json.dumps(metadata_patch, ensure_ascii=False),
                    token_id_s,
                ),
            )
            entitlements = [_format_entitlement(dict(row)) for row in cur.fetchall()]
            return {
                "token_id": token_id_s,
                "status": "revoked",
                "revoked_entitlements": entitlements,
            }

        return self.store._with_retry_locked(_do, tries=1)

    def list_partner_tokens(
        self,
        *,
        partner_id: str,
        scope: str = "all",
        query: Optional[str] = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        """Return partner-facing token report with real activation/runtime state."""

        self.ensure_schema()
        partner_id_s = str(partner_id or "").strip()
        if not partner_id_s:
            raise BotTokenLicenseError("bot_token_partner_required")
        scope_s = str(scope or "all").strip().lower()
        if scope_s not in {"all", "month"}:
            scope_s = "all"
        query_s = str(query or "").strip()
        limit_i = max(1, min(int(limit or 500), 5000))
        now = _utc_now()
        period_start, period_end = _billing_period_bounds(scope_s, now=now)

        def _do(_con: Any, cur: Any) -> dict[str, Any]:
            where = ["t.partner_id = %s"]
            params: list[Any] = [partner_id_s]
            if scope_s == "month":
                where.append(
                    """
                    (
                        (t.issued_at >= %s AND t.issued_at <= %s)
                        OR EXISTS (
                            SELECT 1
                            FROM bot_token_entitlements ee
                            WHERE ee.token_id = t.token_id
                              AND ee.starts_at < %s
                              AND ee.expires_at > %s
                        )
                        OR (t.revoked_at IS NOT NULL AND t.revoked_at >= %s AND t.revoked_at <= %s)
                    )
                    """
                )
                params.extend([period_start, period_end, period_end, period_start, period_start, period_end])
            if query_s:
                needle = f"%{query_s}%"
                where.append(
                    """
                    (
                        t.token_id ILIKE %s
                        OR COALESCE(t.issued_to_note, '') ILIKE %s
                        OR t.bot_code ILIKE %s
                    )
                    """
                )
                params.extend([needle, needle, needle])
            params.append(limit_i)
            cur.execute(
                f"""
                SELECT
                    t.*,
                    e.entitlement_id,
                    e.status AS entitlement_status,
                    e.starts_at AS entitlement_starts_at,
                    e.expires_at AS entitlement_expires_at,
                    e.stopped_at AS entitlement_stopped_at,
                    e.deployment_id AS entitlement_deployment_id,
                    d.status AS deployment_status,
                    d.desired_state AS deployment_desired_state,
                    d.health_status AS deployment_health_status
                FROM bot_access_tokens t
                LEFT JOIN LATERAL (
                    SELECT *
                    FROM bot_token_entitlements e
                    WHERE e.token_id = t.token_id
                    ORDER BY e.created_at DESC, e.id DESC
                    LIMIT 1
                ) e ON TRUE
                LEFT JOIN bot_deployments d ON d.id = COALESCE(e.deployment_id, t.bound_deployment_id)
                WHERE {" AND ".join(where)}
                ORDER BY t.issued_at DESC, t.id DESC
                LIMIT %s
                """,
                tuple(params),
            )
            items = [
                _format_partner_token_report_row(
                    dict(row),
                    now=now,
                    period_start=period_start,
                    period_end=period_end,
                )
                for row in cur.fetchall()
            ]
            return {
                "partner_id": partner_id_s,
                "scope": scope_s,
                "query": query_s or None,
                "billing_policy": "activated_overlap_by_day",
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "items": items,
                "summary": _partner_token_report_summary(items),
                "generated_at": now.isoformat(),
            }

        return self.store._with_retry_read(_do, tries=1)
