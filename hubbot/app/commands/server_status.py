# -*- coding: utf-8 -*-
""" /trangthai, /sys server status (dev-only)."""
from __future__ import annotations

import traceback
import logging
from telegram import Bot, Update
from telegram.ext import ContextTypes
import psutil

from app.config import DEV_CHAT_ID, SYSTEM_BOT_TOKEN
from app.formatters import h
from ops_telegram_alerts import schedule_error_alert

log = logging.getLogger("hubbot")


async def cmd_server_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return

    chat_id = str(update.effective_chat.id)
    log.info("--- DEBUG: Chat ID thực tế: %s | Đợi ID: %s ---", chat_id, DEV_CHAT_ID)

    if not DEV_CHAT_ID or chat_id != DEV_CHAT_ID:
        await update.message.reply_text(
            f"❌ <b>Sai ID bảo mật!</b> ID chat hiện tại: <code>{h(chat_id)}</code> (cần: {DEV_CHAT_ID})\n\n"
            "💡 Trong Group có nhiều bot, hãy gửi trực tiếp lệnh <b>/trangthai</b> trong chat với bot CNTx labs.",
            parse_mode="HTML",
        )
        return

    try:
        cpu = psutil.cpu_percent()
        ram = psutil.virtual_memory()
        cpu_ok = cpu < 90
        ram_ok = ram.percent < 92
        if cpu_ok and ram_ok:
            trang_thai = "🟢 <b>Trạng thái: Ổn định</b>"
        else:
            parts = []
            if not cpu_ok:
                parts.append("CPU cao")
            if not ram_ok:
                parts.append("RAM cao")
            trang_thai = f"🟡 <b>Trạng thái: Cảnh báo</b> ({', '.join(parts)})"

        msg = (
            f"📊 <b>BÁO CÁO HỆ THỐNG CNTX LABS LINUX SERVER</b>\n\n"
            f"🖥 <b>CPU:</b> <code>{cpu}%</code>\n"
            f"🧠 <b>RAM:</b> <code>{ram.percent}%</code>\n"
            f"{trang_thai}\n"
            f"➖➖➖➖➖➖➖➖➖➖"
        )
        if SYSTEM_BOT_TOKEN:
            try:
                system_bot = Bot(token=SYSTEM_BOT_TOKEN)
                await system_bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
            except Exception as send_err:
                log.warning("System bot send failed, fallback to main bot: %s", send_err)
                await update.message.reply_text(msg, parse_mode="HTML")
        else:
            await update.message.reply_text(msg, parse_mode="HTML")
    except Exception as e:
        err_trace = traceback.format_exc()
        schedule_error_alert(
            area="Lệnh trạng thái",
            summary="Không lấy được trạng thái server từ Telegram.",
            exc=e,
            user_id=chat_id,
            impact="Admin chưa xem được báo cáo nhanh trong chat.",
            action="Kiểm tra process Hubbot và quyền đọc tài nguyên hệ thống.",
            alert_key=f"hubbot_server_status:{type(e).__name__}",
            cooldown_sec=300,
        )
        log.exception("cmd_server_status failed: %s", e)
        try:
            await update.message.reply_text(
                f"❌ <b>Lỗi khi kiểm tra:</b>\n<pre>{h(err_trace[-3000:])}</pre>",
                parse_mode="HTML",
            )
        except Exception:
            pass
