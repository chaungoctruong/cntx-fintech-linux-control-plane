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
