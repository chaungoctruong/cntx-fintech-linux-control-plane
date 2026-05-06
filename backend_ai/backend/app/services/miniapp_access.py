from __future__ import annotations

from typing import Any

from app.settings import settings

FULL_ACCESS_ENTITLEMENT_ID = "miniapp_full_access"
FULL_ACCESS_BOT_CODE = "*"


def _split_telegram_ids(raw: Any) -> set[str]:
    normalized = str(raw or "").strip().replace(";", ",").replace("\n", ",").replace(" ", ",")
    return {item.strip() for item in normalized.split(",") if item.strip()}


def miniapp_full_access_telegram_ids() -> set[str]:
    ids = _split_telegram_ids(getattr(settings, "MINIAPP_FULL_ACCESS_TELEGRAM_IDS", ""))
    ids.update(_split_telegram_ids(getattr(settings, "ADMIN_TELEGRAM_IDS", "")))
    ids.update(_split_telegram_ids(getattr(settings, "DEV_CHAT_ID", "")))
    return ids


def has_miniapp_full_access(telegram_id: Any) -> bool:
    value = str(telegram_id or "").strip()
    return bool(value and value in miniapp_full_access_telegram_ids())


def build_full_access_entitlement(*, telegram_id: str, user_id: int, account_id: int) -> dict[str, Any]:
    return {
        "entitlement_id": FULL_ACCESS_ENTITLEMENT_ID,
        "token_id": "",
        "partner_id": "miniapp_full_access",
        "telegram_id": str(telegram_id),
        "user_id": int(user_id),
        "account_id": int(account_id),
        "deployment_id": None,
        "bot_code": FULL_ACCESS_BOT_CODE,
        "status": "active",
        "starts_at": None,
        "expires_at": None,
        "stop_command_id": None,
        "stop_reason": None,
    }
