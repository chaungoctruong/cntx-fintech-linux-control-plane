# -*- coding: utf-8 -*-
"""Message handler: claim token, AI chat."""
from __future__ import annotations

import time
from telegram import Update
from telegram.ext import ContextTypes

from app.config import AI_ENABLED, AI_COOLDOWN_SEC
from app.state import st
from app.api.client import api_json
from app.api.ai_chat import backend_ai_chat
from app.api import message_is_duplicate
from app.keyboards import main_menu_keyboard
from app.formatters import extract_id, safe_reply_plain_text

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    uid = update.effective_user.id
    message_id = getattr(update.message, "message_id", None)
    s = st(context)
    text = (update.message.text or "").strip()
    wad = getattr(update.message, "web_app_data", None)
    raw_data = wad.data if wad else text
    pid = extract_id(raw_data)

    if message_is_duplicate(uid, message_id, text):
        return

    if pid or text.startswith("tok_"):
        if text.startswith("tok_"):
            r = await api_json("POST", "/miniapp/claim", json_body={"telegram_id": str(uid), "token": text})
            pid = extract_id(r)
        if pid:
            s.profile_id = pid
            await update.message.reply_text(
                "🚀 <b>CNTx labs thông báo:</b>\n\n"
                "Tài khoản MT5 của bạn đã lên sóng! Mọi thiết lập đã sẵn sàng, chúc bạn một ngày giao dịch bùng nổ.\n\n"
                "👉 Hãy bấm /start rồi mở <b>MINI APP QUẢN LÝ BOT</b> để bật bot và quản lý trạng thái trực tiếp trên web mini app.",
                parse_mode="HTML",
                reply_markup=main_menu_keyboard(uid),
            )
        else:
            await update.message.reply_text(
                "⚠️ <b>Có chút trục trặc khi kết nối MT5.</b> Token sai hoặc đã hết hạn. Vui lòng mở lại nút Kết nối để tạo phiên mới nhé!",
                parse_mode="HTML",
            )
        return

    if AI_ENABLED and text and not text.startswith("/"):
        now = time.time()
        if (now - s.last_ai_ts) < AI_COOLDOWN_SEC:
            return
        s.last_ai_ts = now
        reply = await backend_ai_chat(text, uid)
        await safe_reply_plain_text(update.message, reply)
        return
