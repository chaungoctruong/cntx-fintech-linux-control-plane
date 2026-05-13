"""Persistent retry cho force-stop khi backend tạm down.

Module độc lập với tg_bot.py. Cấu trúc:
- mark_attempt(): ghi token attempt vào DB sau mỗi call backend (success / fail)
- pending_jtis(): trả danh sách token cần retry (locked + chưa force_stop_at + attempts < MAX)
- retry_tick(): chạy 1 vòng quét + gọi backend cho từng token còn pending
- run_loop(): background loop gọi retry_tick định kỳ

Backoff: exponential 1m → 2m → 5m → 15m → 30m → 1h (cap). Sau MAX_ATTEMPTS,
escalate DM admin + dừng retry (cần human can thiệp).
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any

from .backend_client import BackendClient
from .models import Partner, Token


log = logging.getLogger("token-bot.force_stop_retry")

RETRY_INTERVAL_SEC = 60  # quét mỗi phút
MAX_ATTEMPTS = 12
BACKOFF_SCHEDULE_SEC = [60, 120, 300, 600, 900, 1800, 1800, 3600, 3600, 3600, 3600, 3600]


def _backoff_for_attempt(attempts: int) -> int:
    if attempts <= 0:
        return BACKOFF_SCHEDULE_SEC[0]
    idx = min(attempts - 1, len(BACKOFF_SCHEDULE_SEC) - 1)
    return BACKOFF_SCHEDULE_SEC[idx]


def _pending_query(session, now: datetime) -> list[Token]:
    """Tokens cần retry: locked, chưa force_stop_at, attempts < MAX, đến hạn backoff."""
    rows = (
        session.query(Token)
        .filter(Token.locked_at.isnot(None))
        .filter(Token.force_stop_at.is_(None))
        .filter(Token.force_stop_attempts < MAX_ATTEMPTS)
        .order_by(Token.locked_at.asc())
        .limit(50)
        .all()
    )
    out = []
    for tk in rows:
        if tk.force_stop_last_attempt is None:
            out.append(tk)
            continue
        backoff = _backoff_for_attempt(tk.force_stop_attempts)
        if (now - tk.force_stop_last_attempt) >= timedelta(seconds=backoff):
            out.append(tk)
    return out


async def mark_attempt(
    session_factory,
    *,
    jti: str,
    success: bool,
    error: str | None = None,
) -> None:
    """Cập nhật trạng thái sau mỗi lần thử."""
    def _do():
        with session_factory() as s:
            tk = s.get(Token, jti)
            if not tk:
                return
            tk.force_stop_attempts = (tk.force_stop_attempts or 0) + 1
            tk.force_stop_last_attempt = datetime.utcnow()
            if success:
                tk.force_stop_at = datetime.utcnow()
                tk.force_stop_last_error = None
            else:
                tk.force_stop_last_error = (error or "unknown")[:255]
            s.commit()
    await asyncio.to_thread(_do)


async def retry_tick(app) -> None:
    sf = app.bot_data["session_factory"]
    bc: BackendClient | None = app.bot_data.get("backend_client")
    if bc is None or not bc.enabled:
        return
    now = datetime.utcnow()
    pending = await asyncio.to_thread(lambda: _pending_query_sync(sf, now))
    if not pending:
        return
    log.info("retry_tick pending=%d", len(pending))
    for item in pending:
        jti = item["jti"]
        reason = f"retry:after_attempts={item['attempts']}:original={item['reason']}"
        result = await bc.force_stop(jti=jti, reason=reason)
        if result is None:
            await mark_attempt(
                sf, jti=jti, success=False,
                error="backend_unreachable_or_error",
            )
            continue
        action = result.get("action")
        if action in ("stop", "noop"):
            # noop is fine: bot đã không chạy → coi như đã hoàn tất
            await mark_attempt(sf, jti=jti, success=True)
            log.info("retry_success jti=%s action=%s", jti[:16], action)
            # Nếu attempts >= 3 thì cũng DM partner để biết đã recover
            if item["attempts"] >= 3:
                await _dm_partner_recovered(app, item)
        else:
            await mark_attempt(
                sf, jti=jti, success=False,
                error=f"action={action} note={result.get('note')}",
            )
            log.warning("retry_failed jti=%s action=%s", jti[:16], action)


def _pending_query_sync(session_factory, now: datetime) -> list[dict[str, Any]]:
    """Thread-safe wrapper trả ra dữ liệu sao chép (không giữ ORM object qua thread)."""
    with session_factory() as s:
        rows = _pending_query(s, now)
        out: list[dict[str, Any]] = []
        for tk in rows:
            out.append({
                "jti": tk.jti,
                "attempts": tk.force_stop_attempts or 0,
                "partner_id": tk.partner_id,
                "khach": tk.end_user_username or "?",
                "reason": "lock_loop",
                "partner_tg_id": tk.partner.telegram_user_id if tk.partner else None,
            })
        return out


async def _dm_partner_recovered(app, item: dict) -> None:
    if not item.get("partner_tg_id"):
        return
    try:
        from telegram.constants import ParseMode

        await app.bot.send_message(
            chat_id=item["partner_tg_id"],
            text=(
                f"♻️ <b>Đã khôi phục force-stop</b>\n"
                f"Khách: <b>{item['khach']}</b>\n"
                f"Sau {item['attempts']} lần thử, bot đã được dừng thành công."
            ),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        log.exception("dm_recovered_failed")


async def check_exhausted_attempts(app) -> None:
    """Tìm token đã hết MAX_ATTEMPTS nhưng chưa force_stop_at → DM admin."""
    sf = app.bot_data["session_factory"]
    settings = app.bot_data["settings"]
    admin_ids = settings.admin_id_set()
    if not admin_ids:
        return

    def fetch():
        with sf() as s:
            rows = (
                s.query(Token)
                .filter(Token.locked_at.isnot(None))
                .filter(Token.force_stop_at.is_(None))
                .filter(Token.force_stop_attempts >= MAX_ATTEMPTS)
                .limit(20)
                .all()
            )
            return [
                {
                    "jti": tk.jti,
                    "khach": tk.end_user_username or "?",
                    "partner_id": tk.partner_id,
                    "attempts": tk.force_stop_attempts,
                    "last_error": tk.force_stop_last_error,
                }
                for tk in rows
            ]

    items = await asyncio.to_thread(fetch)
    if not items:
        return
    try:
        from telegram.constants import ParseMode

        lines = ["🆘 <b>Force-stop ĐÃ HẾT retry — cần can thiệp tay</b>"]
        for it in items:
            lines.append(
                f"• <code>{it['jti'][:16]}…</code> khách=<b>{it['khach']}</b> "
                f"partner={it['partner_id']} attempts={it['attempts']}"
                f"\n   last_error: <code>{(it['last_error'] or '')[:120]}</code>"
            )
        text = "\n".join(lines)
        for admin_id in admin_ids:
            try:
                await app.bot.send_message(chat_id=admin_id, text=text, parse_mode=ParseMode.HTML)
            except Exception:
                log.exception("dm_admin_failed admin=%s", admin_id)
    except Exception:
        log.exception("check_exhausted_failed")


async def run_loop(app) -> None:
    log.info("force_stop_retry loop started (interval=%ds, max_attempts=%d)",
             RETRY_INTERVAL_SEC, MAX_ATTEMPTS)
    counter = 0
    while True:
        try:
            await retry_tick(app)
            # Mỗi 10 vòng (~10 min) check exhausted để DM admin
            counter = (counter + 1) % 10
            if counter == 0:
                await check_exhausted_attempts(app)
        except Exception:
            log.exception("retry_tick_unhandled")
        await asyncio.sleep(RETRY_INTERVAL_SEC)
