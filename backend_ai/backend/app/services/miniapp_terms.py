from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from app.store import Store

TERMS_VERSION = "miniapp-risk-v1-2026-05"
TERMS_SOURCE = "miniapp"
TERMS_NOT_ACCEPTED_MESSAGE = (
    "Vui lòng đọc và xác nhận Điều khoản sử dụng & Cảnh báo rủi ro trước khi tiếp tục."
)


class MiniappTermsError(ValueError):
    """Public Mini App terms error code."""


def _norm(value: Any, *, max_len: int = 500) -> str:
    return str(value or "").strip()[:max_len]


def _iso(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.isoformat()
    return None


class MiniappTermsConsentService:
    def __init__(self, store: Store) -> None:
        self.store = store

    def ensure_schema(self) -> None:
        def _do(_con: Any, cur: Any) -> None:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS miniapp_terms_consents (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
                    telegram_id TEXT NOT NULL,
                    consent_version TEXT NOT NULL,
                    accepted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    ip_address TEXT NULL,
                    user_agent TEXT NULL,
                    source TEXT NOT NULL DEFAULT 'miniapp',
                    partner_id TEXT NULL,
                    token_id TEXT NULL,
                    checkbox_1 BOOLEAN NOT NULL DEFAULT FALSE,
                    checkbox_2 BOOLEAN NOT NULL DEFAULT FALSE,
                    checkbox_3 BOOLEAN NOT NULL DEFAULT FALSE,
                    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(telegram_id, consent_version)
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_miniapp_terms_consents_user_version
                ON miniapp_terms_consents(user_id, consent_version, accepted_at DESC)
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_miniapp_terms_consents_partner
                ON miniapp_terms_consents(partner_id, token_id)
                WHERE partner_id IS NOT NULL OR token_id IS NOT NULL
                """
            )

        self.store._with_retry_locked(_do)

    def _ensure_user(
        self,
        *,
        telegram_id: str,
        username: Optional[str],
        cur: Any,
    ) -> dict[str, Any]:
        cur.execute(
            """
            INSERT INTO users(telegram_id, username, created_at, updated_at)
            VALUES(%s, %s, NOW(), NOW())
            ON CONFLICT(telegram_id) DO UPDATE SET
                username = COALESCE(EXCLUDED.username, users.username),
                updated_at = NOW()
            RETURNING *
            """,
            (_norm(telegram_id, max_len=100), _norm(username, max_len=200) or None),
        )
        return dict(cur.fetchone() or {})

    def status(self, *, telegram_id: str, username: Optional[str]) -> dict[str, Any]:
        self.ensure_schema()

        def _do(_con: Any, cur: Any) -> dict[str, Any]:
            user = self._ensure_user(telegram_id=telegram_id, username=username, cur=cur)
            cur.execute(
                """
                SELECT *
                FROM miniapp_terms_consents
                WHERE telegram_id = %s
                  AND consent_version = %s
                  AND checkbox_1 = TRUE
                  AND checkbox_2 = TRUE
                  AND checkbox_3 = TRUE
                ORDER BY accepted_at DESC, id DESC
                LIMIT 1
                """,
                (_norm(telegram_id, max_len=100), TERMS_VERSION),
            )
            row = dict(cur.fetchone() or {})
            accepted = bool(row)
            return {
                "accepted": accepted,
                "version": TERMS_VERSION,
                "accepted_at": _iso(row.get("accepted_at")),
                "requires_acceptance": not accepted,
                "user_id": user.get("id"),
            }

        return self.store._with_retry_locked(_do)

    def accept(
        self,
        *,
        telegram_id: str,
        username: Optional[str],
        version: str,
        checkbox_1: bool,
        checkbox_2: bool,
        checkbox_3: bool,
        ip_address: Optional[str],
        user_agent: Optional[str],
        partner_id: Optional[str] = None,
        token_id: Optional[str] = None,
    ) -> dict[str, Any]:
        self.ensure_schema()
        if _norm(version, max_len=100) != TERMS_VERSION:
            raise MiniappTermsError("invalid_terms_version")
        if not (checkbox_1 and checkbox_2 and checkbox_3):
            raise MiniappTermsError("terms_checkboxes_required")

        def _do(_con: Any, cur: Any) -> dict[str, Any]:
            user = self._ensure_user(telegram_id=telegram_id, username=username, cur=cur)
            metadata = {"source": TERMS_SOURCE}
            cur.execute(
                """
                INSERT INTO miniapp_terms_consents(
                    user_id, telegram_id, consent_version, accepted_at,
                    ip_address, user_agent, source, partner_id, token_id,
                    checkbox_1, checkbox_2, checkbox_3, metadata_json,
                    created_at, updated_at
                )
                VALUES(%s, %s, %s, NOW(), %s, %s, %s, %s, %s, TRUE, TRUE, TRUE, %s::jsonb, NOW(), NOW())
                ON CONFLICT(telegram_id, consent_version) DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    accepted_at = NOW(),
                    ip_address = EXCLUDED.ip_address,
                    user_agent = EXCLUDED.user_agent,
                    source = EXCLUDED.source,
                    partner_id = COALESCE(EXCLUDED.partner_id, miniapp_terms_consents.partner_id),
                    token_id = COALESCE(EXCLUDED.token_id, miniapp_terms_consents.token_id),
                    checkbox_1 = TRUE,
                    checkbox_2 = TRUE,
                    checkbox_3 = TRUE,
                    metadata_json = COALESCE(miniapp_terms_consents.metadata_json, '{}'::jsonb) || EXCLUDED.metadata_json,
                    updated_at = NOW()
                RETURNING *
                """,
                (
                    int(user["id"]),
                    _norm(telegram_id, max_len=100),
                    TERMS_VERSION,
                    _norm(ip_address, max_len=120) or None,
                    _norm(user_agent, max_len=500) or None,
                    TERMS_SOURCE,
                    _norm(partner_id, max_len=120) or None,
                    _norm(token_id, max_len=120) or None,
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )
            row = dict(cur.fetchone() or {})
            return {
                "accepted": True,
                "version": TERMS_VERSION,
                "accepted_at": _iso(row.get("accepted_at")),
                "requires_acceptance": False,
            }

        return self.store._with_retry_locked(_do, tries=1)

    def assert_accepted(self, *, telegram_id: str, username: Optional[str]) -> None:
        status = self.status(telegram_id=telegram_id, username=username)
        if status.get("requires_acceptance"):
            raise MiniappTermsError("TERMS_NOT_ACCEPTED")

    def attach_partner_context(
        self,
        *,
        telegram_id: str,
        username: Optional[str],
        partner_id: Optional[str],
        token_id: Optional[str],
    ) -> None:
        partner_id_s = _norm(partner_id, max_len=120)
        token_id_s = _norm(token_id, max_len=120)
        if not partner_id_s and not token_id_s:
            return
        self.ensure_schema()

        def _do(_con: Any, cur: Any) -> None:
            user = self._ensure_user(telegram_id=telegram_id, username=username, cur=cur)
            cur.execute(
                """
                UPDATE miniapp_terms_consents
                SET user_id = %s,
                    partner_id = COALESCE(NULLIF(%s, ''), partner_id),
                    token_id = COALESCE(NULLIF(%s, ''), token_id),
                    metadata_json = COALESCE(metadata_json, '{}'::jsonb) || %s::jsonb,
                    updated_at = NOW()
                WHERE telegram_id = %s
                  AND consent_version = %s
                  AND checkbox_1 = TRUE
                  AND checkbox_2 = TRUE
                  AND checkbox_3 = TRUE
                """,
                (
                    int(user["id"]),
                    partner_id_s,
                    token_id_s,
                    json.dumps(
                        {
                            "last_partner_context": {
                                "partner_id": partner_id_s or None,
                                "token_id": token_id_s or None,
                            }
                        },
                        ensure_ascii=False,
                    ),
                    _norm(telegram_id, max_len=100),
                    TERMS_VERSION,
                ),
            )

        self.store._with_retry_locked(_do, tries=1)
