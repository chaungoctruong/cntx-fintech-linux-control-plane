# -*- coding: utf-8 -*-
""" /ping command."""
from __future__ import annotations

import logging
from telegram import Update
from telegram.ext import ContextTypes

from app.formatters import h

log = logging.getLogger("hubbot")


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return
    chat_id = str(update.effective_chat.id)
    log.warning("👉 /ping nhận được từ ChatID: %s", chat_id)
    await update.message.reply_text(
        f"🏓 <b>Pong!</b>\nChat ID: <code>{h(chat_id)}</code>\n\n"
        "✅ Bot đã nhận tin. Nếu /trangthai không chạy trong Group, hãy gửi trực tiếp lệnh đó trong chat với bot CNTx labs.",
        parse_mode="HTML",
    )
