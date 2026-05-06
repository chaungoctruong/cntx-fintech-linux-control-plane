from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.store import Store


class BotTokenLicenseError(ValueError):
    """Public error code for Mini App bot-token entitlement checks."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_token(raw_token: str) -> str:
    token = str(raw_token or "").strip()
    if not token:
        raise BotTokenLicenseError("bot_token_required")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _norm_identity(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


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


class BotTokenLicenseService:
    """PostgreSQL-backed token entitlement bridge for Mini App MT5 bot access."""

    def __init__(self, store: Store) -> None:
        self.store = store

    def ensure_schema(self) -> None:
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

        self.store._with_retry_locked(_do)

    def _bot_matches(self, token_bot_code: Any, *, bot_name: str, bot_code: Optional[str]) -> bool:
        token_identity = _norm_identity(token_bot_code)
        allowed = {_norm_identity(bot_name), _norm_identity(bot_code)}
        allowed.discard("")
        return bool(token_identity and token_identity in allowed)

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
        token_hash = _hash_token(raw_token)
        account_id = int(account_id)
        user_id = int(user_id)
        telegram_id = str(telegram_id)

        def _do(_con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                "SELECT * FROM bot_access_tokens WHERE token_hash = %s LIMIT 1 FOR UPDATE",
                (token_hash,),
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

            if not self._bot_matches(token.get("bot_code"), bot_name=bot_name, bot_code=bot_code):
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

            entitlement_id = f"ent_{secrets.token_urlsafe(12)}"
            expires_at = checked_at + timedelta(days=duration_days)
            metadata = {
                "source": "miniapp",
                "bot_name": bot_name,
                "bot_code": bot_code,
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
                    token.get("bot_code"),
                    checked_at,
                    expires_at,
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )
            return _format_entitlement(dict(cur.fetchone() or {}))

        return self.store._with_retry_locked(_do, tries=1)

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
                """,
                (
                    int(deployment_id),
                    json.dumps({"deployment_bound_at": _utc_now().isoformat()}, ensure_ascii=False),
                    str(entitlement_id),
                ),
            )
            if int(cur.rowcount or 0) < 1:
                raise BotTokenLicenseError("bot_token_entitlement_not_found")

        self.store._with_retry_locked(_do, tries=1)
