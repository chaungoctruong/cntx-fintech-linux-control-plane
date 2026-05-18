import asyncio
import base64
import html
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .backend_client import BackendClient
from .bot_registry import BotRegistry
from .config import Settings
from .crypto import BotCipher
from .db import ensure_schema_patches, make_engine, make_session_factory
from . import force_stop_retry
from .models import Base, Partner, PartnerBotGrant, Token
from . import state_mirror
from .token_service import TokenService


log = logging.getLogger("token-bot.tg")


# ───────────────────────── helpers ─────────────────────────

def _role(ctx: ContextTypes.DEFAULT_TYPE, tg_id: int) -> tuple[str, Partner | None]:
    settings: Settings = ctx.application.bot_data["settings"]
    if tg_id in settings.admin_id_set():
        return "admin", None
    sf = ctx.application.bot_data["session_factory"]
    with sf() as s:
        p = s.query(Partner).filter_by(telegram_user_id=tg_id, active=True).first()
    if p:
        return "partner", p
    return "stranger", None


async def _async_role(ctx, tg_id):
    return await asyncio.to_thread(_role, ctx, tg_id)


def _admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("👥 Danh sách đối tác", callback_data="menu:partners")],
            [InlineKeyboardButton("➕ Thêm đối tác", callback_data="menu:add_partner")],
            [InlineKeyboardButton("🤖 Kho bot đã mã hóa", callback_data="menu:bots")],
            [InlineKeyboardButton("🔑 Cấp quyền bot cho đối tác", callback_data="menu:grant")],
            [InlineKeyboardButton("🚫 Hủy quyền", callback_data="menu:revokegrant")],
            [InlineKeyboardButton("📜 Mã đã cấp", callback_data="menu:tokens")],
        ]
    )


def _partner_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🤖 Bot của tôi", callback_data="pmenu:mybots")],
            [InlineKeyboardButton("🎫 Tạo mã cho khách", callback_data="pmenu:issue")],
            [InlineKeyboardButton("🔎 Tra cứu khách / mã", callback_data="pmenu:search")],
            [
                InlineKeyboardButton("♻️ Gia hạn mã", callback_data="pmenu:renew"),
                InlineKeyboardButton("🚫 Khóa bot khách", callback_data="pmenu:lock"),
            ],
            [
                InlineKeyboardButton("📜 Mã đã cấp", callback_data="pmenu:mytokens"),
                InlineKeyboardButton("📊 Báo cáo tháng", callback_data="ptok_sum:month"),
            ],
        ]
    )


def _back_to_admin() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Menu chính", callback_data="menu:home")]]
    )


def _back_to_partner() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Menu", callback_data="pmenu:home")]]
    )


def _fmt_partner_short(p: Partner) -> str:
    return f"{p.name} ({p.id})"


def _h(value) -> str:
    return html.escape(str(value or ""), quote=False)


async def _safe_edit_message_text(q, *args, **kwargs):
    try:
        return await q.edit_message_text(*args, **kwargs)
    except BadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return None
        raise


async def _safe_reply_text(message, *args, **kwargs):
    try:
        return await message.reply_text(*args, **kwargs)
    except BadRequest as exc:
        # Telegram can reject a specific markup/message combination. Keep the
        # command usable by retrying once without buttons.
        if kwargs.get("reply_markup") is not None:
            clean_kwargs = dict(kwargs)
            clean_kwargs.pop("reply_markup", None)
            try:
                return await message.reply_text(*args, **clean_kwargs)
            except BadRequest:
                pass
        log.warning("telegram_reply_bad_request reason=%s", str(exc)[:240])
        return None


def _short_ref(value: str, *, left: int = 10, right: int = 6) -> str:
    raw = str(value or "").strip()
    if len(raw) <= left + right + 1:
        return raw
    return f"{raw[:left]}...{raw[-right:]}"


def _token_billable_days(token: Token) -> int:
    delta = token.expires_at - token.issued_at
    seconds = max(0, int(delta.total_seconds()))
    return max(1, (seconds + 86_399) // 86_400)


def _token_status_label(token: Token, now: datetime) -> str:
    if token.revoked:
        return "đã khóa"
    if token.locked_at is not None:
        return "đã khóa"
    if token.expires_at < now:
        return "hết hạn"
    return "còn hạn"


def _activation_code_message(
    *,
    title: str,
    customer_label: str,
    bot_id: str,
    days: int,
    expires_at: datetime,
    activation_code: str,
    management_ref: str,
    extra_note: str = "",
) -> str:
    note = f"\n{extra_note.strip()}\n" if extra_note.strip() else "\n"
    return (
        f"{title}\n"
        f"Khách: <b>{_h(customer_label)}</b>\n"
        f"Bot: <b>{_h(bot_id)}</b>\n"
        f"Hạn dùng: <b>{int(days)} ngày</b> kể từ khi khách kích hoạt trên ứng dụng CNTxLabs\n"
        f"Mã quản lý: <code>{_h(_short_ref(management_ref))}</code>\n\n"
        f"<b>Mã kích hoạt gửi cho khách</b>\n"
        f"<pre>{html.escape(str(activation_code or ''), quote=False)}</pre>\n"
        f"{note}"
        f"<b>Hướng dẫn gửi khách:</b>\n"
        f"1. Mở ứng dụng CNTxLabs.\n"
        f"2. Dán mã kích hoạt này vào ô mã bot.\n"
        f"3. Kết nối tài khoản rồi bật/tắt bot bình thường.\n\n"
        f"<i>Mã này chỉ nên gửi riêng cho đúng khách. Hết hạn thì hệ thống tự khóa quyền bot.</i>"
    )


def _is_backend_product_token(tk: Token | object) -> bool:
    return str(getattr(tk, "created_by", "") or "").startswith("backend-product:")


async def _partner_allowed_bot_ids(ctx, partner: Partner) -> list[str]:
    sf = ctx.application.bot_data["session_factory"]

    def db_q():
        with sf() as s:
            return [
                g.bot_id
                for g in s.query(PartnerBotGrant)
                .filter_by(partner_id=partner.id, revoked=False)
                .all()
            ]

    return await asyncio.to_thread(db_q)


async def _sync_product_partner(ctx, partner: Partner, allowed_bot_ids: list[str]) -> bool:
    bc: BackendClient | None = ctx.application.bot_data.get("backend_client")
    if bc is None or not bc.enabled:
        return False
    synced = await bc.upsert_product_partner(
        partner_id=partner.id,
        display_name=partner.name,
        telegram_id=partner.telegram_user_id,
        allowed_bot_codes=allowed_bot_ids,
    )
    return synced is not None


async def _backend_partner_token_report(
    ctx,
    partner: Partner,
    *,
    scope: str = "all",
    query: str | None = None,
    limit: int = 500,
):
    bc: BackendClient | None = ctx.application.bot_data.get("backend_client")
    if bc is None or not bc.enabled:
        return None
    allowed_bot_ids = await _partner_allowed_bot_ids(ctx, partner)
    if not await _sync_product_partner(ctx, partner, allowed_bot_ids):
        return None
    return await bc.list_partner_tokens(
        partner_id=partner.id,
        scope=scope,
        query=query,
        limit=limit,
    )


def _backend_status_icon(code: str) -> str:
    return {
        "issued": "🕓",
        "redeemed": "✅",
        "running": "🟢",
        "expired": "⌛",
        "revoked": "🚫",
    }.get(str(code or "").lower(), "•")


def _backend_status_label(item: dict) -> str:
    label = str(item.get("status_label") or "").strip()
    return label or "Chưa rõ trạng thái"


def _backend_short_date(value) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "-"
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.strftime("%d/%m %H:%M")
    except Exception:
        return raw[:16].replace("T", " ")


def _backend_token_counts(summary: dict) -> dict[str, int]:
    counts = dict((summary or {}).get("status_counts") or {})
    return {
        "issued": int(counts.get("issued") or 0),
        "redeemed": int(counts.get("redeemed") or 0),
        "running": int(counts.get("running") or 0),
        "expired": int(counts.get("expired") or 0),
        "revoked": int(counts.get("revoked") or 0),
        "all": int((summary or {}).get("total_tokens") or 0),
    }


def _backend_items_by_filter(items: list[dict], filter_kind: str) -> list[dict]:
    if filter_kind == "active":
        return [
            item for item in items
            if str(item.get("status_code") or "") in {"issued", "redeemed", "running"}
        ]
    if filter_kind == "expired":
        return [item for item in items if str(item.get("status_code") or "") == "expired"]
    if filter_kind == "revoked":
        return [item for item in items if str(item.get("status_code") or "") == "revoked"]
    return list(items)


async def _is_admin(ctx, tg_id) -> bool:
    settings: Settings = ctx.application.bot_data["settings"]
    return tg_id in settings.admin_id_set()


# ───────────────────────── /start, /whoami, /menu, /cancel ─────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    role, partner = await _async_role(ctx, u.id)
    if role == "admin":
        await update.message.reply_text(
            f"👋 Xin chào admin <b>{u.full_name}</b>\n"
            f"ID: <code>{u.id}</code>\n\n"
            f"Chọn chức năng:",
            parse_mode=ParseMode.HTML,
            reply_markup=_admin_menu(),
        )
    elif role == "partner":
        stats = await _async_partner_stats(ctx, partner)
        await update.message.reply_text(
            f"👋 Xin chào đối tác <b>{u.full_name}</b>\n"
            f"Mã đối tác: <code>{partner.id}</code>\n\n"
            f"📊 <b>Tổng quan</b>\n"
            f"  ✅ Mã đang mở: <b>{stats['active']}</b>\n"
            f"  🟢 Khách đang dùng bot: <b>{stats['running']}</b>\n"
            f"  📅 Khách tính phí tháng này: <b>{stats['billable_customers']}</b>\n"
            f"  🚫 Đã khóa: <b>{stats['locked']}</b>\n\n"
            f"Chọn chức năng:",
            parse_mode=ParseMode.HTML,
            reply_markup=_partner_menu(),
        )
    else:
        await update.message.reply_text(
            f"Xin chào <b>{u.full_name}</b>\n"
            f"Telegram ID: <code>{u.id}</code>\n"
            f"Trạng thái: <b>chưa đăng ký</b>\n\n"
            f"Bạn chưa được cấp quyền sử dụng bot này.\n"
            f"Gửi Telegram ID phía trên cho đội vận hành để đăng ký.",
            parse_mode=ParseMode.HTML,
        )


async def cmd_whoami(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await update.message.reply_text(
        f"id=<code>{u.id}</code>\nusername=@{u.username or '-'}\nname={u.full_name}",
        parse_mode=ParseMode.HTML,
    )


async def cmd_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    role, _ = await _async_role(ctx, update.effective_user.id)
    if role == "admin":
        await update.message.reply_text("Menu chính:", reply_markup=_admin_menu())
    elif role == "partner":
        await update.message.reply_text("Menu chính:", reply_markup=_partner_menu())
    else:
        await update.message.reply_text("Bạn chưa có quyền.")


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    role, _ = await _async_role(ctx, update.effective_user.id)
    kb = _admin_menu() if role == "admin" else (_partner_menu() if role == "partner" else None)
    await _safe_reply_text(update.message, "Đã hủy thao tác.", reply_markup=kb)


# ───────────────────────── admin menu callbacks ─────────────────────────

async def cb_admin_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await _is_admin(ctx, update.effective_user.id):
        await _safe_edit_message_text(q, "Chỉ admin mới dùng được chức năng này.")
        return

    action = q.data.split(":", 1)[1]

    if action == "home":
        ctx.user_data.clear()
        await _safe_edit_message_text(q, "Menu chính:", reply_markup=_admin_menu())
        return

    if action == "partners":
        await _show_partners(q, ctx)
        return

    if action == "bots":
        await _show_bots_admin(q, ctx)
        return

    if action == "tokens":
        await _show_tokens_admin(q, ctx)
        return

    if action == "add_partner":
        ctx.user_data["awaiting"] = "add_partner_tg_id"
        await _safe_edit_message_text(q,
            "<b>➕ Thêm đối tác</b>\n\nGửi Telegram ID của đối tác (số nguyên).\n"
            "Gõ /cancel để hủy.",
            parse_mode=ParseMode.HTML,
        )
        return

    if action == "grant":
        await _show_grant_pick_partner(q, ctx)
        return

    if action == "revokegrant":
        await _show_revoke_pick_partner(q, ctx)
        return


async def _show_partners(q, ctx):
    sf = ctx.application.bot_data["session_factory"]

    def db_q():
        with sf() as s:
            partners = s.query(Partner).order_by(Partner.created_at.desc()).all()
            return [
                (
                    p,
                    s.query(PartnerBotGrant)
                    .filter_by(partner_id=p.id, revoked=False)
                    .count(),
                )
                for p in partners
            ]

    rows = await asyncio.to_thread(db_q)
    if not rows:
        await _safe_edit_message_text(q,
            "Chưa có đối tác nào.\nDùng <b>➕ Thêm đối tác</b> để bắt đầu.",
            parse_mode=ParseMode.HTML,
            reply_markup=_back_to_admin(),
        )
        return
    lines = ["<b>👥 Danh sách đối tác</b>"]
    for p, g in rows:
        status = "✅" if p.active else "🚫"
        lines.append(
            f"{status} <code>{p.id}</code> — {p.name}\n"
            f"   Telegram: {p.telegram_user_id or '-'}  · quyền bot: {g}"
        )
    await _safe_edit_message_text(q,
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=_back_to_admin(),
    )


async def _show_bots_admin(q, ctx):
    reg: BotRegistry = ctx.application.bot_data["registry"]
    items = await asyncio.to_thread(reg.list_encrypted)
    if not items:
        await _safe_edit_message_text(q,
            "Chưa có bot nào đã mã hóa để cấp quyền.\n"
            "Hãy mã hóa bot trong thư mục <code>bot-trading</code> trước.",
            parse_mode=ParseMode.HTML,
            reply_markup=_back_to_admin(),
        )
        return
    lines = ["<b>🤖 Kho bot đã mã hóa</b>"]
    for b in items:
        sm = b.get("summary") or {}
        lines.append(
            f"• <code>{b['bot_id']}</code> v{b['version']}\n"
            f"   {sm.get('bot_name', '?')} — {sm.get('owner', '?')}"
        )
    await _safe_edit_message_text(q,
        "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=_back_to_admin()
    )


async def _show_tokens_admin(q, ctx, filter_kind: str = "all"):
    sf = ctx.application.bot_data["session_factory"]
    now = datetime.utcnow()

    def db_q():
        with sf() as s:
            base = s.query(Token).order_by(Token.issued_at.desc())
            if filter_kind == "active":
                return [t for t in base.limit(200).all() if not t.revoked and t.expires_at >= now][:20]
            if filter_kind == "expired":
                return [t for t in base.limit(200).all() if not t.revoked and t.expires_at < now][:20]
            if filter_kind == "revoked":
                return base.filter(Token.revoked == True).limit(20).all()  # noqa: E712
            return base.limit(20).all()

    rows = await asyncio.to_thread(db_q)

    title_map = {
        "all": "Tất cả",
        "active": "✅ Đang còn hạn",
        "expired": "⌛ Hết hạn",
        "revoked": "🚫 Đã khóa",
    }
    header = f"<b>📜 Mã kích hoạt — {title_map.get(filter_kind, 'Tất cả')}</b>"

    if not rows:
        body = f"{header}\n\n<i>Không có mã nào.</i>"
    else:
        lines = [header]
        for tk in rows:
            bids = ",".join(json.loads(tk.bot_ids_json))
            if tk.revoked:
                state = "🚫"
            elif tk.expires_at < now:
                state = "⌛"
            else:
                state = "✅"
            khach = tk.end_user_username or (f"tg:{tk.end_user_telegram_id}" if tk.end_user_telegram_id else "?")
            lines.append(
                f"{state} <b>{khach}</b> · bot={bids}\n"
                f"   đối tác={tk.partner_id} · hết hạn={tk.expires_at:%Y-%m-%d %H:%M}\n"
                f"   mã quản lý=<code>{_short_ref(tk.jti)}</code>"
            )
        body = "\n".join(lines)

    kb = [
        [
            InlineKeyboardButton(
                ("• Tất cả •" if filter_kind == "all" else "Tất cả"),
                callback_data="atok:all",
            ),
            InlineKeyboardButton(
                ("• ✅ •" if filter_kind == "active" else "✅"),
                callback_data="atok:active",
            ),
            InlineKeyboardButton(
                ("• ⌛ •" if filter_kind == "expired" else "⌛"),
                callback_data="atok:expired",
            ),
            InlineKeyboardButton(
                ("• 🚫 •" if filter_kind == "revoked" else "🚫"),
                callback_data="atok:revoked",
            ),
        ],
        [InlineKeyboardButton("⬅️ Menu chính", callback_data="menu:home")],
    ]
    await _safe_edit_message_text(q, body, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))


async def cb_admin_tokens_filter(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await _is_admin(ctx, update.effective_user.id):
        return
    kind = q.data.split(":", 1)[1]
    if kind not in {"all", "active", "expired", "revoked"}:
        kind = "all"
    await _show_tokens_admin(q, ctx, filter_kind=kind)


# ───── Grant flow (admin) ─────

async def _show_grant_pick_partner(q, ctx):
    sf = ctx.application.bot_data["session_factory"]

    def db_q():
        with sf() as s:
            return s.query(Partner).filter_by(active=True).order_by(Partner.created_at.desc()).all()

    partners = await asyncio.to_thread(db_q)
    if not partners:
        await _safe_edit_message_text(q,
            "Chưa có đối tác nào. Thêm đối tác trước.",
            reply_markup=_back_to_admin(),
        )
        return
    kb = [
        [InlineKeyboardButton(_fmt_partner_short(p), callback_data=f"grant_p:{p.id}")]
        for p in partners[:20]
    ]
    kb.append([InlineKeyboardButton("⬅️ Quay lại", callback_data="menu:home")])
    await _safe_edit_message_text(q,
        "<b>🔑 Cấp quyền bot</b>\n\nBước 1/2: Chọn đối tác cần cấp quyền:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def cb_grant_partner(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await _is_admin(ctx, update.effective_user.id):
        return
    partner_id = q.data.split(":", 1)[1]
    ctx.user_data["grant_partner_id"] = partner_id

    reg: BotRegistry = ctx.application.bot_data["registry"]
    items = await asyncio.to_thread(reg.list_encrypted)
    if not items:
        await _safe_edit_message_text(q, "Chưa có bot nào.", reply_markup=_back_to_admin())
        return
    kb = [
        [
            InlineKeyboardButton(
                f"{b['bot_id']} v{b['version']}",
                callback_data=f"grant_b:{b['bot_id']}",
            )
        ]
        for b in items
    ]
    kb.append([InlineKeyboardButton("⬅️ Quay lại", callback_data="menu:home")])
    await _safe_edit_message_text(q,
        f"<b>🔑 Cấp quyền cho <code>{partner_id}</code></b>\n\n"
        f"Bước 2/2: Chọn bot:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def cb_grant_bot(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await _is_admin(ctx, update.effective_user.id):
        return
    bot_id = q.data.split(":", 1)[1]
    partner_id = ctx.user_data.pop("grant_partner_id", None)
    if not partner_id:
        await _safe_edit_message_text(q, "Phiên thao tác đã hết hạn. Thử lại nhé.", reply_markup=_back_to_admin())
        return

    sf = ctx.application.bot_data["session_factory"]

    def do_grant():
        with sf() as s:
            partner = s.get(Partner, partner_id)
            if not partner:
                return "❌ Không tìm thấy đối tác."
            grant = (
                s.query(PartnerBotGrant)
                .filter_by(partner_id=partner_id, bot_id=bot_id)
                .first()
            )
            if grant and not grant.revoked:
                return (
                    f"ℹ️ Đối tác <code>{partner_id}</code> đã có quyền dùng "
                    f"<code>{bot_id}</code> từ trước."
                )
            if grant:
                grant.revoked = False
                grant.revoked_at = None
                msg = "♻️ Đã kích hoạt lại quyền"
            else:
                s.add(PartnerBotGrant(partner_id=partner_id, bot_id=bot_id))
                msg = "✅ Đã cấp quyền"
            s.commit()
            return (
                f"{msg}: <code>{partner_id}</code> ({partner.name}) → "
                f"<code>{bot_id}</code>"
            )

    text = await asyncio.to_thread(do_grant)
    await _safe_edit_message_text(q,
        text, parse_mode=ParseMode.HTML, reply_markup=_back_to_admin()
    )


# ───── Revoke grant flow (admin) ─────

async def _show_revoke_pick_partner(q, ctx):
    sf = ctx.application.bot_data["session_factory"]

    def db_q():
        with sf() as s:
            grants = (
                s.query(PartnerBotGrant)
                .filter_by(revoked=False)
                .all()
            )
            partner_ids = sorted({g.partner_id for g in grants})
            return [s.get(Partner, pid) for pid in partner_ids]

    partners = await asyncio.to_thread(db_q)
    partners = [p for p in partners if p]
    if not partners:
        await _safe_edit_message_text(q,
            "Không có quyền bot nào đang hoạt động.", reply_markup=_back_to_admin()
        )
        return
    kb = [
        [InlineKeyboardButton(_fmt_partner_short(p), callback_data=f"rg_p:{p.id}")]
        for p in partners[:20]
    ]
    kb.append([InlineKeyboardButton("⬅️ Quay lại", callback_data="menu:home")])
    await _safe_edit_message_text(q,
        "<b>🚫 Hủy quyền</b>\n\nChọn đối tác:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def cb_revoke_pick_bot(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await _is_admin(ctx, update.effective_user.id):
        return
    partner_id = q.data.split(":", 1)[1]
    ctx.user_data["rg_partner_id"] = partner_id
    sf = ctx.application.bot_data["session_factory"]

    def db_q():
        with sf() as s:
            return (
                s.query(PartnerBotGrant)
                .filter_by(partner_id=partner_id, revoked=False)
                .all()
            )

    grants = await asyncio.to_thread(db_q)
    if not grants:
        await _safe_edit_message_text(q,
            "Đối tác này chưa có quyền bot nào.", reply_markup=_back_to_admin()
        )
        return
    kb = [
        [InlineKeyboardButton(g.bot_id, callback_data=f"rg_b:{g.bot_id}")]
        for g in grants
    ]
    kb.append([InlineKeyboardButton("⬅️ Quay lại", callback_data="menu:home")])
    await _safe_edit_message_text(q,
        f"<b>🚫 Hủy quyền của <code>{partner_id}</code></b>\n\nChọn bot cần hủy:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def cb_revoke_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await _is_admin(ctx, update.effective_user.id):
        return
    bot_id = q.data.split(":", 1)[1]
    partner_id = ctx.user_data.pop("rg_partner_id", None)
    if not partner_id:
        await _safe_edit_message_text(q, "Phiên thao tác đã hết hạn. Thử lại nhé.", reply_markup=_back_to_admin())
        return
    sf = ctx.application.bot_data["session_factory"]

    def do():
        with sf() as s:
            grant = (
                s.query(PartnerBotGrant)
                .filter_by(partner_id=partner_id, bot_id=bot_id, revoked=False)
                .first()
            )
            if not grant:
                return "Không có quyền bot đang hoạt động để hủy.", []
            grant.revoked = True
            grant.revoked_at = datetime.utcnow()
            tokens = (
                s.query(Token)
                .filter_by(partner_id=partner_id, revoked=False)
                .all()
            )
            snaps = []
            for tk in tokens:
                if bot_id in json.loads(tk.bot_ids_json):
                    tk.revoked = True
                    tk.revoked_at = datetime.utcnow()
                    snaps.append({
                        "jti": tk.jti,
                        "partner_id": tk.partner_id,
                        "bot_id": bot_id,
                        "account_id": tk.account_id,
                        "end_user_label": tk.end_user_username,
                        "expires_at": tk.expires_at,
                        "created_by": tk.created_by,
                    })
            s.commit()
            return (
                f"✅ Đã hủy quyền <code>{partner_id}</code> dùng "
                f"<code>{bot_id}</code> và khóa {len(snaps)} mã liên quan."
            ), snaps

    text, snaps = await asyncio.to_thread(do)
    rc = ctx.application.bot_data.get("redis_client")
    bc: BackendClient | None = ctx.application.bot_data.get("backend_client")
    grace = ctx.application.bot_data["settings"].redis_state_grace_sec
    for snap in snaps:
        if str(snap.get("created_by") or "").startswith("backend-product:"):
            if bc:
                await bc.revoke_activation_token(
                    token_id=snap["jti"],
                    partner_id=snap["partner_id"],
                    revoked_by_telegram_id=update.effective_user.id if update.effective_user else None,
                    reason=f"admin_revoke_grant:bot={snap['bot_id']}",
                )
            continue
        state_mirror.mirror(
            rc, jti=snap["jti"], state=state_mirror.STATE_REVOKED,
            partner_id=snap["partner_id"], bot_id=snap["bot_id"],
            account_id=snap["account_id"], end_user_label=snap["end_user_label"],
            expires_at=snap["expires_at"], grace_sec=grace,
        )
        if bc:
            await bc.force_stop(
                jti=snap["jti"],
                reason=f"admin_revoke_grant:bot={snap['bot_id']}",
            )
    await _safe_edit_message_text(q,
        text, parse_mode=ParseMode.HTML, reply_markup=_back_to_admin()
    )


# ───────────────────────── partner menu callbacks ─────────────────────────

async def cb_partner_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    role, partner = await _async_role(ctx, update.effective_user.id)
    if role != "partner":
        await _safe_edit_message_text(q, "Chỉ đối tác đã đăng ký mới dùng được chức năng này.")
        return
    action = q.data.split(":", 1)[1]

    if action == "home":
        ctx.user_data.clear()
        await _safe_edit_message_text(q, "Menu:", reply_markup=_partner_menu())
        return

    if action == "mybots":
        await _show_partner_bots(q, ctx, partner)
        return

    if action == "mytokens":
        await _show_partner_tokens(q, ctx, partner, filter_kind="active")
        return

    if action == "lock":
        await _show_partner_lock_tokens(q, ctx, partner)
        return

    if action == "search":
        ctx.user_data["awaiting"] = "partner_search_query"
        await _safe_edit_message_text(
            q,
            "<b>🔎 Tra cứu khách / mã</b>\n\n"
            "Nhập tên khách hoặc mã quản lý để tra cứu.\n"
            "Bot sẽ trả về bot, hạn dùng, trạng thái kích hoạt và nút thao tác nhanh.\n\n"
            "/cancel để hủy.",
            parse_mode=ParseMode.HTML,
        )
        return

    if action == "issue":
        ctx.user_data["awaiting"] = "issue_user_label"
        ctx.user_data["issue_partner_id"] = partner.id
        await _safe_edit_message_text(q,
            "<b>🎫 Tạo mã kích hoạt cho khách</b>\n\n"
            "Bước 1/3: Nhập tên dễ nhớ để bạn quản lý khách.\n"
            "Ví dụ: <i>Anh Tuấn</i>, <i>Khách-001</i>, <i>hung</i>.\n\n"
            "Tên này chỉ để bạn nhận biết trong danh sách, khách sẽ tự kết nối tài khoản trên ứng dụng.\n"
            "/cancel để hủy.",
            parse_mode=ParseMode.HTML,
        )
        return

    if action == "renew":
        await _show_renew_pick_token(q, ctx, partner)
        return


async def _show_partner_bots(q, ctx, partner: Partner):
    sf = ctx.application.bot_data["session_factory"]
    reg: BotRegistry = ctx.application.bot_data["registry"]

    def db_q():
        with sf() as s:
            grants = (
                s.query(PartnerBotGrant)
                .filter_by(partner_id=partner.id, revoked=False)
                .all()
            )
            return [g.bot_id for g in grants]

    bot_ids = await asyncio.to_thread(db_q)
    if not bot_ids:
        await _safe_edit_message_text(q,
            "Bạn chưa được cấp quyền bot nào.\nLiên hệ đội vận hành CNTx Labs.",
            reply_markup=_back_to_partner(),
        )
        return
    lines = ["<b>🤖 Bot bạn được phép cấp mã</b>"]
    for bid in bot_ids:
        try:
            mf = reg.get_manifest(bid)
            lines.append(f"• <code>{bid}</code> v{mf['version']} — {mf['bot_name']}")
        except FileNotFoundError:
            lines.append(f"• <code>{bid}</code> <i>(bot này chưa sẵn sàng trong kho bot)</i>")
    await _safe_edit_message_text(q,
        "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=_back_to_partner()
    )


async def _async_partner_stats(ctx, partner: Partner) -> dict[str, int]:
    backend_report = await _backend_partner_token_report(ctx, partner, scope="month", limit=500)
    if backend_report is not None:
        summary = dict(backend_report.get("summary") or {})
        counts = _backend_token_counts(summary)
        return {
            "active": counts["issued"] + counts["redeemed"] + counts["running"],
            "running": counts["running"],
            "billable_customers": int(summary.get("billable_customers") or 0),
            "billing_days": int(summary.get("total_days") or 0),
            "expiring_soon": 0,
            "expired": counts["expired"],
            "locked": counts["revoked"],
        }

    sf = ctx.application.bot_data["session_factory"]
    now = datetime.utcnow()

    def q():
        with sf() as s:
            tokens = (
                s.query(Token)
                .filter_by(partner_id=partner.id)
                .filter(Token.renewed_to_jti.is_(None))
                .all()
            )
            active = sum(1 for t in tokens if not t.revoked and t.expires_at >= now)
            expiring_soon = sum(
                1
                for t in tokens
                if not t.revoked and now <= t.expires_at <= now + timedelta(hours=24)
            )
            locked = sum(1 for t in tokens if not t.revoked and t.locked_at is not None)
            return {
                "active": active,
                "running": 0,
                "billable_customers": 0,
                "billing_days": 0,
                "expiring_soon": expiring_soon,
                "expired": locked,
                "locked": locked,
            }

    return await asyncio.to_thread(q)


async def _show_partner_tokens_from_backend(q, report: dict, filter_kind: str = "active"):
    items = list(report.get("items") or [])
    summary = dict(report.get("summary") or {})
    counts = _backend_token_counts(summary)
    rows = _backend_items_by_filter(items, filter_kind)[:25]
    active_count = counts["issued"] + counts["redeemed"] + counts["running"]
    title_map = {
        "active": "✅ Đang mở",
        "expired": "⌛ Hết hạn",
        "revoked": "🚫 Đã khóa",
        "all": "Tất cả",
    }
    header = f"<b>📜 Mã kích hoạt</b>\nĐang xem: <b>{title_map.get(filter_kind, 'Đang mở')}</b>"
    filter_items = [
        ("active", f"✅ Đang mở ({active_count})"),
        ("expired", f"⌛ Hết hạn ({counts['expired']})"),
        ("revoked", f"🚫 Đã khóa ({counts['revoked']})"),
        ("all", f"📚 Tất cả ({counts['all']})"),
    ]
    filter_buttons = [
        InlineKeyboardButton(
            ("• " + label + " •") if key == filter_kind else label,
            callback_data=f"ptok_f:{key}",
        )
        for key, label in filter_items
    ]
    kb: list[list[InlineKeyboardButton]] = [filter_buttons[:2], filter_buttons[2:]]
    kb.append(
        [
            InlineKeyboardButton("📊 Đối soát tháng", callback_data="ptok_sum:month"),
            InlineKeyboardButton("📚 Tổng tất cả mã", callback_data="ptok_sum:all"),
        ]
    )
    stats_line = (
        f"Tổng: <b>{counts['all']}</b> mã · "
        f"khách <b>{int(summary.get('total_customers') or 0)}</b> · "
        f"khách tính phí <b>{int(summary.get('billable_customers') or 0)}</b> · "
        f"ngày tính phí <b>{int(summary.get('total_days') or 0)}</b>"
    )
    state_line = (
        f"🕓 Chưa kích hoạt <b>{counts['issued']}</b> · "
        f"✅ Đã kích hoạt <b>{counts['redeemed']}</b> · "
        f"🟢 Đang dùng <b>{counts['running']}</b> · "
        f"⌛ Hết hạn <b>{counts['expired']}</b> · "
        f"🚫 Đã khóa <b>{counts['revoked']}</b>"
    )
    if not rows:
        body = f"{header}\n{stats_line}\n{state_line}\n\n<i>Không có mã nào ở mục này.</i>"
    else:
        body = (
            f"{header}\n"
            f"{stats_line}\n"
            f"{state_line}\n"
            f"<i>Chọn 1 mã để xem chi tiết, gia hạn hoặc khóa quyền.</i>"
        )
        for item in rows:
            token_id = str(item.get("token_id") or "")
            customer = str(item.get("customer_label") or "Không tên")
            bot_code = str(item.get("bot_code") or "?")
            days = int(item.get("duration_days") or 0)
            code = str(item.get("status_code") or "")
            label = (
                f"{_backend_status_icon(code)} {customer} · {bot_code} · "
                f"{days} ngày · {_backend_status_label(item)}"
            )
            kb.append([InlineKeyboardButton(label[:64], callback_data=f"ptok_d:{token_id}")])
    kb.append([InlineKeyboardButton("⬅️ Menu", callback_data="pmenu:home")])
    await _safe_edit_message_text(q, body, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))


async def _show_partner_lock_tokens(q, ctx, partner: Partner):
    backend_report = await _backend_partner_token_report(ctx, partner, scope="all", limit=500)
    if backend_report is not None:
        items = list(backend_report.get("items") or [])
        rows = [
            item for item in items
            if str(item.get("status_code") or "") not in {"revoked", "expired"}
        ][:25]
        lines = [
            "<b>🚫 Khóa bot khách</b>",
            "Chọn khách/mã cần khóa. Bot sẽ hỏi xác nhận trước khi khóa.",
            "",
            "<i>Dùng khi khách ngừng thanh toán hoặc đối tác cần thu hồi quyền.</i>",
        ]
        kb: list[list[InlineKeyboardButton]] = []
        if not rows:
            lines.append("\nKhông có mã nào đang mở để khóa.")
        for item in rows:
            token_id = str(item.get("token_id") or "")
            code = str(item.get("status_code") or "")
            customer = str(item.get("customer_label") or "Không tên")
            bot_code = str(item.get("bot_code") or "?")
            label = (
                f"{_backend_status_icon(code)} {customer} · "
                f"{bot_code} · {_backend_status_label(item)}"
            )
            kb.append([InlineKeyboardButton(label[:64], callback_data=f"ptok_rv:{token_id}")])
        kb.append([InlineKeyboardButton("🔎 Tra cứu khách / mã", callback_data="pmenu:search")])
        kb.append([InlineKeyboardButton("⬅️ Menu", callback_data="pmenu:home")])
        await _safe_edit_message_text(
            q,
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return

    sf = ctx.application.bot_data["session_factory"]
    now = datetime.utcnow()

    def db_q():
        with sf() as s:
            return (
                s.query(Token)
                .filter_by(partner_id=partner.id)
                .filter(Token.revoked == False)  # noqa: E712
                .filter(Token.locked_at.is_(None))
                .filter(Token.renewed_to_jti.is_(None))
                .filter(Token.expires_at >= now)
                .order_by(Token.issued_at.desc())
                .limit(25)
                .all()
            )

    rows = await asyncio.to_thread(db_q)
    lines = [
        "<b>🚫 Khóa bot khách</b>",
        "Chọn khách/mã cần khóa. Bot sẽ hỏi xác nhận trước khi khóa.",
    ]
    kb: list[list[InlineKeyboardButton]] = []
    if not rows:
        lines.append("\nKhông có mã nào đang mở để khóa.")
    for tk in rows:
        bot_id = ",".join(json.loads(tk.bot_ids_json))
        label = f"{tk.end_user_username or 'Không tên'} · {bot_id} · hết {tk.expires_at:%d/%m}"
        kb.append([InlineKeyboardButton(label[:64], callback_data=f"ptok_rv:{tk.jti}")])
    kb.append([InlineKeyboardButton("🔎 Tra cứu khách / mã", callback_data="pmenu:search")])
    kb.append([InlineKeyboardButton("⬅️ Menu", callback_data="pmenu:home")])
    await _safe_edit_message_text(q, "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))


async def _show_partner_summary_from_backend(q, report: dict, scope: str = "month"):
    summary = dict(report.get("summary") or {})
    items = list(report.get("items") or [])
    counts = _backend_token_counts(summary)
    title = "Báo cáo tháng" if scope == "month" else "Tổng tất cả mã"
    now = datetime.utcnow()
    period = f"{now:%m/%Y}" if scope == "month" else "toàn bộ thời gian"
    lines = [
        f"<b>📊 {title}</b>",
        f"Kỳ: <b>{period}</b>",
        "<i>Chỉ tính từ lúc khách kích hoạt mã. Mã chưa kích hoạt = 0 ngày.</i>",
        "",
        f"Tổng khách: <b>{int(summary.get('total_customers') or 0)}</b>",
        f"Khách tính phí: <b>{int(summary.get('billable_customers') or 0)}</b>",
        f"Tổng mã: <b>{int(summary.get('total_tokens') or 0)}</b>",
        f"Tổng ngày tính phí: <b>{int(summary.get('total_days') or 0)}</b>",
        (
            "Trạng thái: "
            f"chưa kích hoạt <b>{counts['issued']}</b> · "
            f"đã kích hoạt <b>{counts['redeemed']}</b> · "
            f"đang dùng <b>{counts['running']}</b> · "
            f"hết hạn <b>{counts['expired']}</b> · "
            f"đã khóa <b>{counts['revoked']}</b>"
        ),
        "",
        "<b>Danh sách khách</b>",
    ]
    for item in list(summary.get("by_customer") or [])[:30]:
        lines.append(
            f"• <b>{_h(item.get('customer_label') or 'Không tên')}</b>: "
            f"{int(item.get('token_count') or 0)} mã · "
            f"{int(item.get('total_days') or 0)} ngày tính phí · "
            f"đang dùng {int(item.get('running') or 0)} · khóa {int(item.get('revoked') or 0)}"
        )
    if not summary.get("by_customer"):
        lines.append("• Chưa có dữ liệu.")
    if len(items) > 0:
        lines.extend(["", "<b>Mã gần nhất</b>"])
        for item in items[:20]:
            lines.append(
                f"• <code>{_h(_short_ref(item.get('token_id') or ''))}</code> · "
                f"{_h(item.get('customer_label') or 'Không tên')} · "
                f"{_h(item.get('bot_code') or '?')} · "
                f"{int(item.get('duration_days') or 0)} ngày · "
                f"{_h(_backend_status_label(item))}"
            )
    lines.extend(["", "<i>Bản này có thể chuyển tiếp cho đối tác để đối soát cuối tháng.</i>"])
    kb = [
        [
            InlineKeyboardButton("📊 Đối soát tháng", callback_data="ptok_sum:month"),
            InlineKeyboardButton("📚 Tổng tất cả mã", callback_data="ptok_sum:all"),
        ],
        [InlineKeyboardButton("⬅️ Danh sách", callback_data="pmenu:mytokens")],
    ]
    await _safe_edit_message_text(q, "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))


async def _show_backend_token_detail(q, item: dict):
    code = str(item.get("status_code") or "")
    expires_at = item.get("entitlement_expires_at") or item.get("redeem_expires_at")
    text = (
        f"<b>🎫 Chi tiết mã kích hoạt</b>\n"
        f"Khách: <b>{_h(item.get('customer_label') or 'Không tên')}</b>\n"
        f"Bot: <code>{_h(item.get('bot_code') or '?')}</code>\n"
        f"Hạn dùng: <b>{int(item.get('duration_days') or 0)} ngày</b>\n"
        f"Cấp: {_backend_short_date(item.get('issued_at'))}\n"
        f"Kích hoạt: {_backend_short_date(item.get('redeemed_at'))}\n"
        f"Hết hạn: {_backend_short_date(expires_at)}\n"
        f"Tính phí kỳ này: <b>{int(item.get('billing_days') or 0)} ngày</b>\n"
        f"Trạng thái: {_backend_status_icon(code)} <b>{_h(_backend_status_label(item))}</b>\n"
        f"Mã quản lý: <code>{_h(_short_ref(item.get('token_id') or ''))}</code>"
    )
    if item.get("bound_account_id"):
        text += f"\nTài khoản: <code>{_h(item.get('bound_account_id'))}</code>"
    kb: list[list[InlineKeyboardButton]] = []
    token_id = str(item.get("token_id") or "")
    if token_id and code != "issued":
        kb.append([InlineKeyboardButton("♻️ Gia hạn", callback_data=f"renew_t:{token_id}")])
    if token_id and code not in {"revoked", "expired"}:
        kb.append([InlineKeyboardButton("🚫 Khóa bot của khách", callback_data=f"ptok_rv:{token_id}")])
    kb.append([InlineKeyboardButton("⬅️ Danh sách", callback_data="pmenu:mytokens")])
    kb.append([InlineKeyboardButton("🏠 Menu", callback_data="pmenu:home")])
    await _safe_edit_message_text(q, text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))


async def _reply_partner_search_results(message, ctx, partner: Partner, query: str):
    report = await _backend_partner_token_report(ctx, partner, scope="all", query=query, limit=30)
    if report is None:
        await _safe_reply_text(
            message,
            "Hệ thống tra cứu chưa sẵn sàng. Vui lòng thử lại sau vài phút.",
            reply_markup=_back_to_partner(),
        )
        return
    items = list((report or {}).get("items") or [])
    if not items:
        await _safe_reply_text(
            message,
            "Không tìm thấy khách hoặc mã quản lý phù hợp.\n"
            "Bạn có thể thử nhập lại tên khách, mã rút gọn hoặc mã quản lý đầy đủ.",
            reply_markup=_back_to_partner(),
        )
        return
    lines = [
        "<b>🔎 Kết quả tra cứu</b>",
        f"Từ khóa: <code>{_h(query)}</code>",
        f"Tìm thấy: <b>{len(items)}</b> mã",
        "",
        "Chọn một mã để xem chi tiết:",
    ]
    kb: list[list[InlineKeyboardButton]] = []
    for item in items[:15]:
        token_id = str(item.get("token_id") or "")
        code = str(item.get("status_code") or "")
        label = (
            f"{_backend_status_icon(code)} {item.get('customer_label') or 'Không tên'} · "
            f"{item.get('bot_code') or '?'} · {_backend_status_label(item)}"
        )
        kb.append([InlineKeyboardButton(label[:64], callback_data=f"ptok_d:{token_id}")])
    kb.append([InlineKeyboardButton("⬅️ Menu", callback_data="pmenu:home")])
    await _safe_reply_text(
        message,
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def _show_partner_tokens(q, ctx, partner: Partner, filter_kind: str = "active"):
    backend_report = await _backend_partner_token_report(ctx, partner, scope="all", limit=500)
    if backend_report is not None:
        await _show_partner_tokens_from_backend(q, backend_report, filter_kind=filter_kind)
        return

    sf = ctx.application.bot_data["session_factory"]
    now = datetime.utcnow()

    def db_q():
        with sf() as s:
            base = (
                s.query(Token)
                .filter_by(partner_id=partner.id)
                .filter(Token.renewed_to_jti.is_(None))
                .order_by(Token.issued_at.desc())
                .limit(200)
                .all()
            )
            counts = {
                "active": sum(1 for t in base if not t.revoked and t.locked_at is None and t.expires_at >= now),
                "expired": sum(1 for t in base if not t.revoked and t.expires_at < now),
                "revoked": sum(1 for t in base if t.revoked or t.locked_at is not None),
                "all": len(base),
                "expiring": sum(
                    1
                    for t in base
                    if not t.revoked and t.locked_at is None and now <= t.expires_at <= now + timedelta(hours=24)
                ),
            }
            if filter_kind == "active":
                rows = [t for t in base if not t.revoked and t.locked_at is None and t.expires_at >= now][:25]
            elif filter_kind == "expired":
                rows = [t for t in base if not t.revoked and t.expires_at < now][:25]
            elif filter_kind == "revoked":
                rows = [t for t in base if t.revoked or t.locked_at is not None][:25]
            else:
                rows = base[:25]
            return rows, counts

    rows, counts = await asyncio.to_thread(db_q)

    title_map = {
        "active": "✅ Đang còn hạn",
        "expired": "⌛ Hết hạn",
        "revoked": "🚫 Đã khóa",
        "all": "Tất cả",
    }
    header = f"<b>📜 Mã kích hoạt</b>\nĐang xem: <b>{title_map.get(filter_kind, 'Đang còn hạn')}</b>"

    def _filter_row(active: str) -> list[InlineKeyboardButton]:
        items = [
            ("active", f"✅ Còn hạn ({counts['active']})"),
            ("expired", f"⌛ Hết hạn ({counts['expired']})"),
            ("revoked", f"🚫 Đã khóa ({counts['revoked']})"),
            ("all", f"📚 Tất cả ({counts['all']})"),
        ]
        return [
            InlineKeyboardButton(
                ("• " + label + " •") if k == active else label,
                callback_data=f"ptok_f:{k}",
            )
            for k, label in items
        ]

    filter_buttons = _filter_row(filter_kind)
    kb: list[list[InlineKeyboardButton]] = [
        filter_buttons[:2],
        filter_buttons[2:],
    ]
    kb.append(
        [
            InlineKeyboardButton("📊 Đối soát tháng", callback_data="ptok_sum:month"),
            InlineKeyboardButton("📚 Tổng tất cả mã", callback_data="ptok_sum:all"),
        ]
    )
    stats_line = (
        f"Tổng: <b>{counts['all']}</b> mã · "
        f"còn hạn <b>{counts['active']}</b> · "
        f"sắp hết <b>{counts['expiring']}</b> · "
        f"đã khóa <b>{counts['revoked']}</b>"
    )
    if not rows:
        body = f"{header}\n{stats_line}\n\n<i>Không có mã nào ở mục này.</i>"
    else:
        body = (
            f"{header}\n"
            f"{stats_line}\n"
            f"<i>Chọn 1 mã để xem chi tiết, gia hạn hoặc khóa quyền.</i>"
        )
        for tk in rows:
            bids = ",".join(json.loads(tk.bot_ids_json))
            khach = tk.end_user_username or "?"
            days = _token_billable_days(tk)
            if tk.revoked:
                tag = "🚫"
            elif tk.locked_at is not None:
                tag = "🔒"
            elif tk.expires_at < now:
                tag = "⌛"
            elif tk.expires_at - now <= timedelta(hours=24):
                tag = "⚠️"
            else:
                tag = "✅"
            label = f"{tag} {khach} · {bids} · {days} ngày · hết {tk.expires_at:%d/%m %H:%M}"
            kb.append([InlineKeyboardButton(label[:64], callback_data=f"ptok_d:{tk.jti}")])
    kb.append([InlineKeyboardButton("⬅️ Menu", callback_data="pmenu:home")])

    await _safe_edit_message_text(q, body, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))


async def _show_partner_token_summary(q, ctx, partner: Partner, scope: str = "month"):
    backend_report = await _backend_partner_token_report(ctx, partner, scope=scope, limit=500)
    if backend_report is not None:
        await _show_partner_summary_from_backend(q, backend_report, scope=scope)
        return

    sf = ctx.application.bot_data["session_factory"]
    now = datetime.utcnow()
    month_start = datetime(now.year, now.month, 1)

    def db_q():
        with sf() as s:
            query = (
                s.query(Token)
                .filter(Token.partner_id == partner.id)
                .order_by(Token.issued_at.desc())
            )
            if scope == "month":
                query = query.filter(Token.issued_at >= month_start)
            return query.limit(500).all()

    rows = await asyncio.to_thread(db_q)
    title = "Tổng mã tháng này" if scope == "month" else "Tổng tất cả mã đã tạo"
    period = f"{month_start:%m/%Y}" if scope == "month" else "toàn bộ thời gian"

    if not rows:
        kb = [
            [
                InlineKeyboardButton("📊 Đối soát tháng", callback_data="ptok_sum:month"),
                InlineKeyboardButton("📚 Tổng tất cả mã", callback_data="ptok_sum:all"),
            ],
            [InlineKeyboardButton("⬅️ Danh sách", callback_data="pmenu:mytokens")],
        ]
        await _safe_edit_message_text(
            q,
            f"<b>📊 {title}</b>\nKỳ: <b>{period}</b>\n\n<i>Chưa có mã nào.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return

    by_customer: dict[str, dict[str, int | datetime]] = {}
    total_days = 0
    status_counts = {"còn hạn": 0, "hết hạn": 0, "đã khóa": 0}
    for tk in rows:
        customer = (tk.end_user_username or "Không tên").strip() or "Không tên"
        days = _token_billable_days(tk)
        total_days += days
        status = _token_status_label(tk, now)
        status_counts[status] = int(status_counts.get(status, 0)) + 1
        item = by_customer.setdefault(
            customer,
            {
                "count": 0,
                "days": 0,
                "active": 0,
                "expired": 0,
                "revoked": 0,
                "last_issued_at": tk.issued_at,
            },
        )
        item["count"] = int(item["count"]) + 1
        item["days"] = int(item["days"]) + days
        if status == "còn hạn":
            item["active"] = int(item["active"]) + 1
        elif status == "hết hạn":
            item["expired"] = int(item["expired"]) + 1
        else:
            item["revoked"] = int(item["revoked"]) + 1
        if tk.issued_at > item["last_issued_at"]:
            item["last_issued_at"] = tk.issued_at

    lines = [
        f"<b>📊 {title}</b>",
        f"Kỳ: <b>{period}</b>",
        "",
        f"Tổng mã đã tạo: <b>{len(rows)}</b>",
        f"Tổng ngày theo mã local: <b>{total_days}</b>",
        (
            "Trạng thái: "
            f"còn hạn <b>{status_counts['còn hạn']}</b> · "
            f"hết hạn <b>{status_counts['hết hạn']}</b> · "
            f"đã khóa <b>{status_counts['đã khóa']}</b>"
        ),
        "",
        "<b>Theo từng khách</b>",
    ]

    sorted_customers = sorted(
        by_customer.items(),
        key=lambda item: (-int(item[1]["days"]), -int(item[1]["count"]), item[0].lower()),
    )
    for customer, item in sorted_customers[:20]:
        lines.append(
            f"• <b>{_h(customer)}</b>: "
            f"{int(item['count'])} mã · {int(item['days'])} ngày "
            f"(còn {int(item['active'])}, hết {int(item['expired'])}, khóa {int(item['revoked'])})"
        )
    if len(sorted_customers) > 20:
        lines.append(f"… và {len(sorted_customers) - 20} khách khác.")

    lines.extend(["", "<b>ID mã gần nhất</b>"])
    for tk in rows[:30]:
        bot_id = ",".join(json.loads(tk.bot_ids_json))
        lines.append(
            f"• <code>{_h(_short_ref(tk.jti))}</code> · "
            f"{_h(tk.end_user_username or 'Không tên')} · "
            f"{_h(bot_id)} · {_token_billable_days(tk)} ngày · "
            f"{_h(_token_status_label(tk, now))}"
        )
    if len(rows) > 30:
        lines.append(f"… và {len(rows) - 30} mã khác.")

    kb = [
        [
            InlineKeyboardButton("📊 Đối soát tháng", callback_data="ptok_sum:month"),
            InlineKeyboardButton("📚 Tổng tất cả mã", callback_data="ptok_sum:all"),
        ],
        [InlineKeyboardButton("⬅️ Danh sách", callback_data="pmenu:mytokens")],
    ]
    await _safe_edit_message_text(
        q,
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def cb_partner_tokens_filter(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    role, partner = await _async_role(ctx, update.effective_user.id)
    if role != "partner":
        return
    kind = q.data.split(":", 1)[1]
    if kind not in {"active", "expired", "revoked", "all"}:
        kind = "active"
    await _show_partner_tokens(q, ctx, partner, filter_kind=kind)


async def cb_partner_tokens_summary(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    role, partner = await _async_role(ctx, update.effective_user.id)
    if role != "partner":
        return
    scope = q.data.split(":", 1)[1]
    if scope not in {"month", "all"}:
        scope = "month"
    await _show_partner_token_summary(q, ctx, partner, scope=scope)


async def cb_partner_token_detail(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    role, partner = await _async_role(ctx, update.effective_user.id)
    if role != "partner":
        return
    jti = q.data.split(":", 1)[1]
    backend_report = await _backend_partner_token_report(ctx, partner, scope="all", query=jti, limit=20)
    for item in list((backend_report or {}).get("items") or []):
        if str(item.get("token_id") or "") == jti:
            await _show_backend_token_detail(q, item)
            return

    sf = ctx.application.bot_data["session_factory"]
    now = datetime.utcnow()

    def fetch():
        with sf() as s:
            tk = s.get(Token, jti)
            if not tk or tk.partner_id != partner.id:
                return None
            return {
                "jti": tk.jti,
                "khach": tk.end_user_username or "?",
                "bot_id": json.loads(tk.bot_ids_json)[0],
                "issued_at": tk.issued_at,
                "expires_at": tk.expires_at,
                "revoked": tk.revoked,
                "revoked_at": tk.revoked_at,
                "renewed_to_jti": tk.renewed_to_jti,
                "expiry_notice_sent_at": tk.expiry_notice_sent_at,
                "locked_at": tk.locked_at,
            }

    info = await asyncio.to_thread(fetch)
    if not info:
        await _safe_edit_message_text(q, "Không tìm thấy mã kích hoạt này.", reply_markup=_back_to_partner())
        return

    if info["revoked"]:
        status = "🚫 Đã khóa"
        if info["revoked_at"]:
            status += f" lúc {info['revoked_at']:%Y-%m-%d %H:%M}"
        if info["renewed_to_jti"]:
            status += f"\n   <i>(đã gia hạn sang mã mới <code>{_short_ref(info['renewed_to_jti'])}</code>)</i>"
    elif info["locked_at"] is not None:
        status = (
            f"🔒 Đã khóa do hết hạn lúc {info['locked_at']:%Y-%m-%d %H:%M}\n"
            f"   Bot đã tự dừng cho khách này. Gia hạn để mở lại."
        )
    elif info["expires_at"] < now:
        status = "⌛ Đã hết hạn (đang chờ khóa)"
    elif info["expires_at"] - now <= timedelta(hours=24):
        h = int((info["expires_at"] - now).total_seconds() // 3600)
        status = f"⚠️ Sắp hết hạn (còn ~{h}h)"
    else:
        d = (info["expires_at"] - now).days
        status = f"✅ Đang còn hạn (còn ~{d} ngày)"

    text = (
        f"<b>🎫 Chi tiết mã kích hoạt</b>\n"
        f"Khách: <b>{_h(info['khach'])}</b>\n"
        f"Bot: <code>{_h(info['bot_id'])}</code>\n"
        f"Cấp: {info['issued_at']:%Y-%m-%d %H:%M}\n"
        f"Hết hạn: {info['expires_at']:%Y-%m-%d %H:%M}\n"
        f"Trạng thái: {status}\n"
        f"Mã quản lý: <code>{_h(_short_ref(info['jti']))}</code>"
    )

    kb: list[list[InlineKeyboardButton]] = []
    can_renew = not info["renewed_to_jti"] and (
        not info["revoked"] or info["expires_at"] >= now - timedelta(days=7)
    )
    can_revoke = not info["revoked"] and info["expires_at"] >= now
    if can_renew:
        kb.append([InlineKeyboardButton("♻️ Gia hạn", callback_data=f"renew_t:{info['jti']}")])
    if can_revoke:
        kb.append([InlineKeyboardButton("🚫 Khóa bot của khách", callback_data=f"ptok_rv:{info['jti']}")])
    kb.append([InlineKeyboardButton("⬅️ Danh sách", callback_data="pmenu:mytokens")])
    kb.append([InlineKeyboardButton("🏠 Menu", callback_data="pmenu:home")])

    await _safe_edit_message_text(q,
        text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb)
    )


async def cb_partner_token_revoke(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    role, partner = await _async_role(ctx, update.effective_user.id)
    if role != "partner":
        return
    jti = q.data.split(":", 1)[1]
    backend_report = await _backend_partner_token_report(ctx, partner, scope="all", query=jti, limit=20)
    backend_item = None
    for item in list((backend_report or {}).get("items") or []):
        if str(item.get("token_id") or "") == jti:
            backend_item = item
            break

    if backend_item is not None:
        if str(backend_item.get("status_code") or "") == "revoked":
            await _safe_edit_message_text(q, "Mã này đã được khóa trước đó.", reply_markup=_back_to_partner())
            return
        text = (
            "<b>🚫 Xác nhận khóa bot</b>\n\n"
            f"Khách: <b>{_h(backend_item.get('customer_label') or 'Không tên')}</b>\n"
            f"Bot: <code>{_h(backend_item.get('bot_code') or '?')}</code>\n"
            f"Trạng thái hiện tại: <b>{_h(_backend_status_label(backend_item))}</b>\n\n"
            "Sau khi khóa, khách sẽ không còn quyền dùng bot này. "
            "Nếu bot đang chạy, hệ thống sẽ tự tắt bot cho khách."
        )
        kb = [
            [InlineKeyboardButton("🚫 Khóa bot của khách", callback_data=f"ptok_rvc:{jti}")],
            [InlineKeyboardButton("⬅️ Quay lại", callback_data=f"ptok_d:{jti}")],
        ]
        await _safe_edit_message_text(q, text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
        return

    sf = ctx.application.bot_data["session_factory"]

    def fetch():
        with sf() as s:
            tk = s.get(Token, jti)
            if not tk or tk.partner_id != partner.id:
                return None
            if tk.revoked:
                return {"already_revoked": True, "khach": tk.end_user_username or "?"}
            return {
                "jti": tk.jti,
                "partner_id": tk.partner_id,
                "bot_id": json.loads(tk.bot_ids_json)[0],
                "account_id": tk.account_id,
                "end_user_label": tk.end_user_username,
                "expires_at": tk.expires_at,
                "created_by": tk.created_by,
                "khach": tk.end_user_username or "?",
            }

    snap = await asyncio.to_thread(fetch)
    if not snap:
        await _safe_edit_message_text(q, "❌ Không tìm thấy mã kích hoạt.", reply_markup=_back_to_partner())
        return
    if snap.get("already_revoked"):
        await _safe_edit_message_text(q, "Mã kích hoạt này đã được khóa trước đó.", reply_markup=_back_to_partner())
        return

    text = (
        "<b>🚫 Xác nhận khóa bot</b>\n\n"
        f"Khách: <b>{_h(snap['khach'])}</b>\n"
        f"Bot: <code>{_h(snap['bot_id'])}</code>\n\n"
        "Sau khi khóa, khách sẽ không còn quyền dùng bot này. "
        "Nếu bot đang chạy, hệ thống sẽ gửi lệnh tắt."
    )
    kb = [
        [InlineKeyboardButton("🚫 Khóa bot của khách", callback_data=f"ptok_rvc:{jti}")],
        [InlineKeyboardButton("⬅️ Quay lại", callback_data=f"ptok_d:{jti}")],
    ]
    await _safe_edit_message_text(q, text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))


async def cb_partner_token_revoke_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    role, partner = await _async_role(ctx, update.effective_user.id)
    if role != "partner":
        return
    jti = q.data.split(":", 1)[1]
    sf = ctx.application.bot_data["session_factory"]

    def fetch():
        with sf() as s:
            tk = s.get(Token, jti)
            if not tk or tk.partner_id != partner.id:
                return None
            if tk.revoked:
                return {"already_revoked": True, "khach": tk.end_user_username or "?"}
            return {
                "jti": tk.jti,
                "partner_id": tk.partner_id,
                "bot_id": json.loads(tk.bot_ids_json)[0],
                "account_id": tk.account_id,
                "end_user_label": tk.end_user_username,
                "expires_at": tk.expires_at,
                "created_by": tk.created_by,
                "khach": tk.end_user_username or "?",
            }

    snap = await asyncio.to_thread(fetch)
    backend_report = await _backend_partner_token_report(ctx, partner, scope="all", query=jti, limit=20)
    backend_item = None
    for item in list((backend_report or {}).get("items") or []):
        if str(item.get("token_id") or "") == jti:
            backend_item = item
            break
    if not snap and backend_item is None:
        await _safe_edit_message_text(q, "❌ Không tìm thấy mã kích hoạt.", reply_markup=_back_to_partner())
        return
    if snap and snap.get("already_revoked"):
        await _safe_edit_message_text(q, "Mã kích hoạt này đã được khóa trước đó.", reply_markup=_back_to_partner())
        return

    bc: BackendClient | None = ctx.application.bot_data.get("backend_client")
    is_backend_product = backend_item is not None or str((snap or {}).get("created_by") or "").startswith("backend-product:")
    if is_backend_product:
        if bc is None or not bc.enabled:
            await _safe_edit_message_text(q,
                "Hệ thống chưa sẵn sàng để khóa mã. Vui lòng thử lại sau vài phút.",
                reply_markup=_back_to_partner(),
            )
            return
        revoked = await bc.revoke_activation_token(
            token_id=jti,
            partner_id=partner.id,
            revoked_by_telegram_id=update.effective_user.id if update.effective_user else None,
            reason="partner_lock_unpaid",
        )
        if revoked is None:
            await _safe_edit_message_text(q,
                "Hệ thống chưa khóa được mã. Vui lòng thử lại sau vài phút.",
                reply_markup=_back_to_partner(),
            )
            return

    def mark_revoked():
        with sf() as s:
            tk = s.get(Token, jti)
            if tk and not tk.revoked:
                tk.revoked = True
                tk.revoked_at = datetime.utcnow()
                s.commit()

    await asyncio.to_thread(mark_revoked)
    customer = (
        str((backend_item or {}).get("customer_label") or "").strip()
        or str((snap or {}).get("khach") or "").strip()
        or "khách này"
    )
    msg = (
        f"🚫 Đã khóa quyền bot của khách <b>{_h(customer)}</b>.\n"
        "Khách sẽ không bật được bot bằng mã này nữa."
    )
    if is_backend_product:
        msg += "\nNếu bot đang chạy, hệ thống đang tắt bot cho khách."
    if snap:
        if not str(snap.get("created_by") or "").startswith("backend-product:"):
            state_mirror.mirror(
                ctx.application.bot_data.get("redis_client"),
                jti=snap["jti"], state=state_mirror.STATE_REVOKED,
                partner_id=snap["partner_id"], bot_id=snap["bot_id"],
                account_id=snap["account_id"], end_user_label=snap["end_user_label"],
                expires_at=snap["expires_at"],
                grace_sec=ctx.application.bot_data["settings"].redis_state_grace_sec,
            )
            if bc:
                await bc.force_stop(
                    jti=snap["jti"],
                    reason=f"partner_revoke",
                )
    kb = [
        [InlineKeyboardButton("⬅️ Danh sách", callback_data="pmenu:mytokens")],
        [InlineKeyboardButton("🏠 Menu", callback_data="pmenu:home")],
    ]
    await _safe_edit_message_text(q, msg, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))


# Issue flow: pick bot button
async def cb_issue_pick_bot(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    role, partner = await _async_role(ctx, update.effective_user.id)
    if role != "partner":
        return
    bot_id = q.data.split(":", 1)[1]
    ctx.user_data["issue_bot_id"] = bot_id
    kb = [
        [
            InlineKeyboardButton("1 ngày", callback_data="issue_d:1"),
            InlineKeyboardButton("3 ngày", callback_data="issue_d:3"),
        ],
        [
            InlineKeyboardButton("7 ngày", callback_data="issue_d:7"),
            InlineKeyboardButton("30 ngày", callback_data="issue_d:30"),
        ],
        [InlineKeyboardButton("⬅️ Hủy", callback_data="pmenu:home")],
    ]
    await _safe_edit_message_text(q,
        f"<b>🎫 Tạo mã kích hoạt</b>\n"
        f"Khách: <b>{_h(ctx.user_data.get('issue_user_label'))}</b>\n"
        f"Bot: <code>{_h(bot_id)}</code>\n\n"
        f"Bước 3/3: Chọn hạn dùng cho mã",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def cb_issue_days(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    role, partner = await _async_role(ctx, update.effective_user.id)
    if role != "partner":
        return
    days = int(q.data.split(":", 1)[1])
    bot_id = ctx.user_data.pop("issue_bot_id", None)
    user_label = ctx.user_data.pop("issue_user_label", None)
    ctx.user_data.pop("issue_partner_id", None)
    ctx.user_data.pop("awaiting", None)
    if not bot_id or not user_label:
        await _safe_edit_message_text(q, "Phiên thao tác đã hết hạn. Thử lại nhé.", reply_markup=_back_to_partner())
        return

    sf = ctx.application.bot_data["session_factory"]
    reg: BotRegistry = ctx.application.bot_data["registry"]
    bc: BackendClient | None = ctx.application.bot_data.get("backend_client")

    def check():
        with sf() as s:
            allowed = [
                g.bot_id
                for g in s.query(PartnerBotGrant)
                .filter_by(partner_id=partner.id, revoked=False)
                .all()
            ]
            return bot_id in allowed, allowed

    allowed_ok, allowed_bot_ids = await asyncio.to_thread(check)
    if not allowed_ok:
        await _safe_edit_message_text(q,
            f"Bạn không có quyền dùng bot {bot_id} nữa.",
            reply_markup=_back_to_partner(),
        )
        return
    if not reg.has(bot_id):
        await _safe_edit_message_text(q,
            f"Bot {bot_id} hiện chưa sẵn sàng trong kho bot.",
            reply_markup=_back_to_partner(),
        )
        return

    if bc is None or not bc.enabled:
        await _safe_edit_message_text(q,
            "Hệ thống cấp mã chưa sẵn sàng. Vui lòng thử lại sau vài phút.",
            reply_markup=_back_to_partner(),
        )
        return
    if not await _sync_product_partner(ctx, partner, allowed_bot_ids):
        await _safe_edit_message_text(q,
            "Chưa cập nhật được quyền đối tác. Vui lòng thử lại sau vài phút.",
            reply_markup=_back_to_partner(),
        )
        return
    issued = await bc.issue_activation_token(
        partner_id=partner.id,
        bot_code=bot_id,
        duration_days=days,
        issued_by_telegram_id=update.effective_user.id if update.effective_user else None,
        customer_label=user_label,
    )
    if not issued or not issued.get("raw_token") or not issued.get("token_id"):
        await _safe_edit_message_text(q,
            "Hệ thống chưa cấp được mã kích hoạt. Vui lòng thử lại sau vài phút.",
            reply_markup=_back_to_partner(),
        )
        return

    token = str(issued["raw_token"])
    jti = str(issued["token_id"])
    exp = datetime.utcnow() + timedelta(days=days)

    def save():
        with sf() as s:
            s.add(
                Token(
                    jti=jti,
                    partner_id=partner.id,
                    bot_ids_json=json.dumps([bot_id]),
                    issued_at=datetime.utcnow(),
                    expires_at=exp.replace(tzinfo=None),
                    revoked=False,
                    end_user_username=user_label,
                    created_by=f"backend-product:partner:{partner.id}",
                )
            )
            s.commit()

    await asyncio.to_thread(save)
    msg = _activation_code_message(
        title="✅ <b>Mã kích hoạt đã tạo</b>",
        customer_label=user_label,
        bot_id=bot_id,
        days=days,
        expires_at=exp,
        activation_code=token,
        management_ref=jti,
    )
    await _safe_edit_message_text(q,
        msg, parse_mode=ParseMode.HTML, reply_markup=_back_to_partner()
    )


# ───────────────────────── renew flow (partner) ─────────────────────────

async def _show_renew_pick_token(q, ctx, partner: Partner):
    backend_report = await _backend_partner_token_report(ctx, partner, scope="all", limit=500)
    if backend_report is not None:
        rows = [
            item for item in list(backend_report.get("items") or [])
            if str(item.get("status_code") or "") != "issued"
        ][:25]
        if not rows:
            await _safe_edit_message_text(
                q,
                "Chưa có khách nào cần gia hạn.\n\n"
                "Mã chưa kích hoạt thì chưa tính phí và chưa cần gia hạn.",
                reply_markup=_back_to_partner(),
            )
            return
        kb: list[list[InlineKeyboardButton]] = []
        for item in rows:
            token_id = str(item.get("token_id") or "")
            code = str(item.get("status_code") or "")
            label = (
                f"{_backend_status_icon(code)} {item.get('customer_label') or 'Không tên'} · "
                f"{item.get('bot_code') or '?'} · {_backend_status_label(item)}"
            )
            kb.append([InlineKeyboardButton(label[:64], callback_data=f"renew_t:{token_id}")])
        kb.append([InlineKeyboardButton("⬅️ Menu", callback_data="pmenu:home")])
        await _safe_edit_message_text(
            q,
            "<b>♻️ Gia hạn mã kích hoạt</b>\n\n"
            "Chọn khách cần gia hạn:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return

    sf = ctx.application.bot_data["session_factory"]
    now = datetime.utcnow()

    def db_q():
        with sf() as s:
            rows = (
                s.query(Token)
                .filter(Token.partner_id == partner.id)
                .filter(Token.renewed_to_jti.is_(None))
                .order_by(Token.expires_at.asc())
                .limit(30)
                .all()
            )
            return [t for t in rows if not t.revoked or t.expires_at >= now - timedelta(days=7)][:15]

    rows = await asyncio.to_thread(db_q)
    rows.sort(key=lambda t: (t.revoked, t.expires_at < now, t.expires_at))
    if not rows:
        await _safe_edit_message_text(q,
            "Bạn chưa có mã kích hoạt nào để gia hạn.",
            reply_markup=_back_to_partner(),
        )
        return

    kb = []
    for tk in rows:
        khach = tk.end_user_username or "?"
        bids = ",".join(json.loads(tk.bot_ids_json))
        if tk.revoked:
            tag = "🚫"
        elif tk.expires_at < now:
            tag = "⌛"
        elif tk.expires_at - now <= timedelta(days=1):
            tag = "⚠️"
        else:
            tag = "✅"
        label = f"{tag} {khach} · {bids} · {tk.expires_at:%d/%m}"
        kb.append([InlineKeyboardButton(label[:60], callback_data=f"renew_t:{tk.jti}")])
    kb.append([InlineKeyboardButton("⬅️ Quay lại", callback_data="pmenu:home")])

    await _safe_edit_message_text(q,
        "<b>♻️ Gia hạn mã kích hoạt</b>\n\nChọn khách cần gia hạn:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def cb_renew_pick_token(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    role, partner = await _async_role(ctx, update.effective_user.id)
    if role != "partner":
        return
    jti = q.data.split(":", 1)[1]
    sf = ctx.application.bot_data["session_factory"]

    def fetch():
        with sf() as s:
            tk = s.get(Token, jti)
            if not tk or tk.partner_id != partner.id:
                return None
            return {
                "jti": tk.jti,
                "khach": tk.end_user_username,
                "bot_id": json.loads(tk.bot_ids_json)[0],
                "expires_at": tk.expires_at,
                "revoked": tk.revoked,
                "renewed_to_jti": tk.renewed_to_jti,
            }

    info = await asyncio.to_thread(fetch)
    if not info:
        backend_report = await _backend_partner_token_report(ctx, partner, scope="all", query=jti, limit=20)
        for item in list((backend_report or {}).get("items") or []):
            if str(item.get("token_id") or "") == jti:
                info = {
                    "jti": jti,
                    "khach": item.get("customer_label") or "Không tên",
                    "bot_id": item.get("bot_code") or "",
                    "expires_at": item.get("entitlement_expires_at") or item.get("redeem_expires_at"),
                    "revoked": str(item.get("status_code") or "") == "revoked",
                    "renewed_to_jti": None,
                }
                break
        if not info:
            await _safe_edit_message_text(q, "Không tìm thấy mã kích hoạt này.", reply_markup=_back_to_partner())
            return
    if info["renewed_to_jti"]:
        await _safe_edit_message_text(q,
            f"Mã này đã được gia hạn sang mã mới <code>{_short_ref(info['renewed_to_jti'])}</code>.",
            parse_mode=ParseMode.HTML,
            reply_markup=_back_to_partner(),
        )
        return

    ctx.user_data["renew_jti"] = jti
    ctx.user_data["renew_khach"] = info["khach"]
    ctx.user_data["renew_bot_id"] = info["bot_id"]
    expires_at = info.get("expires_at")
    expires_text = (
        expires_at.strftime("%Y-%m-%d %H:%M")
        if isinstance(expires_at, datetime)
        else _backend_short_date(expires_at)
    )

    kb = [
        [
            InlineKeyboardButton("1 ngày", callback_data="renew_d:1"),
            InlineKeyboardButton("3 ngày", callback_data="renew_d:3"),
        ],
        [
            InlineKeyboardButton("7 ngày", callback_data="renew_d:7"),
            InlineKeyboardButton("30 ngày", callback_data="renew_d:30"),
        ],
        [InlineKeyboardButton("⬅️ Hủy", callback_data="pmenu:home")],
    ]
    await _safe_edit_message_text(q,
        f"<b>♻️ Gia hạn mã kích hoạt</b>\n"
        f"Khách: <b>{_h(info['khach'])}</b>\n"
        f"Bot: <code>{_h(info['bot_id'])}</code>\n"
        f"Hết hạn hiện tại: {expires_text}\n\n"
        f"Chọn thời hạn mới:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def cb_renew_days(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    role, partner = await _async_role(ctx, update.effective_user.id)
    if role != "partner":
        return
    days = int(q.data.split(":", 1)[1])
    old_jti = ctx.user_data.pop("renew_jti", None)
    khach = ctx.user_data.pop("renew_khach", None)
    bot_id = ctx.user_data.pop("renew_bot_id", None)
    if not old_jti or not bot_id:
        await _safe_edit_message_text(q, "Phiên thao tác đã hết hạn. Thử lại nhé.", reply_markup=_back_to_partner())
        return

    sf = ctx.application.bot_data["session_factory"]
    reg: BotRegistry = ctx.application.bot_data["registry"]
    bc: BackendClient | None = ctx.application.bot_data.get("backend_client")

    def check_grant():
        with sf() as s:
            allowed = [
                g.bot_id
                for g in s.query(PartnerBotGrant)
                .filter_by(partner_id=partner.id, revoked=False)
                .all()
            ]
            return bot_id in allowed, allowed

    allowed_ok, allowed_bot_ids = await asyncio.to_thread(check_grant)
    if not allowed_ok:
        await _safe_edit_message_text(q,
            f"Bạn không còn quyền dùng bot {bot_id}.",
            reply_markup=_back_to_partner(),
        )
        return
    if not reg.has(bot_id):
        await _safe_edit_message_text(q,
            f"Bot {bot_id} hiện chưa sẵn sàng trong kho bot.",
            reply_markup=_back_to_partner(),
        )
        return

    if bc is None or not bc.enabled:
        await _safe_edit_message_text(q,
            "Hệ thống cấp mã chưa sẵn sàng. Vui lòng thử lại sau vài phút.",
            reply_markup=_back_to_partner(),
        )
        return
    if not await _sync_product_partner(ctx, partner, allowed_bot_ids):
        await _safe_edit_message_text(q,
            "Chưa cập nhật được quyền đối tác. Vui lòng thử lại sau vài phút.",
            reply_markup=_back_to_partner(),
        )
        return
    issued = await bc.issue_activation_token(
        partner_id=partner.id,
        bot_code=bot_id,
        duration_days=days,
        issued_by_telegram_id=update.effective_user.id if update.effective_user else None,
        customer_label=khach,
    )
    if not issued or not issued.get("raw_token") or not issued.get("token_id"):
        await _safe_edit_message_text(q,
            "Hệ thống chưa cấp được mã gia hạn. Vui lòng thử lại sau vài phút.",
            reply_markup=_back_to_partner(),
        )
        return

    token = str(issued["raw_token"])
    new_jti = str(issued["token_id"])
    exp = datetime.utcnow() + timedelta(days=days)

    def save():
        with sf() as s:
            s.add(
                Token(
                    jti=new_jti,
                    partner_id=partner.id,
                    bot_ids_json=json.dumps([bot_id]),
                    issued_at=datetime.utcnow(),
                    expires_at=exp.replace(tzinfo=None),
                    revoked=False,
                    end_user_username=khach,
                    created_by=f"backend-product:partner:{partner.id}:renew",
                )
            )
            old = s.get(Token, old_jti)
            if old:
                old.renewed_to_jti = new_jti
            s.commit()

    await asyncio.to_thread(save)
    transfer_note = (
        "\nKhách dán mã mới vào ứng dụng CNTxLabs để gia hạn quyền. "
        "Mã cũ vẫn chạy đến hết hạn nếu đã được kích hoạt."
    )
    msg = _activation_code_message(
        title="♻️ <b>Mã kích hoạt đã gia hạn</b>",
        customer_label=khach or "?",
        bot_id=bot_id,
        days=days,
        expires_at=exp,
        activation_code=token,
        management_ref=new_jti,
        extra_note=transfer_note,
    )
    await _safe_edit_message_text(q,
        msg, parse_mode=ParseMode.HTML, reply_markup=_back_to_partner()
    )


# ───────────────────────── free-text router (conv state) ─────────────────────────

async def msg_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    state = ctx.user_data.get("awaiting")
    if not state:
        return
    text = update.message.text.strip()

    # ─── Add partner flow (admin) ───
    if state == "add_partner_tg_id":
        if not await _is_admin(ctx, update.effective_user.id):
            ctx.user_data.clear()
            return
        try:
            tg_id = int(text)
        except ValueError:
            await update.message.reply_text(
                "Telegram ID phải là số nguyên. Nhập lại hoặc /cancel."
            )
            return
        ctx.user_data["pending_partner_tg_id"] = tg_id
        ctx.user_data["awaiting"] = "add_partner_name"
        await update.message.reply_text(
            "OK. Bước 2/2: Gửi tên hiển thị của đối tác:"
        )
        return

    if state == "add_partner_name":
        if not await _is_admin(ctx, update.effective_user.id):
            ctx.user_data.clear()
            return
        tg_id = ctx.user_data.pop("pending_partner_tg_id", None)
        ctx.user_data.pop("awaiting", None)
        if not tg_id:
            await update.message.reply_text("Phiên thao tác đã hết hạn. Thử lại nhé.", reply_markup=_admin_menu())
            return
        sf = ctx.application.bot_data["session_factory"]
        name = text

        def do_add():
            with sf() as s:
                existing = s.query(Partner).filter_by(telegram_user_id=tg_id).first()
                if existing:
                    return (
                        f"⚠️ Telegram ID này đã thuộc partner <code>{existing.id}</code> "
                        f"({existing.name})."
                    )
                pid = f"p_{tg_id}"
                if s.get(Partner, pid):
                    pid = f"p_{tg_id}_{int(datetime.utcnow().timestamp())}"
                s.add(
                    Partner(
                        id=pid,
                        name=name,
                        contact=f"tg:{tg_id}",
                        active=True,
                        telegram_user_id=tg_id,
                    )
                )
                s.commit()
                return (
                    f"✅ Đã thêm đối tác\n"
                    f"ID: <code>{pid}</code>\n"
                    f"Tên: {name}\n"
                    f"Telegram: <code>{tg_id}</code>\n\n"
                    f"Bước tiếp: nhấn <b>🔑 Cấp quyền bot cho đối tác</b>."
                )

        text_out = await asyncio.to_thread(do_add)
        await update.message.reply_text(
            text_out, parse_mode=ParseMode.HTML, reply_markup=_admin_menu()
        )
        return

    # ─── Partner search flow ───
    if state == "partner_search_query":
        role, partner = await _async_role(ctx, update.effective_user.id)
        ctx.user_data.pop("awaiting", None)
        if role != "partner":
            ctx.user_data.clear()
            return
        query = text[:120].strip()
        if not query:
            await update.message.reply_text("Nhập tên khách hoặc mã quản lý để tra cứu nhé.")
            return
        await _reply_partner_search_results(update.message, ctx, partner, query)
        return

    # ─── Issue token flow (partner) ───
    if state == "issue_user_label":
        role, partner = await _async_role(ctx, update.effective_user.id)
        if role != "partner":
            ctx.user_data.clear()
            return
        label = text[:64].strip()
        if not label:
            await update.message.reply_text(
                "Tên không được rỗng. Nhập lại hoặc /cancel."
            )
            return
        ctx.user_data["issue_user_label"] = label
        ctx.user_data.pop("awaiting", None)

        sf = ctx.application.bot_data["session_factory"]
        reg: BotRegistry = ctx.application.bot_data["registry"]

        def db_q():
            with sf() as s:
                return [
                    g.bot_id
                    for g in s.query(PartnerBotGrant)
                    .filter_by(partner_id=partner.id, revoked=False)
                    .all()
                ]

        bot_ids = await asyncio.to_thread(db_q)
        bot_ids = [b for b in bot_ids if reg.has(b)]
        if not bot_ids:
            await update.message.reply_text(
                "Bạn chưa có bot nào đang dùng được. Liên hệ đội vận hành CNTx Labs.",
                reply_markup=_partner_menu(),
            )
            return
        kb = [
            [InlineKeyboardButton(b, callback_data=f"issue_b:{b}")]
            for b in bot_ids
        ]
        kb.append([InlineKeyboardButton("⬅️ Hủy", callback_data="pmenu:home")])
        await update.message.reply_text(
            f"Khách: <b>{label}</b>\n\nBước 2/3: Chọn bot:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return


# ───────────────────────── error handler ─────────────────────────

# ───────────────────────── expiry notifier ─────────────────────────

NOTIFY_BEFORE = timedelta(hours=24)
NOTIFY_INTERVAL_SEC = 900  # 15 phút


async def _notifier_tick(app: Application):
    sf = app.bot_data["session_factory"]
    now = datetime.utcnow()
    deadline = now + NOTIFY_BEFORE

    def fetch():
        with sf() as s:
            return [
                {
                    "jti": tk.jti,
                    "partner_id": tk.partner_id,
                    "khach": tk.end_user_username
                    or (f"tg:{tk.end_user_telegram_id}" if tk.end_user_telegram_id else "?"),
                    "bot_id": json.loads(tk.bot_ids_json)[0],
                    "expires_at": tk.expires_at,
                    "partner_tg_id": tk.partner.telegram_user_id if tk.partner else None,
                }
                for tk in s.query(Token)
                .join(Partner, Token.partner_id == Partner.id)
                .filter(Token.revoked == False)  # noqa: E712
                .filter(~Token.created_by.like("backend-product:%"))
                .filter(Token.renewed_to_jti.is_(None))
                .filter(Token.expiry_notice_sent_at.is_(None))
                .filter(Token.expires_at > now)
                .filter(Token.expires_at <= deadline)
                .filter(Partner.telegram_user_id.isnot(None))
                .filter(Partner.active == True)  # noqa: E712
                .limit(50)
                .all()
            ]

    items = await asyncio.to_thread(fetch)
    if not items:
        return

    sent_jtis: list[str] = []
    for it in items:
        try:
            hours_left = max(0, int((it["expires_at"] - now).total_seconds() // 3600))
            text = (
                "⚠️ <b>Mã kích hoạt sắp hết hạn</b>\n"
                f"Khách: <b>{_h(it['khach'])}</b>\n"
                f"Bot: <code>{_h(it['bot_id'])}</code>\n"
                f"Còn ~{hours_left}h (đến {it['expires_at']:%Y-%m-%d %H:%M UTC})\n\n"
                f"Vào /start → <b>♻️ Gia hạn mã</b> để cấp tiếp."
            )
            await app.bot.send_message(
                chat_id=it["partner_tg_id"],
                text=text,
                parse_mode=ParseMode.HTML,
            )
            sent_jtis.append(it["jti"])
        except Exception:
            log.exception("notify_failed jti=%s", it["jti"])

    if sent_jtis:
        def mark():
            with sf() as s:
                for j in sent_jtis:
                    tk = s.get(Token, j)
                    if tk:
                        tk.expiry_notice_sent_at = datetime.utcnow()
                s.commit()

        await asyncio.to_thread(mark)
        log.info("notifier sent=%d", len(sent_jtis))


async def _notifier_loop(app: Application):
    log.info("notifier loop started (interval=%ds)", NOTIFY_INTERVAL_SEC)
    while True:
        try:
            await _notifier_tick(app)
        except Exception:
            log.exception("notifier_tick_failed")
        await asyncio.sleep(NOTIFY_INTERVAL_SEC)


LOCK_INTERVAL_SEC = 300  # 5 phút


async def _lock_check_tick(app: Application):
    """Quét token hết hạn nhưng chưa khóa → mark locked_at + DM partner."""
    sf = app.bot_data["session_factory"]
    now = datetime.utcnow()

    def fetch():
        with sf() as s:
            return [
                {
                    "jti": tk.jti,
                    "khach": tk.end_user_username
                    or (f"tg:{tk.end_user_telegram_id}" if tk.end_user_telegram_id else "?"),
                    "bot_id": json.loads(tk.bot_ids_json)[0],
                    "account_id": tk.account_id,
                    "partner_id": tk.partner_id,
                    "expires_at": tk.expires_at,
                    "partner_tg_id": tk.partner.telegram_user_id if tk.partner else None,
                }
                for tk in s.query(Token)
                .join(Partner, Token.partner_id == Partner.id)
                .filter(Token.revoked == False)  # noqa: E712
                .filter(~Token.created_by.like("backend-product:%"))
                .filter(Token.renewed_to_jti.is_(None))
                .filter(Token.locked_at.is_(None))
                .filter(Token.expires_at < now)
                .filter(Partner.telegram_user_id.isnot(None))
                .filter(Partner.active == True)  # noqa: E712
                .limit(50)
                .all()
            ]

    items = await asyncio.to_thread(fetch)
    if not items:
        return

    locked_jtis: list[str] = []
    rc = app.bot_data.get("redis_client")
    bc: BackendClient | None = app.bot_data.get("backend_client")
    grace = app.bot_data["settings"].redis_state_grace_sec
    for it in items:
        try:
            state_mirror.mirror(
                rc, jti=it["jti"], state=state_mirror.STATE_LOCKED,
                partner_id=it["partner_id"], bot_id=it["bot_id"],
                account_id=it["account_id"], end_user_label=it["khach"],
                expires_at=it["expires_at"], grace_sec=grace,
            )
            force_result = None
            if bc:
                force_result = await bc.force_stop(
                    jti=it["jti"],
                    reason=f"token_expired:khach={it['khach']}",
                )
                if force_result is None:
                    await force_stop_retry.mark_attempt(
                        sf, jti=it["jti"], success=False,
                        error="initial_call_failed",
                    )
                elif force_result.get("action") in ("stop", "noop"):
                    await force_stop_retry.mark_attempt(sf, jti=it["jti"], success=True)
                else:
                    await force_stop_retry.mark_attempt(
                        sf, jti=it["jti"], success=False,
                        error=f"action={force_result.get('action')} note={force_result.get('note')}",
                    )
            stop_note = ""
            if force_result is not None:
                action = force_result.get("action")
                if action == "stop":
                    stop_note = "\n✅ Bot của khách đã được tự dừng."
                elif action == "noop":
                    stop_note = "\n(Bot không chạy nên không cần dừng.)"
                elif action == "error":
                    stop_note = f"\n⚠️ Hệ thống chưa dừng được bot: {force_result.get('note')}"
            text = (
                "🔒 <b>Mã kích hoạt đã hết hạn — Bot bị khóa</b>\n"
                f"Khách: <b>{_h(it['khach'])}</b>\n"
                f"Bot: <code>{_h(it['bot_id'])}</code>\n"
                f"Tài khoản giao dịch: <code>{it['account_id']}</code>\n"
                f"Hết hạn: {it['expires_at']:%Y-%m-%d %H:%M UTC}"
                f"{stop_note}\n\n"
                f"Vào /start → <b>♻️ Gia hạn mã</b> để cấp lại quyền."
            )
            if it["partner_tg_id"]:
                await app.bot.send_message(
                    chat_id=it["partner_tg_id"],
                    text=text,
                    parse_mode=ParseMode.HTML,
                )
            locked_jtis.append(it["jti"])
        except Exception:
            log.exception("lock_dm_failed jti=%s", it["jti"])

    if locked_jtis:
        def mark():
            with sf() as s:
                ts = datetime.utcnow()
                for j in locked_jtis:
                    tk = s.get(Token, j)
                    if tk and tk.locked_at is None:
                        tk.locked_at = ts
                s.commit()

        await asyncio.to_thread(mark)
        log.info("lock_check locked=%d", len(locked_jtis))


async def _lock_check_loop(app: Application):
    log.info("lock_check loop started (interval=%ds)", LOCK_INTERVAL_SEC)
    while True:
        try:
            await _lock_check_tick(app)
        except Exception:
            log.exception("lock_check_tick_failed")
        await asyncio.sleep(LOCK_INTERVAL_SEC)


async def _post_init(app: Application):
    tasks = [
        asyncio.create_task(_notifier_loop(app), name="expiry_notifier"),
        asyncio.create_task(_lock_check_loop(app), name="lock_checker"),
        asyncio.create_task(force_stop_retry.run_loop(app), name="force_stop_retry"),
    ]
    app.bot_data["background_tasks"] = tasks


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    if isinstance(ctx.error, BadRequest):
        log.warning("telegram bad request: %s", str(ctx.error)[:240])
    else:
        log.exception("telegram handler error: %s", ctx.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "Thao tác chưa hoàn tất. Bấm /start để mở lại menu nhé."
            )
        except Exception:
            pass


# ───────────────────────── build app ─────────────────────────

def build_application(settings: Settings) -> Application:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN chưa được cấu hình")

    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    ensure_schema_patches(engine)
    sf = make_session_factory(engine)
    cipher = BotCipher(base64.b64decode(settings.master_key_b64))
    registry = BotRegistry(settings.source_bot_dir, settings.encrypted_bot_dir, cipher)
    token_service = TokenService(settings.jwt_secret)
    redis_client = state_mirror.make_client(settings.redis_url)
    backend_client = BackendClient(settings.backend_url, settings.backend_internal_key)
    if backend_client.enabled:
        log.info("backend_client enabled url=%s", settings.backend_url)
    else:
        log.warning("backend_client disabled — lock/revoke không tự dừng bot")

    app = Application.builder().token(settings.telegram_bot_token).post_init(_post_init).build()
    app.bot_data["settings"] = settings
    app.bot_data["session_factory"] = sf
    app.bot_data["registry"] = registry
    app.bot_data["token_service"] = token_service
    app.bot_data["redis_client"] = redis_client
    app.bot_data["backend_client"] = backend_client

    async def _debug_update(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        u = update.effective_user
        if update.callback_query is not None:
            log.info(
                "tg.callback user=%s data=%r",
                u.id if u else "?",
                update.callback_query.data,
            )
        elif update.message is not None and update.message.text:
            log.info(
                "tg.message user=%s text=%r",
                u.id if u else "?",
                update.message.text[:60],
            )

    from telegram.ext import TypeHandler

    app.add_handler(TypeHandler(Update, _debug_update), group=-1)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("whoami", cmd_whoami))

    app.add_handler(CallbackQueryHandler(cb_admin_menu, pattern=r"^menu:"))
    app.add_handler(CallbackQueryHandler(cb_partner_menu, pattern=r"^pmenu:"))
    app.add_handler(CallbackQueryHandler(cb_grant_partner, pattern=r"^grant_p:"))
    app.add_handler(CallbackQueryHandler(cb_grant_bot, pattern=r"^grant_b:"))
    app.add_handler(CallbackQueryHandler(cb_revoke_pick_bot, pattern=r"^rg_p:"))
    app.add_handler(CallbackQueryHandler(cb_revoke_confirm, pattern=r"^rg_b:"))
    app.add_handler(CallbackQueryHandler(cb_issue_pick_bot, pattern=r"^issue_b:"))
    app.add_handler(CallbackQueryHandler(cb_issue_days, pattern=r"^issue_d:"))
    app.add_handler(CallbackQueryHandler(cb_renew_pick_token, pattern=r"^renew_t:"))
    app.add_handler(CallbackQueryHandler(cb_renew_days, pattern=r"^renew_d:"))
    app.add_handler(CallbackQueryHandler(cb_admin_tokens_filter, pattern=r"^atok:"))
    app.add_handler(CallbackQueryHandler(cb_partner_tokens_filter, pattern=r"^ptok_f:"))
    app.add_handler(CallbackQueryHandler(cb_partner_tokens_summary, pattern=r"^ptok_sum:"))
    app.add_handler(CallbackQueryHandler(cb_partner_token_detail, pattern=r"^ptok_d:"))
    app.add_handler(CallbackQueryHandler(cb_partner_token_revoke_confirm, pattern=r"^ptok_rvc:"))
    app.add_handler(CallbackQueryHandler(cb_partner_token_revoke, pattern=r"^ptok_rv:"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, msg_router))
    app.add_error_handler(on_error)

    return app
