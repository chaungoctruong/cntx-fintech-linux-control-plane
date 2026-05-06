# -*- coding: utf-8 -*-
""" /start command."""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.formatters import h
from app.keyboards import main_menu_keyboard


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    uid = update.effective_user.id
    msg = (
        f"<b>CNTx Labs | Bot Control Desk</b>\n"
        f"Xin chào <b>{h(update.effective_user.first_name)}</b>.\n\n"
        "Quản lý bot giao dịch của bạn tại Mini App: kết nối tài khoản, chọn bot, bật/tắt và theo dõi trạng thái vận hành.\n\n"
        "Telegram dùng để nhận thông báo và hỗ trợ nhanh.\n\n"
        "<b>Lưu ý:</b> CNTx Labs cung cấp công cụ công nghệ hỗ trợ vận hành bot, không cam kết lợi nhuận, "
        "không nhận ủy thác đầu tư và không thay mặt người dùng ra quyết định tài chính.\n"
        "Giao dịch tài chính luôn có rủi ro. Người dùng tự chịu trách nhiệm với tài khoản, vốn và quyết định giao dịch của mình."
    )
    await update.message.reply_text(msg, reply_markup=main_menu_keyboard(uid), parse_mode="HTML")
