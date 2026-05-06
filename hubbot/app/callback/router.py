# -*- coding: utf-8 -*-
"""Lean callback router for the current Mini App-first hubbot flow."""
from __future__ import annotations

import logging
import time

from telegram import Update
from telegram.ext import ContextTypes

from app.api.client import callback_is_duplicate
from app.config import BOT_BUTTON_COOLDOWN_SEC
from app.formatters import safe_edit_text
from app.keyboards import main_menu_keyboard
from app.state import st

log = logging.getLogger("hubbot")

_LEGACY_CONTROL_PREFIXES = (
    "manage:",
    "stat:",
    "on:",
    "off:",
    "off_req:",
    "off_ok:",
    "del_req:",
    "del_conf:",
)


def _extract_profile_id(data: str) -> str:
    parts = str(data or "").split(":", 1)
    if len(parts) != 2:
        return ""
    return str(parts[1] or "").strip()


def _legacy_redirect_message() -> str:
    return (
        "<b>Điều khiển bot đã chuyển sang CNTx labs Mini App</b>\n\n"
        "Các nút quản lý bot trong chat Telegram đã được gỡ để giao diện gọn hơn và ổn định hơn.\n\n"
        "Hãy mở Mini App bằng nút bên dưới để:\n"
        "• xem danh sách tài khoản MT5\n"
        "• bật hoặc tắt bot\n"
        "• theo dõi trạng thái bot theo thời gian thực\n\n"
        "<i>Nếu bạn đang bấm vào một tin nhắn cũ, chỉ cần mở lại Mini App là tiếp tục dùng bình thường.</i>"
    )


def _is_legacy_control_callback(data: str) -> bool:
    data_s = str(data or "")
    return data_s == "list_bots" or data_s.startswith(_LEGACY_CONTROL_PREFIXES)


async def _redirect_legacy_control_to_miniapp(query, uid: int, data: str) -> None:
    try:
        await query.answer("Điều khiển bot đã chuyển sang Mini App.", show_alert=False)
    except Exception:
        pass

    suffix = "\u200b" * ((int(time.time()) % 5) + 1)
    await safe_edit_text(
        query,
        _legacy_redirect_message() + "\n" + suffix,
        reply_markup=main_menu_keyboard(uid),
    )
    log.info(
        "legacy_telegram_control_redirected uid=%s callback_data=%s",
        str(uid or "")[-4:],
        str(data or "")[:64],
    )


async def _show_main_menu(query, uid: int) -> None:
    try:
        await query.answer()
    except Exception:
        pass
    await safe_edit_text(
        query,
        "👋 <b>Trung tâm Quản lý CNTx labs</b>\nVui lòng chọn chức năng bên dưới:",
        reply_markup=main_menu_keyboard(uid),
    )


async def cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.from_user:
        return

    uid = query.from_user.id
    data = query.data or ""

    if data.startswith("retry_start:"):
        pid = _extract_profile_id(data)
        if not pid:
            await safe_edit_text(query, "⚠️ Không tìm thấy profile hợp lệ. Bạn mở lại Mini App giúp mình nhé.")
            return
        data = f"on:{pid}"
    elif data.startswith("retry_stop:"):
        pid = _extract_profile_id(data)
        if not pid:
            await safe_edit_text(query, "⚠️ Không tìm thấy profile hợp lệ. Bạn mở lại Mini App giúp mình nhé.")
            return
        data = f"off_ok:{pid}"

    user_state = st(context)
    if callback_is_duplicate(uid, data):
        try:
            await query.answer("🟡 Yêu cầu vừa được ghi nhận. Bạn chờ thêm vài giây nhé.", show_alert=False)
        except Exception:
            pass
        return

    now = time.time()
    if (now - user_state.last_button_ts) < max(2.0, BOT_BUTTON_COOLDOWN_SEC):
        try:
            await query.answer("🟡 Trạng thái vừa được làm mới.\nBạn kiểm tra lại giúp mình nhé.", show_alert=False)
        except Exception:
            pass
        return
    user_state.last_button_ts = now

    if data == "main_menu":
        await _show_main_menu(query, uid)
        return

    if _is_legacy_control_callback(data):
        await _redirect_legacy_control_to_miniapp(query, uid, data)
        return

    await _redirect_legacy_control_to_miniapp(query, uid, data or "unknown")
