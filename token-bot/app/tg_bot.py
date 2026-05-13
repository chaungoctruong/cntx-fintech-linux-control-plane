import asyncio
import base64
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
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
            [InlineKeyboardButton("🤖 Bot trong catalog", callback_data="menu:bots")],
            [InlineKeyboardButton("🔑 Cấp quyền bot cho đối tác", callback_data="menu:grant")],
            [InlineKeyboardButton("🚫 Hủy quyền", callback_data="menu:revokegrant")],
            [InlineKeyboardButton("📜 Token đã cấp", callback_data="menu:tokens")],
        ]
    )


def _partner_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🤖 Bot của tôi", callback_data="pmenu:mybots")],
            [InlineKeyboardButton("🎫 Cấp token cho khách", callback_data="pmenu:issue")],
            [InlineKeyboardButton("♻️ Gia hạn token", callback_data="pmenu:renew")],
            [InlineKeyboardButton("📜 Token tôi đã cấp", callback_data="pmenu:mytokens")],
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
            f"Partner ID: <code>{partner.id}</code>\n\n"
            f"📊 <b>Tổng quan</b>\n"
            f"  ✅ Active: <b>{stats['active']}</b>\n"
            f"  ⚠️ Sắp hết hạn (24h): <b>{stats['expiring_soon']}</b>\n"
            f"  🔒 Đã khóa (chờ gia hạn): <b>{stats['locked']}</b>\n\n"
            f"Chọn chức năng:",
            parse_mode=ParseMode.HTML,
            reply_markup=_partner_menu(),
        )
    else:
        await update.message.reply_text(
            f"Xin chào <b>{u.full_name}</b>\n"
            f"Telegram ID: <code>{u.id}</code>\n"
            f"Vai trò: <b>stranger</b>\n\n"
            f"Bạn chưa được cấp quyền sử dụng bot này.\n"
            f"Gửi Telegram ID phía trên cho admin để đăng ký.",
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
    await update.message.reply_text("Đã hủy thao tác.", reply_markup=kb)


# ───────────────────────── admin menu callbacks ─────────────────────────

async def cb_admin_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await _is_admin(ctx, update.effective_user.id):
        await q.edit_message_text("Admin only.")
        return

    action = q.data.split(":", 1)[1]

    if action == "home":
        ctx.user_data.clear()
        await q.edit_message_text("Menu chính:", reply_markup=_admin_menu())
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
        await q.edit_message_text(
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
        await q.edit_message_text(
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
            f"   tg={p.telegram_user_id or '-'}  grants={g}"
        )
    await q.edit_message_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=_back_to_admin(),
    )


async def _show_bots_admin(q, ctx):
    reg: BotRegistry = ctx.application.bot_data["registry"]
    items = await asyncio.to_thread(reg.list_encrypted)
    if not items:
        await q.edit_message_text(
            "Chưa có bot nào trong catalog.\nChạy <code>scripts/encrypt_bots.py</code> trước.",
            parse_mode=ParseMode.HTML,
            reply_markup=_back_to_admin(),
        )
        return
    lines = ["<b>🤖 Bot trong catalog</b>"]
    for b in items:
        sm = b.get("summary") or {}
        lines.append(
            f"• <code>{b['bot_id']}</code> v{b['version']}\n"
            f"   {sm.get('bot_name', '?')} — {sm.get('owner', '?')}"
        )
    await q.edit_message_text(
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
        "active": "✅ Active",
        "expired": "⌛ Hết hạn",
        "revoked": "🚫 Revoked",
    }
    header = f"<b>📜 Token — {title_map.get(filter_kind, 'Tất cả')}</b>"

    if not rows:
        body = f"{header}\n\n<i>Không có token nào.</i>"
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
                f"   partner={tk.partner_id} exp={tk.expires_at:%Y-%m-%d %H:%M}\n"
                f"   jti=<code>{tk.jti[:16]}…</code>"
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
    await q.edit_message_text(body, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))


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
        await q.edit_message_text(
            "Chưa có đối tác nào. Thêm đối tác trước.",
            reply_markup=_back_to_admin(),
        )
        return
    kb = [
        [InlineKeyboardButton(_fmt_partner_short(p), callback_data=f"grant_p:{p.id}")]
        for p in partners[:20]
    ]
    kb.append([InlineKeyboardButton("⬅️ Quay lại", callback_data="menu:home")])
    await q.edit_message_text(
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
        await q.edit_message_text("Chưa có bot nào.", reply_markup=_back_to_admin())
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
    await q.edit_message_text(
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
        await q.edit_message_text("Lỗi state. Thử lại.", reply_markup=_back_to_admin())
        return

    sf = ctx.application.bot_data["session_factory"]

    def do_grant():
        with sf() as s:
            partner = s.get(Partner, partner_id)
            if not partner:
                return "❌ Không tìm thấy partner."
            grant = (
                s.query(PartnerBotGrant)
                .filter_by(partner_id=partner_id, bot_id=bot_id)
                .first()
            )
            if grant and not grant.revoked:
                return (
                    f"ℹ️ Partner <code>{partner_id}</code> đã có quyền dùng "
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
    await q.edit_message_text(
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
        await q.edit_message_text(
            "Không có grant nào đang hoạt động.", reply_markup=_back_to_admin()
        )
        return
    kb = [
        [InlineKeyboardButton(_fmt_partner_short(p), callback_data=f"rg_p:{p.id}")]
        for p in partners[:20]
    ]
    kb.append([InlineKeyboardButton("⬅️ Quay lại", callback_data="menu:home")])
    await q.edit_message_text(
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
        await q.edit_message_text(
            "Đối tác này không có grant nào.", reply_markup=_back_to_admin()
        )
        return
    kb = [
        [InlineKeyboardButton(g.bot_id, callback_data=f"rg_b:{g.bot_id}")]
        for g in grants
    ]
    kb.append([InlineKeyboardButton("⬅️ Quay lại", callback_data="menu:home")])
    await q.edit_message_text(
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
        await q.edit_message_text("Lỗi state.", reply_markup=_back_to_admin())
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
                return "Không có grant active để hủy.", []
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
                    })
            s.commit()
            return (
                f"✅ Đã hủy quyền <code>{partner_id}</code> dùng "
                f"<code>{bot_id}</code> + revoke {len(snaps)} token liên quan."
            ), snaps

    text, snaps = await asyncio.to_thread(do)
    rc = ctx.application.bot_data.get("redis_client")
    bc: BackendClient | None = ctx.application.bot_data.get("backend_client")
    grace = ctx.application.bot_data["settings"].redis_state_grace_sec
    for snap in snaps:
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
    await q.edit_message_text(
        text, parse_mode=ParseMode.HTML, reply_markup=_back_to_admin()
    )


# ───────────────────────── partner menu callbacks ─────────────────────────

async def cb_partner_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    role, partner = await _async_role(ctx, update.effective_user.id)
    if role != "partner":
        await q.edit_message_text("Partner only.")
        return
    action = q.data.split(":", 1)[1]

    if action == "home":
        ctx.user_data.clear()
        await q.edit_message_text("Menu:", reply_markup=_partner_menu())
        return

    if action == "mybots":
        await _show_partner_bots(q, ctx, partner)
        return

    if action == "mytokens":
        await _show_partner_tokens(q, ctx, partner, filter_kind="active")
        return

    if action == "issue":
        ctx.user_data["awaiting"] = "issue_user_label"
        ctx.user_data["issue_partner_id"] = partner.id
        await q.edit_message_text(
            "<b>🎫 Cấp token cho khách</b>\n\n"
            "Bước 1/3: Gõ tên/nhãn để bạn quản lý khách (vd: <i>Anh Tuấn</i>, "
            "<i>Khách-001</i>). Khách sẽ tự link MT5 account khi login.\n"
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
        await q.edit_message_text(
            "Bạn chưa được cấp quyền bot nào.\nLiên hệ admin.",
            reply_markup=_back_to_partner(),
        )
        return
    lines = ["<b>🤖 Bot bạn được phép cấp token</b>"]
    for bid in bot_ids:
        try:
            mf = reg.get_manifest(bid)
            lines.append(f"• <code>{bid}</code> v{mf['version']} — {mf['bot_name']}")
        except FileNotFoundError:
            lines.append(f"• <code>{bid}</code> <i>(không có trong catalog)</i>")
    await q.edit_message_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=_back_to_partner()
    )


async def _async_partner_stats(ctx, partner: Partner) -> dict[str, int]:
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
            return {"active": active, "expiring_soon": expiring_soon, "expired": locked, "locked": locked}

    return await asyncio.to_thread(q)


async def _show_partner_tokens(q, ctx, partner: Partner, filter_kind: str = "active"):
    sf = ctx.application.bot_data["session_factory"]
    now = datetime.utcnow()

    def db_q():
        with sf() as s:
            base = (
                s.query(Token)
                .filter_by(partner_id=partner.id)
                .order_by(Token.expires_at.desc())
                .limit(200)
                .all()
            )
            if filter_kind == "active":
                return [t for t in base if not t.revoked and t.expires_at >= now][:25]
            if filter_kind == "expired":
                return [t for t in base if not t.revoked and t.expires_at < now][:25]
            if filter_kind == "revoked":
                return [t for t in base if t.revoked][:25]
            return base[:25]

    rows = await asyncio.to_thread(db_q)

    title_map = {
        "active": "✅ Active",
        "expired": "⌛ Hết hạn",
        "revoked": "🚫 Revoked",
        "all": "Tất cả",
    }
    header = f"<b>📜 Token — {title_map.get(filter_kind, 'Active')}</b>"

    def _filter_row(active: str) -> list[InlineKeyboardButton]:
        items = [("active", "✅"), ("expired", "⌛"), ("revoked", "🚫"), ("all", "Tất cả")]
        return [
            InlineKeyboardButton(
                ("• " + label + " •") if k == active else label,
                callback_data=f"ptok_f:{k}",
            )
            for k, label in items
        ]

    kb: list[list[InlineKeyboardButton]] = [_filter_row(filter_kind)]
    if not rows:
        body = f"{header}\n\n<i>Không có token nào ở mục này.</i>"
    else:
        body = f"{header}\n<i>Tap 1 token để xem chi tiết / gia hạn / revoke.</i>"
        for tk in rows:
            bids = ",".join(json.loads(tk.bot_ids_json))
            khach = tk.end_user_username or "?"
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
            label = f"{tag} {khach} · {bids} · {tk.expires_at:%d/%m %H:%M}"
            kb.append([InlineKeyboardButton(label[:60], callback_data=f"ptok_d:{tk.jti}")])
    kb.append([InlineKeyboardButton("⬅️ Menu", callback_data="pmenu:home")])

    await q.edit_message_text(body, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))


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


async def cb_partner_token_detail(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    role, partner = await _async_role(ctx, update.effective_user.id)
    if role != "partner":
        return
    jti = q.data.split(":", 1)[1]
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
        await q.edit_message_text("Token không tồn tại.", reply_markup=_back_to_partner())
        return

    if info["revoked"]:
        status = "🚫 Revoked"
        if info["revoked_at"]:
            status += f" lúc {info['revoked_at']:%Y-%m-%d %H:%M}"
        if info["renewed_to_jti"]:
            status += f"\n   <i>(đã gia hạn thành <code>{info['renewed_to_jti'][:16]}…</code>)</i>"
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
        status = f"✅ Active (còn ~{d} ngày)"

    text = (
        f"<b>🎫 Chi tiết token</b>\n"
        f"Khách: <b>{info['khach']}</b>\n"
        f"Bot: <code>{info['bot_id']}</code>\n"
        f"Cấp: {info['issued_at']:%Y-%m-%d %H:%M}\n"
        f"Hết hạn: {info['expires_at']:%Y-%m-%d %H:%M}\n"
        f"Trạng thái: {status}\n"
        f"JTI: <code>{info['jti']}</code>"
    )

    kb: list[list[InlineKeyboardButton]] = []
    can_renew = not info["renewed_to_jti"] and (
        not info["revoked"] or info["expires_at"] >= now - timedelta(days=7)
    )
    can_revoke = not info["revoked"] and info["expires_at"] >= now
    if can_renew:
        kb.append([InlineKeyboardButton("♻️ Gia hạn", callback_data=f"renew_t:{info['jti']}")])
    if can_revoke:
        kb.append([InlineKeyboardButton("🚫 Revoke token này", callback_data=f"ptok_rv:{info['jti']}")])
    kb.append([InlineKeyboardButton("⬅️ Danh sách", callback_data="pmenu:mytokens")])
    kb.append([InlineKeyboardButton("🏠 Menu", callback_data="pmenu:home")])

    await q.edit_message_text(
        text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb)
    )


async def cb_partner_token_revoke(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    role, partner = await _async_role(ctx, update.effective_user.id)
    if role != "partner":
        return
    jti = q.data.split(":", 1)[1]
    sf = ctx.application.bot_data["session_factory"]

    def do():
        with sf() as s:
            tk = s.get(Token, jti)
            if not tk or tk.partner_id != partner.id:
                return "❌ Không tìm thấy token.", None
            if tk.revoked:
                return "Token đã được revoke trước đó.", None
            tk.revoked = True
            tk.revoked_at = datetime.utcnow()
            s.commit()
            khach = tk.end_user_username or "?"
            snapshot = {
                "jti": tk.jti,
                "partner_id": tk.partner_id,
                "bot_id": json.loads(tk.bot_ids_json)[0],
                "account_id": tk.account_id,
                "end_user_label": tk.end_user_username,
                "expires_at": tk.expires_at,
            }
            return f"🚫 Đã revoke token cho khách <b>{khach}</b>.", snapshot

    msg, snap = await asyncio.to_thread(do)
    if snap:
        state_mirror.mirror(
            ctx.application.bot_data.get("redis_client"),
            jti=snap["jti"], state=state_mirror.STATE_REVOKED,
            partner_id=snap["partner_id"], bot_id=snap["bot_id"],
            account_id=snap["account_id"], end_user_label=snap["end_user_label"],
            expires_at=snap["expires_at"],
            grace_sec=ctx.application.bot_data["settings"].redis_state_grace_sec,
        )
        bc: BackendClient | None = ctx.application.bot_data.get("backend_client")
        if bc:
            await bc.force_stop(
                jti=snap["jti"],
                reason=f"partner_revoke",
            )
    kb = [
        [InlineKeyboardButton("⬅️ Danh sách", callback_data="pmenu:mytokens")],
        [InlineKeyboardButton("🏠 Menu", callback_data="pmenu:home")],
    ]
    await q.edit_message_text(msg, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))


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
    await q.edit_message_text(
        f"<b>🎫 Cấp token</b>\n"
        f"Khách: <b>{ctx.user_data.get('issue_user_label')}</b>\n"
        f"Bot: <code>{bot_id}</code>\n\n"
        f"Bước 3/3: Chọn thời hạn token",
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
        await q.edit_message_text("Lỗi state.", reply_markup=_back_to_partner())
        return

    sf = ctx.application.bot_data["session_factory"]
    reg: BotRegistry = ctx.application.bot_data["registry"]
    ts: TokenService = ctx.application.bot_data["token_service"]

    def check():
        with sf() as s:
            return (
                s.query(PartnerBotGrant)
                .filter_by(partner_id=partner.id, bot_id=bot_id, revoked=False)
                .first()
                is not None
            )

    if not await asyncio.to_thread(check):
        await q.edit_message_text(
            f"Bạn không có quyền dùng bot {bot_id} nữa.",
            reply_markup=_back_to_partner(),
        )
        return
    if not reg.has(bot_id):
        await q.edit_message_text(
            f"Bot {bot_id} không còn trong catalog.",
            reply_markup=_back_to_partner(),
        )
        return

    ttl = days * 86400
    token, jti, exp = ts.issue(
        partner.id, [bot_id], ttl,
        end_user_label=user_label,
    )

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
                    created_by=f"partner:{partner.id}",
                )
            )
            s.commit()

    await asyncio.to_thread(save)
    state_mirror.mirror(
        ctx.application.bot_data.get("redis_client"),
        jti=jti, state=state_mirror.STATE_VALID,
        partner_id=partner.id, bot_id=bot_id,
        account_id=None, end_user_label=user_label,
        expires_at=exp.replace(tzinfo=None),
        grace_sec=ctx.application.bot_data["settings"].redis_state_grace_sec,
    )
    msg = (
        f"✅ <b>Token đã cấp</b>\n"
        f"Khách: <b>{user_label}</b>\n"
        f"Bot : <code>{bot_id}</code>\n"
        f"Hạn : {days} ngày (đến {exp.strftime('%Y-%m-%d %H:%M UTC')})\n"
        f"JTI : <code>{jti}</code>\n\n"
        f"<b>Token JWT</b> (gửi cho khách, chỉ hiển thị 1 lần):\n<pre>{token}</pre>\n"
        f"\n<i>Khách dán JWT này vào frontend → link MT5 account của họ 1 lần → "
        f"bật/tắt bot. Hết hạn = backend tự tắt bot.</i>"
    )
    await q.edit_message_text(
        msg, parse_mode=ParseMode.HTML, reply_markup=_back_to_partner()
    )


# ───────────────────────── renew flow (partner) ─────────────────────────

async def _show_renew_pick_token(q, ctx, partner: Partner):
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
        await q.edit_message_text(
            "Bạn chưa có token nào để gia hạn.",
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

    await q.edit_message_text(
        "<b>♻️ Gia hạn token</b>\n\nChọn token cần gia hạn:",
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
        await q.edit_message_text("Token không tồn tại.", reply_markup=_back_to_partner())
        return
    if info["renewed_to_jti"]:
        await q.edit_message_text(
            f"Token này đã được gia hạn bằng <code>{info['renewed_to_jti'][:16]}…</code>.",
            parse_mode=ParseMode.HTML,
            reply_markup=_back_to_partner(),
        )
        return

    ctx.user_data["renew_jti"] = jti
    ctx.user_data["renew_khach"] = info["khach"]
    ctx.user_data["renew_bot_id"] = info["bot_id"]

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
    await q.edit_message_text(
        f"<b>♻️ Gia hạn token</b>\n"
        f"Khách: <b>{info['khach']}</b>\n"
        f"Bot: <code>{info['bot_id']}</code>\n"
        f"Hết hạn cũ: {info['expires_at']:%Y-%m-%d %H:%M}\n\n"
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
        await q.edit_message_text("Lỗi state.", reply_markup=_back_to_partner())
        return

    sf = ctx.application.bot_data["session_factory"]
    reg: BotRegistry = ctx.application.bot_data["registry"]
    ts: TokenService = ctx.application.bot_data["token_service"]

    def check_grant():
        with sf() as s:
            return (
                s.query(PartnerBotGrant)
                .filter_by(partner_id=partner.id, bot_id=bot_id, revoked=False)
                .first()
                is not None
            )

    if not await asyncio.to_thread(check_grant):
        await q.edit_message_text(
            f"Bạn không còn quyền dùng bot {bot_id}.",
            reply_markup=_back_to_partner(),
        )
        return
    if not reg.has(bot_id):
        await q.edit_message_text(
            f"Bot {bot_id} không còn trong catalog.",
            reply_markup=_back_to_partner(),
        )
        return

    ttl = days * 86400
    token, new_jti, exp = ts.issue(
        partner.id, [bot_id], ttl, end_user_label=khach,
    )

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
                    created_by=f"partner:{partner.id}:renew",
                )
            )
            old = s.get(Token, old_jti)
            if old:
                old.revoked = True
                old.revoked_at = datetime.utcnow()
                old.renewed_to_jti = new_jti
            s.commit()

    await asyncio.to_thread(save)
    rc = ctx.application.bot_data.get("redis_client")
    grace = ctx.application.bot_data["settings"].redis_state_grace_sec
    state_mirror.mirror(
        rc, jti=new_jti, state=state_mirror.STATE_VALID,
        partner_id=partner.id, bot_id=bot_id,
        account_id=None, end_user_label=khach,
        expires_at=exp.replace(tzinfo=None), grace_sec=grace,
    )
    state_mirror.mirror(
        rc, jti=old_jti, state=state_mirror.STATE_REVOKED,
        partner_id=partner.id, bot_id=bot_id,
        account_id=None, end_user_label=khach,
        expires_at=exp.replace(tzinfo=None), grace_sec=grace,
    )
    bc: BackendClient | None = ctx.application.bot_data.get("backend_client")
    transfer_result: dict | None = None
    if bc:
        # Transfer account link old → new TRƯỚC (để khách không phải re-link MT5).
        transfer_result = await bc.transfer_link(old_jti=old_jti, new_jti=new_jti)
        # KHÔNG force_stop khi renew — bot sẽ tiếp tục chạy với token mới qua link đã transfer.
        # Nếu khách dùng JWT cũ → backend trả 403 token_revoked, khách phải dán JWT mới.
    transfer_note = ""
    if transfer_result:
        if transfer_result.get("transferred"):
            transfer_note = (
                f"\n✅ Link MT5 account đã chuyển sang token mới — khách "
                f"<b>không cần</b> nhập lại account_id."
            )
        elif transfer_result.get("note") == "old_jti_has_no_link":
            transfer_note = (
                f"\n⚠️ Khách chưa từng link account với token cũ — khi dán "
                f"JWT mới sẽ cần link MT5 account lần đầu."
            )
    msg = (
        f"♻️ <b>Đã gia hạn</b>\n"
        f"Khách: <b>{khach}</b>\n"
        f"Bot : <code>{bot_id}</code>\n"
        f"Hạn mới: {days} ngày (đến {exp:%Y-%m-%d %H:%M UTC})\n"
        f"JTI cũ: <code>{old_jti[:16]}…</code> (đã revoke)\n"
        f"JTI mới: <code>{new_jti}</code>"
        f"{transfer_note}\n\n"
        f"<b>Token JWT mới</b> (gửi cho khách):\n<pre>{token}</pre>"
    )
    await q.edit_message_text(
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
            await update.message.reply_text("Lỗi state.", reply_markup=_admin_menu())
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
                "Bạn không có bot nào đang dùng được. Liên hệ admin.",
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
                "⚠️ <b>Token sắp hết hạn</b>\n"
                f"Khách: <b>{it['khach']}</b>\n"
                f"Bot: <code>{it['bot_id']}</code>\n"
                f"Còn ~{hours_left}h (đến {it['expires_at']:%Y-%m-%d %H:%M UTC})\n\n"
                f"Vào /start → <b>♻️ Gia hạn token</b> để cấp tiếp."
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
                    stop_note = "\n✅ Bot trên Windows runner đã được tự dừng."
                elif action == "noop":
                    stop_note = "\n(Bot không chạy nên không cần dừng.)"
                elif action == "error":
                    stop_note = f"\n⚠️ Backend không dừng được: {force_result.get('note')}"
            text = (
                "🔒 <b>Token đã hết hạn — Bot bị khóa</b>\n"
                f"Khách: <b>{it['khach']}</b>\n"
                f"Bot: <code>{it['bot_id']}</code>\n"
                f"Account MT5: <code>{it['account_id']}</code>\n"
                f"Hết hạn: {it['expires_at']:%Y-%m-%d %H:%M UTC}"
                f"{stop_note}\n\n"
                f"Vào /start → <b>♻️ Gia hạn token</b> để cấp lại quyền."
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
    app.create_task(_notifier_loop(app), name="expiry_notifier")
    app.create_task(_lock_check_loop(app), name="lock_checker")
    app.create_task(force_stop_retry.run_loop(app), name="force_stop_retry")


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    log.exception("telegram handler error: %s", ctx.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                f"Lỗi nội bộ: {type(ctx.error).__name__}"
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
    app.add_handler(CallbackQueryHandler(cb_partner_token_detail, pattern=r"^ptok_d:"))
    app.add_handler(CallbackQueryHandler(cb_partner_token_revoke, pattern=r"^ptok_rv:"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, msg_router))
    app.add_error_handler(on_error)

    return app
