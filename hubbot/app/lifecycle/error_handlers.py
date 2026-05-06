# -*- coding: utf-8 -*-
"""Telegram update handlers for radar logging and global errors."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from telegram import Update
from telegram.ext import ContextTypes


def build_error_handlers(
    *,
    logger: logging.Logger,
    dbg: Callable[..., None],
    alert_sender: Callable[[object, BaseException | None], None],
) -> tuple[
    Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]],
    Callable[[object, ContextTypes.DEFAULT_TYPE], Awaitable[None]],
]:
    async def raw_message_logger(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """RADAR: log every message, do not block flow."""
        del context
        chat_id = "?"
        text = ""
        try:
            chat_id = str(update.effective_chat.id) if update.effective_chat else "?"
            msg = update.message or update.edited_message
            if msg:
                text = (msg.text or msg.caption or "").strip()[:200]
        except Exception:
            pass
        logger.info("👉 RADAR BẮT ĐƯỢC TIN NHẮN | ChatID: %s | Text: %s", chat_id, text)

    async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error("Exception while handling an update:", exc_info=context.error)
        dbg(
            "telegram update exception captured",
            {
                "error_type": type(context.error).__name__ if context and context.error else "",
                "error_text": str(context.error)[:220] if context and context.error else "",
                "has_update": bool(isinstance(update, Update)),
                "effective_user_id": (
                    str(update.effective_user.id)
                    if isinstance(update, Update) and update.effective_user is not None
                    else ""
                ),
            },
            hypothesis_id="H1_H2_H3",
        )
        alert_sender(update, context.error if context else None)
        if isinstance(update, Update) and update.effective_message:
            try:
                await update.effective_message.reply_text(
                    "⚠️ Hệ thống đang xử lý nhiều yêu cầu, xin vui lòng thử lại sau giây lát."
                )
            except Exception:
                pass

    return raw_message_logger, global_error_handler
