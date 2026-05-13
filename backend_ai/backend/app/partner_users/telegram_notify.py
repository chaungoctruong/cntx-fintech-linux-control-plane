"""Gửi DM Telegram cho end-user qua bot hubot (TELEGRAM_BOT_TOKEN).

End-user đã /start hubot từ trước (khi đăng ký MT5 vào Spider AI) nên có chat
với bot. Khi token hết hạn/bị thu hồi/được gia hạn → DM họ để họ biết.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.settings import settings


log = logging.getLogger("partner-user.telegram_notify")


def _bot_token() -> str:
    return (getattr(settings, "TELEGRAM_BOT_TOKEN", "") or "").strip()


async def send_dm(telegram_id: int, text: str, *, parse_mode: str = "HTML") -> bool:
    """Gửi 1 message tới chat của user. Fail-soft: log + return False, không raise."""
    token = _bot_token()
    if not token:
        log.warning("TELEGRAM_BOT_TOKEN chưa cấu hình — skip DM tg=%s", telegram_id)
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": int(telegram_id),
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.post(url, json=payload)
    except Exception:
        log.exception("send_dm_http_failed tg=%s", telegram_id)
        return False
    if r.status_code != 200:
        log.error("send_dm status=%s tg=%s body=%s", r.status_code, telegram_id, r.text[:200])
        return False
    log.info("send_dm ok tg=%s len=%d", telegram_id, len(text))
    return True


# ────────────── Pre-built messages ──────────────

def _classify_reason(reason: str) -> str:
    r = (reason or "").lower()
    if "token_expired" in r:
        return "expired"
    if "renewed" in r:
        return "renewed"
    if "partner_revoke" in r:
        return "partner_revoke"
    if "admin_revoke_grant" in r:
        return "admin_revoke_grant"
    return "other"


def build_force_stop_message(*, end_user_label: str | None, bot_id: str, reason: str) -> str:
    kind = _classify_reason(reason)
    you = f"<b>{end_user_label}</b>" if end_user_label else "Bạn"
    bot_html = f"<code>{bot_id}</code>"

    if kind == "expired":
        title = "⌛ <b>Token đã hết hạn</b>"
        body = (
            f"Token bot {bot_html} cấp cho {you} đã hết hạn.\n"
            f"Bot trên MT5 đã được <b>tự tắt</b> để bảo vệ tài khoản.\n\n"
            f"Vui lòng liên hệ <b>đối tác</b> để gia hạn."
        )
    elif kind == "renewed":
        title = "♻️ <b>Token đã được gia hạn</b>"
        body = (
            f"Đối tác đã gia hạn token bot {bot_html} cho {you}.\n"
            f"Token cũ đã <b>không còn hiệu lực</b> — bot tạm dừng.\n\n"
            f"Vui lòng lấy <b>token mới</b> từ đối tác, dán vào ứng dụng để bot chạy lại."
        )
    elif kind == "partner_revoke":
        title = "🚫 <b>Token đã bị thu hồi</b>"
        body = (
            f"Đối tác đã thu hồi token bot {bot_html} của {you}.\n"
            f"Bot đã được <b>tự tắt</b>.\n\n"
            f"Vui lòng liên hệ đối tác nếu cần biết lý do."
        )
    elif kind == "admin_revoke_grant":
        title = "⛔ <b>Quyền sử dụng bot bị thu hồi</b>"
        body = (
            f"Quyền sử dụng bot {bot_html} của đối tác đã bị thu hồi bởi quản trị viên.\n"
            f"Bot của {you} đã được <b>tự tắt</b>.\n\n"
            f"Liên hệ đối tác để biết chi tiết."
        )
    else:
        title = "🔒 <b>Bot đã tự tắt</b>"
        body = (
            f"Bot {bot_html} của {you} vừa được tự tắt.\n"
            f"Liên hệ đối tác nếu có thắc mắc."
        )
    return f"{title}\n\n{body}"
