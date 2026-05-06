# -*- coding: utf-8 -*-
"""Ops alert helpers for hubbot runtime."""
from __future__ import annotations

from telegram import Update

from ops_telegram_alerts import (
    configure_telegram_alerts,
    notify_error_sync,
    notify_event_sync,
    schedule_error_alert,
)


def configure_runtime_alerts(*, system_bot_token: str, bot_token: str, dev_chat_id: str) -> None:
    configure_telegram_alerts(
        token=system_bot_token or bot_token,
        chat_id=dev_chat_id,
        service_name="CNTX-HUBBOT",
    )


def _update_preview(update: object) -> tuple[str, str, str]:
    if not isinstance(update, Update):
        return "unknown", "", ""
    try:
        if update.callback_query is not None:
            return (
                "callback_query",
                str(update.callback_query.from_user.id) if update.callback_query.from_user else "",
                str(update.callback_query.data or "")[:120],
            )
        if update.effective_message is not None:
            msg = update.effective_message
            return (
                "message",
                str(update.effective_user.id) if update.effective_user else "",
                str(msg.text or msg.caption or "")[:120],
            )
    except Exception:
        pass
    return "unknown", "", ""


def maybe_send_update_ops_alert(update: object, error: BaseException | None) -> None:
    if error is None:
        return
    error_type = type(error).__name__ or "UnknownError"
    error_text = str(error or "")
    if error_type == "BadRequest" and "message is not modified" in error_text.lower():
        return
    update_kind, user_id, preview = _update_preview(update)
    schedule_error_alert(
        area="Telegram Bot",
        summary="Bot Telegram gặp lỗi khi xử lý tin nhắn.",
        exc=error,
        user_id=user_id or None,
        impact="Một thao tác trong chat có thể chưa hoàn tất.",
        action="Kiểm tra log Hubbot và thử lại thao tác vừa lỗi.",
        detail={
            "update_kind": update_kind,
            "preview": preview or "-",
        },
        alert_key=f"hubbot_update_exception:{error_type}",
        cooldown_sec=300,
    )


def notify_started() -> None:
    notify_event_sync(
        area="Telegram Bot",
        summary="Hubbot đã khởi động.",
        severity="info",
        alert_key="hubbot_started",
        cooldown_sec=300,
    )


def notify_main_crash(exc: BaseException) -> None:
    notify_error_sync(
        area="Telegram Bot",
        summary="Hubbot bị dừng bất thường.",
        exc=exc,
        impact="Người dùng có thể không nhận phản hồi trong chat Telegram.",
        action="Kiểm tra PM2 và đảm bảo chỉ có một Hubbot đang chạy.",
        alert_key=f"hubbot_main_crash:{type(exc).__name__}",
        cooldown_sec=120,
    )
