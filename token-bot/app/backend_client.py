import logging
from typing import Any

import httpx


log = logging.getLogger("token-bot.backend_client")


class BackendClient:
    """HTTP client gọi internal endpoint của backend chính.

    Idempotent + soft-fail: backend down hoặc trả lỗi → log + return None,
    KHÔNG raise. Lock loop vẫn tiếp tục mark state ngay cả khi backend offline.
    """

    def __init__(self, base_url: str | None, internal_key: str | None):
        self.base_url = (base_url or "").rstrip("/")
        self.internal_key = (internal_key or "").strip()

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.internal_key)

    async def _post_internal(self, path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        if not self.enabled:
            log.warning("backend_client disabled — skip path=%s", path)
            return None
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=12.0) as c:
                r = await c.post(
                    url,
                    json=payload,
                    headers={"X-Token-Bot-Key": self.internal_key},
                )
        except Exception:
            log.exception("backend_internal_http_failed path=%s", path)
            return None
        if r.status_code >= 400:
            log.error(
                "backend_internal status=%s path=%s body=%s",
                r.status_code,
                path,
                r.text[:300],
            )
            return None
        try:
            return r.json()
        except Exception:
            log.exception("backend_internal_bad_json path=%s", path)
            return None

    async def _get_internal(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if not self.enabled:
            log.warning("backend_client disabled — skip path=%s", path)
            return None
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=12.0) as c:
                r = await c.get(
                    url,
                    params=params or {},
                    headers={"X-Token-Bot-Key": self.internal_key},
                )
        except Exception:
            log.exception("backend_internal_http_failed path=%s", path)
            return None
        if r.status_code >= 400:
            log.error(
                "backend_internal status=%s path=%s body=%s",
                r.status_code,
                path,
                r.text[:300],
            )
            return None
        try:
            return r.json()
        except Exception:
            log.exception("backend_internal_bad_json path=%s", path)
            return None

    async def upsert_product_partner(
        self,
        *,
        partner_id: str,
        display_name: str,
        telegram_id: int | None,
        allowed_bot_codes: list[str],
        created_by_admin_telegram_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Sync local partner/grants into backend product-token tables."""
        return await self._post_internal(
            "/api/v2/token-bot/internal/partners/upsert",
            {
                "partner_id": str(partner_id),
                "partner_code": str(partner_id),
                "display_name": str(display_name or partner_id),
                "telegram_id": str(telegram_id) if telegram_id else None,
                "allowed_bot_codes": list(allowed_bot_codes or []),
                "allowed_duration_days": [1, 3, 7, 30],
                "created_by_admin_telegram_id": (
                    str(created_by_admin_telegram_id) if created_by_admin_telegram_id else None
                ),
                "metadata": {"managed_by": "token_bot"},
            },
        )

    async def issue_activation_token(
        self,
        *,
        partner_id: str,
        bot_code: str,
        duration_days: int,
        issued_by_telegram_id: int | None,
        customer_label: str | None,
    ) -> dict[str, Any] | None:
        """Issue the Mini App product activation code from backend Linux."""
        return await self._post_internal(
            "/api/v2/token-bot/internal/tokens/issue",
            {
                "partner_id": str(partner_id),
                "bot_code": str(bot_code),
                "duration_days": int(duration_days),
                "issued_by_telegram_id": str(issued_by_telegram_id) if issued_by_telegram_id else None,
                "issued_to_note": str(customer_label or "").strip() or None,
                "metadata": {"customer_label": str(customer_label or "").strip()},
            },
        )

    async def revoke_activation_token(
        self,
        *,
        token_id: str,
        partner_id: str | None = None,
        revoked_by_telegram_id: int | None,
        reason: str,
    ) -> dict[str, Any] | None:
        """Revoke a backend product activation code and stop active deployment if any."""
        token_id_s = str(token_id or "").strip()
        if not token_id_s:
            return None
        return await self._post_internal(
            f"/api/v2/token-bot/internal/tokens/{token_id_s}/revoke",
            {
                "partner_id": str(partner_id).strip() if partner_id else None,
                "revoked_by_telegram_id": (
                    str(revoked_by_telegram_id) if revoked_by_telegram_id else None
                ),
                "reason": str(reason or "partner_revoke")[:200],
            },
        )

    async def list_partner_tokens(
        self,
        *,
        partner_id: str,
        scope: str = "all",
        query: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any] | None:
        partner_id_s = str(partner_id or "").strip()
        if not partner_id_s:
            return None
        params: dict[str, Any] = {
            "scope": str(scope or "all"),
            "limit": max(1, min(int(limit or 500), 500)),
        }
        query_s = str(query or "").strip()
        if query_s:
            params["query"] = query_s[:120]
        return await self._get_internal(
            f"/api/v2/token-bot/internal/partners/{partner_id_s}/tokens",
            params=params,
        )

    async def force_stop(self, *, jti: str, reason: str) -> dict[str, Any] | None:
        """Gọi backend force-stop theo JTI. Backend tự lookup account_id từ link."""
        if not self.enabled:
            log.warning(
                "backend_client disabled — bỏ qua force_stop jti=%s reason=%s",
                jti[:16],
                reason,
            )
            return None
        url = f"{self.base_url}/api/v2/partner-user/internal/force-stop"
        try:
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.post(
                    url,
                    json={"jti": jti, "reason": reason},
                    headers={"X-Token-Bot-Key": self.internal_key},
                )
        except Exception:
            log.exception("force_stop_http_failed jti=%s", jti[:16])
            return None
        if r.status_code >= 400:
            log.error(
                "force_stop status=%s jti=%s body=%s",
                r.status_code,
                jti[:16],
                r.text[:300],
            )
            return None
        try:
            data = r.json()
        except Exception:
            log.exception("force_stop_bad_json jti=%s", jti[:16])
            return None
        log.info(
            "force_stop ok jti=%s action=%s note=%s",
            jti[:16],
            data.get("action"),
            data.get("note"),
        )
        return data

    async def transfer_link(self, *, old_jti: str, new_jti: str) -> dict[str, Any] | None:
        """Khi partner gia hạn: copy account link old_jti → new_jti để khách không phải re-link."""
        if not self.enabled:
            log.warning("backend_client disabled — skip transfer_link old=%s", old_jti[:16])
            return None
        url = f"{self.base_url}/api/v2/partner-user/internal/transfer-link"
        try:
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.post(
                    url,
                    json={"old_jti": old_jti, "new_jti": new_jti},
                    headers={"X-Token-Bot-Key": self.internal_key},
                )
        except Exception:
            log.exception("transfer_link_http_failed old=%s", old_jti[:16])
            return None
        if r.status_code >= 400:
            log.error(
                "transfer_link status=%s old=%s body=%s",
                r.status_code, old_jti[:16], r.text[:300],
            )
            return None
        try:
            data = r.json()
        except Exception:
            return None
        log.info(
            "transfer_link result old=%s new=%s transferred=%s account_id=%s",
            old_jti[:16], new_jti[:16],
            data.get("transferred"), data.get("account_id"),
        )
        return data
