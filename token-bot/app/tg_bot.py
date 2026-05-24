from __future__ import annotations

import asyncio
import base64
import html
import json
import logging
import math
import socket
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.request import HTTPXRequest
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
from .db import initialize_schema, make_engine, make_session_factory
from . import force_stop_retry
from .models import (
    Base,
    Partner,
    PartnerBillingNotice,
    PartnerBillingSnapshot,
    PartnerBotGrant,
    PartnerMember,
    PartnerPaymentProof,
    Token,
)
from . import state_mirror
from .token_service import TokenService


log = logging.getLogger("token-bot.tg")
_ORIGINAL_GETADDRINFO = socket.getaddrinfo


# ───────────────────────── helpers ─────────────────────────

PARTNER_MEMBER_ROLES = {"owner", "operator", "accountant", "viewer"}
PARTNER_ROLE_LABELS = {
    "owner": "Chủ đối tác",
    "operator": "Vận hành",
    "accountant": "Kế toán",
    "viewer": "Chỉ xem",
}
PARTNER_ROLE_PERMISSIONS = {
    "token_write": {"owner", "operator"},
    "billing_pay": {"owner", "accountant"},
    "billing_view": {"owner", "operator", "accountant", "viewer"},
    "view": {"owner", "operator", "accountant", "viewer"},
}


def _normalize_partner_member_role(role: str | None) -> str:
    value = str(role or "").strip().lower()
    return value if value in PARTNER_MEMBER_ROLES else "operator"


def _partner_role_label(role: str | None) -> str:
    return PARTNER_ROLE_LABELS.get(_normalize_partner_member_role(role), "Vận hành")


def _partner_can(member_role: str | None, permission: str) -> bool:
    return _normalize_partner_member_role(member_role) in PARTNER_ROLE_PERMISSIONS.get(permission, set())


def _partner_permission_denied_text(member_role: str | None, action_text: str) -> str:
    return (
        f"Vai trò hiện tại của bạn là <b>{_h(_partner_role_label(member_role))}</b>.\n"
        f"Vai trò này chưa được phép {action_text}."
    )


def _member_label_from_parts(
    telegram_user_id: int | str | None,
    telegram_username: str | None = None,
    role: str | None = None,
) -> str:
    username = str(telegram_username or "").strip().lstrip("@")
    tg_id = str(telegram_user_id or "").strip()
    base = f"@{username}" if username else (f"tg:{tg_id}" if tg_id else "Không rõ")
    role_label = _partner_role_label(role)
    return f"{base} ({role_label})"


def _partner_member_label_map(s: Session, partner_id: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    partner = s.get(Partner, partner_id)
    if partner and partner.telegram_user_id:
        labels[str(partner.telegram_user_id)] = _member_label_from_parts(
            partner.telegram_user_id,
            partner.telegram_username,
            "owner",
        )
    members = (
        s.query(PartnerMember)
        .filter_by(partner_id=partner_id)
        .order_by(PartnerMember.created_at.asc())
        .all()
    )
    for member in members:
        labels[str(member.telegram_user_id)] = _member_label_from_parts(
            member.telegram_user_id,
            member.telegram_username,
            member.role,
        )
    return labels


def _partner_actor_label(
    labels: dict[str, str],
    telegram_user_id: int | str | None,
    telegram_username: str | None = None,
    role: str | None = None,
) -> str:
    tg_id = str(telegram_user_id or "").strip()
    if tg_id and tg_id in labels:
        return labels[tg_id]
    return _member_label_from_parts(tg_id or None, telegram_username, role)


def _sync_owner_member(s: Session, partner: Partner) -> PartnerMember | None:
    if not partner.telegram_user_id:
        return None
    member = (
        s.query(PartnerMember)
        .filter_by(partner_id=partner.id, telegram_user_id=partner.telegram_user_id)
        .first()
    )
    if member is None:
        member = PartnerMember(
            partner_id=partner.id,
            telegram_user_id=partner.telegram_user_id,
            telegram_username=partner.telegram_username,
            role="owner",
            active=True,
            note="legacy_owner",
        )
        s.add(member)
    else:
        member.role = "owner"
        member.active = True
        member.revoked_at = None
        if partner.telegram_username and not member.telegram_username:
            member.telegram_username = partner.telegram_username
    return member


def _partner_member_role_sync(
    s: Session,
    *,
    partner_id: str,
    telegram_user_id: int,
) -> str | None:
    partner = s.get(Partner, partner_id)
    if not partner or not partner.active:
        return None
    if partner.telegram_user_id == telegram_user_id:
        _sync_owner_member(s, partner)
        return "owner"
    member = (
        s.query(PartnerMember)
        .filter_by(partner_id=partner_id, telegram_user_id=telegram_user_id, active=True)
        .first()
    )
    if member:
        return _normalize_partner_member_role(member.role)
    return None


def _role(ctx: ContextTypes.DEFAULT_TYPE, tg_id: int) -> tuple[str, Partner | None]:
    settings: Settings = ctx.application.bot_data["settings"]
    if tg_id in settings.admin_id_set():
        return "admin", None
    sf = ctx.application.bot_data["session_factory"]
    with sf() as s:
        member = (
            s.query(PartnerMember)
            .join(Partner, PartnerMember.partner_id == Partner.id)
            .filter(PartnerMember.telegram_user_id == tg_id)
            .filter(PartnerMember.active == True)  # noqa: E712
            .filter(Partner.active == True)  # noqa: E712
            .order_by(PartnerMember.created_at.desc())
            .first()
        )
        if member and member.partner:
            return "partner", member.partner
        p = s.query(Partner).filter_by(telegram_user_id=tg_id, active=True).first()
        if p:
            _sync_owner_member(s, p)
            s.commit()
            return "partner", p
    return "stranger", None


async def _async_role(ctx, tg_id):
    return await asyncio.to_thread(_role, ctx, tg_id)


async def _async_partner_member_role(ctx, partner: Partner, tg_id: int) -> str | None:
    sf = ctx.application.bot_data["session_factory"]

    def db_q():
        with sf() as s:
            role = _partner_member_role_sync(s, partner_id=partner.id, telegram_user_id=tg_id)
            s.commit()
            return role

    return await asyncio.to_thread(db_q)


def _admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("👥 Danh sách đối tác", callback_data="menu:partners")],
            [InlineKeyboardButton("➕ Thêm đối tác", callback_data="menu:add_partner")],
            [InlineKeyboardButton("👤 Thêm member đối tác", callback_data="menu:add_member")],
            [InlineKeyboardButton("🤖 Kho bot đã mã hóa", callback_data="menu:bots")],
            [InlineKeyboardButton("🔑 Cấp quyền bot cho đối tác", callback_data="menu:grant")],
            [InlineKeyboardButton("🚫 Hủy quyền", callback_data="menu:revokegrant")],
            [InlineKeyboardButton("📜 Mã đã cấp", callback_data="menu:tokens")],
            [InlineKeyboardButton("💳 Bill chờ duyệt", callback_data="menu:billing")],
        ]
    )


def _partner_menu(member_role: str | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton("🤖 Bot của tôi", callback_data="pmenu:mybots")],
        [InlineKeyboardButton("🔎 Tra cứu", callback_data="pmenu:search")],
    ]
    if member_role is None or _partner_can(member_role, "token_write"):
        rows.insert(1, [InlineKeyboardButton("🎫 Tạo mã", callback_data="pmenu:issue")])
        rows.append([InlineKeyboardButton("🚫 Khóa khách", callback_data="pmenu:lock")])
    rows.append(
        [
            InlineKeyboardButton("📜 Mã đã cấp", callback_data="pmenu:mytokens"),
            InlineKeyboardButton("📊 Báo cáo", callback_data="ptok_sum:month"),
        ]
    )
    if member_role is None or _partner_can(member_role, "billing_view"):
        rows.append([InlineKeyboardButton("💳 Công nợ", callback_data="pbill:summary")])
    return InlineKeyboardMarkup(rows)


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


def _json_list(value) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except Exception:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _install_telegram_ipv4_preference() -> None:
    """Prefer IPv4 for Telegram API when the host has broken IPv6 routing."""

    if getattr(socket, "_cntx_telegram_ipv4_first", False):
        return

    def _getaddrinfo_ipv4_first(host, port, family=0, type=0, proto=0, flags=0):
        infos = _ORIGINAL_GETADDRINFO(host, port, family, type, proto, flags)
        if str(host or "").lower() == "api.telegram.org":
            infos = sorted(infos, key=lambda item: 0 if item[0] == socket.AF_INET else 1)
        return infos

    socket.getaddrinfo = _getaddrinfo_ipv4_first
    socket._cntx_telegram_ipv4_first = True  # type: ignore[attr-defined]


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


def _norm_bot_code(value) -> str:
    return str(value or "").strip()


def _bot_item_code(item: dict) -> str:
    return _norm_bot_code(
        item.get("bot_code")
        or item.get("bot_id")
        or item.get("code")
        or item.get("id")
    )


def _bot_item_name(item: dict) -> str:
    return str(
        item.get("bot_name")
        or item.get("display_name")
        or item.get("name")
        or _bot_item_code(item)
        or ""
    ).strip()


def _bot_item_label(item: dict) -> str:
    code = _bot_item_code(item)
    name = _bot_item_name(item)
    version = str(item.get("version") or "").strip()
    left = name if name and name != code else code
    if name and code and name != code:
        left = f"{name} · {code}"
    if version:
        return f"{left} v{version}"
    return left or code


async def _available_bot_items_from_bot_data(bot_data: dict) -> list[dict]:
    bc: BackendClient | None = bot_data.get("backend_client")
    if bc is not None and bc.enabled:
        payload = await bc.list_available_bots()
        raw_items = payload.get("items") if isinstance(payload, dict) else None
        if isinstance(raw_items, list) and raw_items:
            items = [item for item in raw_items if isinstance(item, dict) and _bot_item_code(item)]
            if items:
                return sorted(items, key=lambda item: (_bot_item_name(item).lower(), _bot_item_code(item).lower()))

    reg: BotRegistry | None = bot_data.get("registry")
    if reg is None:
        return []
    local_items = await asyncio.to_thread(reg.list_encrypted)
    out: list[dict] = []
    for item in local_items:
        summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        code = _norm_bot_code(item.get("bot_id") or summary.get("bot_id"))
        if not code:
            continue
        out.append(
            {
                "bot_code": code,
                "bot_name": summary.get("bot_name") or code,
                "version": item.get("version") or summary.get("version") or "",
                "catalog_source": "local_registry",
            }
        )
    return sorted(out, key=lambda item: (_bot_item_name(item).lower(), _bot_item_code(item).lower()))


async def _partner_allowed_bot_ids(ctx, partner: Partner) -> list[str]:
    return await _partner_allowed_bot_ids_from_bot_data(ctx.application.bot_data, partner)


async def _partner_allowed_bot_ids_from_bot_data(bot_data: dict, partner: Partner) -> list[str]:
    sf = bot_data["session_factory"]

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
    return await _sync_product_partner_from_bot_data(ctx.application.bot_data, partner, allowed_bot_ids)


async def _sync_product_partner_from_bot_data(
    bot_data: dict,
    partner: Partner,
    allowed_bot_ids: list[str],
) -> bool:
    bc: BackendClient | None = bot_data.get("backend_client")
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
    return await _backend_partner_token_report_from_bot_data(
        ctx.application.bot_data,
        partner,
        scope=scope,
        query=query,
        limit=limit,
    )


async def _backend_partner_token_report_from_bot_data(
    bot_data: dict,
    partner: Partner,
    *,
    scope: str = "all",
    query: str | None = None,
    limit: int = 500,
):
    bc: BackendClient | None = bot_data.get("backend_client")
    if bc is None or not bc.enabled:
        return None
    allowed_bot_ids = await _partner_allowed_bot_ids_from_bot_data(bot_data, partner)
    if not await _sync_product_partner_from_bot_data(bot_data, partner, allowed_bot_ids):
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


def _billing_tz(settings: Settings) -> ZoneInfo:
    try:
        return ZoneInfo(str(settings.partner_billing_timezone or "Asia/Ho_Chi_Minh"))
    except Exception:
        return ZoneInfo("Asia/Ho_Chi_Minh")


def _billing_local_now(settings: Settings) -> datetime:
    return datetime.now(_billing_tz(settings))


def _billing_cycle_days(settings: Settings) -> int:
    return max(1, int(getattr(settings, "partner_billing_cycle_days", 30) or 30))


def _billing_day_start(value: datetime, tz: ZoneInfo) -> datetime:
    local = value.astimezone(tz)
    return datetime(local.year, local.month, local.day, tzinfo=tz)


def _billing_period_bounds(settings: Settings, now: datetime) -> tuple[datetime, datetime]:
    return now - timedelta(days=_billing_cycle_days(settings)), now


def _billing_period_key(period_start: datetime, period_end: datetime) -> str:
    return f"{period_start:%Y-%m-%d}_{period_end:%Y-%m-%d}"


def _billing_month_key(now: datetime, *, settings: Settings | None = None) -> str:
    if settings is None:
        return now.strftime("%Y-%m")
    start, _ = _billing_period_bounds(settings, now)
    return _billing_period_key(start, now)


def _billing_week_key(now: datetime) -> str:
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


def _usd(amount: int | float | None) -> str:
    return f"{int(amount or 0):,} USD"


def _parse_iso_dt(value) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt


def _billing_charge_for_counts(
    bot_data: dict,
    *,
    user_fee_units: int,
    support_active_users: int,
) -> dict[str, int]:
    settings: Settings = bot_data["settings"]
    user_count = max(0, int(user_fee_units or 0))
    support_count = max(0, int(support_active_users or 0))
    block_size = max(1, int(settings.partner_support_block_size or 15))
    blocks = math.ceil(support_count / block_size) if support_count > 0 else 0
    user_fee = user_count * max(0, int(settings.partner_user_fee_usd or 0))
    support_fee = blocks * max(0, int(settings.partner_support_fee_usd or 0))
    infra_fee = blocks * max(0, int(settings.partner_infra_fee_usd or 0))
    total = user_fee + support_fee + infra_fee
    return {
        "billable_users": user_count,
        "support_active_users": support_count,
        "block_size": block_size,
        "blocks": blocks,
        "user_fee_usd": user_fee,
        "support_fee_usd": support_fee,
        "infra_fee_usd": infra_fee,
        "total_fee_usd": total,
    }


def _billing_charge_for_count(bot_data: dict, billable_users: int) -> dict[str, int]:
    return _billing_charge_for_counts(
        bot_data,
        user_fee_units=billable_users,
        support_active_users=billable_users,
    )


def _billable_user_key(item: dict) -> str:
    for key in ("bound_user_id", "redeemed_by_telegram_id", "bound_account_id", "token_id"):
        value = str(item.get(key) or "").strip()
        if value:
            return f"{key}:{value.lower()}"
    return ""


def _billable_fee_unit_key(item: dict) -> str:
    for key in ("entitlement_id", "token_id"):
        value = str(item.get(key) or "").strip()
        if value:
            return f"{key}:{value.lower()}"
    return _billable_user_key(item)


def _billable_seat_key(item: dict) -> str:
    bot_code = str(item.get("bot_code") or "").strip().lower()
    for user_field in ("bound_user_id", "redeemed_by_telegram_id", "bound_account_id"):
        user_value = str(item.get(user_field) or "").strip().lower()
        if user_value and bot_code:
            return f"{user_field}:{user_value}:bot:{bot_code}"
    for deployment_field in ("deployment_id", "bound_deployment_id"):
        deployment_value = str(item.get(deployment_field) or "").strip().lower()
        if deployment_value:
            return f"{deployment_field}:{deployment_value}"
    user_key = _billable_user_key(item)
    if user_key and bot_code:
        return f"{user_key}:bot:{bot_code}"
    return user_key


def _billable_activation_dt(item: dict) -> datetime | None:
    return _parse_iso_dt(item.get("entitlement_starts_at") or item.get("redeemed_at"))


def _billable_window_end_dt(item: dict, *, default_days: int) -> datetime | None:
    activated_at = _billable_activation_dt(item)
    if activated_at is None:
        return None
    natural_end = _parse_iso_dt(item.get("entitlement_expires_at") or item.get("billing_end_at"))
    if natural_end is None:
        try:
            duration_days = int(item.get("duration_days") or default_days)
        except Exception:
            duration_days = default_days
        natural_end = activated_at + timedelta(days=max(1, duration_days))
    stopped_at = _parse_iso_dt(item.get("entitlement_stopped_at") or item.get("revoked_at"))
    if stopped_at is not None and stopped_at < natural_end:
        return stopped_at
    return natural_end


def _billing_window_overlaps(
    item: dict,
    *,
    period_start: datetime,
    period_end: datetime,
    default_days: int,
) -> bool:
    tz = period_start.tzinfo if isinstance(period_start.tzinfo, ZoneInfo) else ZoneInfo("UTC")
    activated_at = _billable_activation_dt(item)
    end_at = _billable_window_end_dt(item, default_days=default_days)
    if activated_at is None or end_at is None:
        return False
    support_start = _billing_day_start(activated_at, tz)
    support_end = _billing_day_start(end_at, tz)
    return (
        support_start < period_end
        and support_end > period_start
    )


def _tag_billing_item(item: dict, *, charge_kind: str, period_key: str) -> dict:
    tagged = dict(item)
    tagged["_billing_charge_kind"] = charge_kind
    tagged["_billing_period_key"] = period_key
    return tagged


def _billing_charge_detail_key(item: dict) -> str:
    period_key = str(item.get("_billing_period_key") or "")
    charge_kind = str(item.get("_billing_charge_kind") or "")
    if charge_kind == "user_fee":
        base_key = _billable_fee_unit_key(item)
    elif charge_kind == "support":
        base_key = _billable_seat_key(item)
    else:
        base_key = _billable_fee_unit_key(item) or _billable_seat_key(item)
    return f"{period_key}:{charge_kind}:{base_key}"


def _billable_month_items(
    report: dict,
    *,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
) -> list[dict]:
    items: list[dict] = []
    for item in list((report or {}).get("items") or []):
        activated_at = _billable_activation_dt(item)
        if activated_at is None or not _billable_fee_unit_key(item):
            continue
        if period_start is not None and activated_at < period_start:
            continue
        if period_end is not None and activated_at > period_end:
            continue
        # User starts counting as soon as Mini App entitlement is activated,
        # regardless of whether they run the bot later.
        items.append(item)
    return items


def _billable_month_user_count(
    report: dict,
    *,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
) -> int:
    return len(
        {
            _billable_fee_unit_key(item)
            for item in _billable_month_items(report, period_start=period_start, period_end=period_end)
        }
    )


def _billing_cycle_anchor(
    settings: Settings,
    report: dict,
    now: datetime,
    stored_anchor: datetime | None = None,
) -> datetime:
    tz = _billing_tz(settings)
    if stored_anchor is not None:
        if stored_anchor.tzinfo is None:
            return stored_anchor.replace(tzinfo=tz)
        return stored_anchor.astimezone(tz)
    activated_dates: list[datetime] = []
    for item in list((report or {}).get("items") or []):
        activated_at = _billable_activation_dt(item)
        if activated_at is None or not _billable_fee_unit_key(item):
            continue
        activated_dates.append(activated_at.astimezone(tz))
    if not activated_dates:
        base = now.astimezone(tz)
    else:
        base = min(activated_dates)
    return datetime(base.year, base.month, base.day, tzinfo=tz)


def _has_billable_activation(report: dict) -> bool:
    for item in list((report or {}).get("items") or []):
        if _billable_activation_dt(item) is not None and _billable_fee_unit_key(item):
            return True
    return False


async def _partner_billing_anchor_from_bot_data(
    bot_data: dict,
    *,
    partner_id: str,
    report: dict,
    now: datetime,
) -> datetime | None:
    settings: Settings = bot_data["settings"]
    if not _has_billable_activation(report):
        return None
    sf = bot_data["session_factory"]

    def db_q() -> datetime | None:
        with sf() as s:
            partner = s.get(Partner, partner_id)
            if not partner:
                return None
            if partner.billing_anchor_at is not None:
                return partner.billing_anchor_at
            anchor = _billing_cycle_anchor(settings, report, now)
            partner.billing_anchor_at = anchor.replace(tzinfo=None)
            s.commit()
            return partner.billing_anchor_at

    return await asyncio.to_thread(db_q)


def _cycle_index_for_dt(anchor: datetime, value: datetime, cycle_days: int) -> int:
    seconds = max(0, int((value - anchor).total_seconds()))
    cycle_seconds = max(1, int(cycle_days) * 86_400)
    return seconds // cycle_seconds


def _billing_cycle_summary(
    bot_data: dict,
    report: dict,
    *,
    now: datetime,
    confirmed_paid_usd: int,
    stored_anchor: datetime | None = None,
) -> dict:
    settings: Settings = bot_data["settings"]
    tz = _billing_tz(settings)
    cycle_days = _billing_cycle_days(settings)
    anchor = _billing_cycle_anchor(settings, report, now, stored_anchor=stored_anchor)
    now_local = now.astimezone(tz)
    current_index = _cycle_index_for_dt(anchor, now_local, cycle_days)
    current_start = anchor + timedelta(days=current_index * cycle_days)
    current_end = current_start + timedelta(days=cycle_days)

    cycle_user_fee_items: dict[int, dict[str, dict]] = {}
    cycle_user_fee_first_seen: dict[tuple[int, str], datetime] = {}
    cycle_support_items: dict[int, dict[str, dict]] = {}
    cycle_support_latest_end: dict[tuple[int, str], datetime] = {}
    for item in list((report or {}).get("items") or []):
        fee_key = _billable_fee_unit_key(item)
        seat_key = _billable_seat_key(item)
        activated_at = _billable_activation_dt(item)
        if not fee_key or not seat_key or activated_at is None:
            continue
        activated_local = activated_at.astimezone(tz)
        if activated_local > now_local:
            continue
        activation_index = _cycle_index_for_dt(anchor, activated_local, cycle_days)
        if activation_index <= current_index:
            first_key = (activation_index, fee_key)
            previous = cycle_user_fee_first_seen.get(first_key)
            if previous is None or activated_local < previous:
                cycle_user_fee_first_seen[first_key] = activated_local
                period_key = _billing_period_key(
                    anchor + timedelta(days=activation_index * cycle_days),
                    anchor + timedelta(days=(activation_index + 1) * cycle_days),
                )
                cycle_user_fee_items.setdefault(activation_index, {})[fee_key] = _tag_billing_item(
                    item,
                    charge_kind="user_fee",
                    period_key=period_key,
                )

        end_at = _billable_window_end_dt(item, default_days=cycle_days)
        if end_at is None:
            continue
        end_local = end_at.astimezone(tz)
        support_start_local = _billing_day_start(activated_local, tz)
        support_end_local = _billing_day_start(end_local, tz)
        if support_end_local <= support_start_local or support_end_local <= anchor:
            continue
        first_support_index = max(0, _cycle_index_for_dt(anchor, support_start_local, cycle_days))
        last_support_index = min(
            current_index,
            _cycle_index_for_dt(anchor, support_end_local - timedelta(microseconds=1), cycle_days),
        )
        for idx in range(first_support_index, last_support_index + 1):
            start = anchor + timedelta(days=idx * cycle_days)
            end = start + timedelta(days=cycle_days)
            if not _billing_window_overlaps(
                item,
                period_start=start,
                period_end=end,
                default_days=cycle_days,
            ):
                continue
            support_key = (idx, seat_key)
            previous_end = cycle_support_latest_end.get(support_key)
            if previous_end is not None and previous_end >= support_end_local:
                continue
            cycle_support_latest_end[support_key] = support_end_local
            cycle_support_items.setdefault(idx, {})[seat_key] = _tag_billing_item(
                item,
                charge_kind="support",
                period_key=_billing_period_key(start, end),
            )

    cycle_summaries: list[dict] = []
    accrued_total = 0
    previous_total = 0
    current_user_fee_items = list(cycle_user_fee_items.get(current_index, {}).values())
    current_support_items = list(cycle_support_items.get(current_index, {}).values())
    current_amounts = _billing_charge_for_counts(
        bot_data,
        user_fee_units=len(current_user_fee_items),
        support_active_users=len(current_support_items),
    )
    for idx in range(current_index + 1):
        start = anchor + timedelta(days=idx * cycle_days)
        end = start + timedelta(days=cycle_days)
        period_key = _billing_period_key(start, end)
        user_fee_items = list(cycle_user_fee_items.get(idx, {}).values())
        support_items = list(cycle_support_items.get(idx, {}).values())
        amounts = _billing_charge_for_counts(
            bot_data,
            user_fee_units=len(user_fee_items),
            support_active_users=len(support_items),
        )
        detail_seen: set[str] = set()
        detail_items: list[dict] = []
        for detail_item in [*user_fee_items, *support_items]:
            detail_key = _billing_charge_detail_key(detail_item)
            if detail_key in detail_seen:
                continue
            detail_seen.add(detail_key)
            detail_items.append(detail_item)
        accrued_total += int(amounts["total_fee_usd"])
        if idx < current_index:
            previous_total += int(amounts["total_fee_usd"])
        cycle_summaries.append(
            {
                "index": idx,
                "period_start": start,
                "period_end": end,
                "period_key": period_key,
                "items": detail_items,
                "user_fee_items": user_fee_items,
                "support_items": support_items,
                **amounts,
            }
        )

    paid = max(0, int(confirmed_paid_usd or 0))
    previous_due = max(0, previous_total - paid)
    return {
        "cycle_anchor": anchor,
        "cycle_index": current_index,
        "period_start": current_start,
        "period_end": current_end,
        "billing_month": _billing_period_key(current_start, current_end),
        "cycle_days": cycle_days,
        "cycle_summaries": cycle_summaries,
        "current_cycle_items": [
            item
            for summary in cycle_summaries[current_index:current_index + 1]
            for item in list(summary.get("items") or [])
        ],
        "current_user_fee_items": current_user_fee_items,
        "current_support_items": current_support_items,
        "all_billable_items": [
            item
            for summary in cycle_summaries
            for item in list(summary.get("items") or [])
        ],
        "previous_total_usd": previous_total,
        "previous_due_usd": previous_due,
        "current_cycle_total_usd": int(current_amounts["total_fee_usd"]),
        "monthly_total_usd": int(current_amounts["total_fee_usd"]),
        "accrued_total_usd": accrued_total,
        "confirmed_paid_usd": paid,
        "amount_due_usd": max(0, accrued_total - paid),
        **current_amounts,
    }


def _issuer_info_from_item(item: dict, issuer_map: dict[str, dict] | None = None) -> dict:
    issuer_map = issuer_map or {}
    token_id = str(item.get("token_id") or "").strip()
    local = issuer_map.get(token_id) or {}
    issuer_id = item.get("issued_by_telegram_id") or local.get("issued_by_telegram_id")
    issuer_username = item.get("issued_by_username") or local.get("issued_by_username")
    issuer_label = item.get("issuer_label") or local.get("issuer_label")
    if not issuer_label:
        issuer_label = _member_label_from_parts(issuer_id, issuer_username)
    return {
        "issued_by_telegram_id": str(issuer_id or "").strip() or None,
        "issued_by_username": str(issuer_username or "").strip() or None,
        "issuer_label": issuer_label,
    }


def _billing_detail_from_backend_item(item: dict, issuer_map: dict[str, dict] | None = None) -> dict:
    issuer = _issuer_info_from_item(item, issuer_map)
    return {
        "user_key": _billable_user_key(item),
        "token_id": item.get("token_id"),
        "customer_label": item.get("customer_label"),
        "bot_code": item.get("bot_code"),
        "charge_kind": item.get("_billing_charge_kind"),
        "billing_period_key": item.get("_billing_period_key"),
        "activated_at": item.get("entitlement_starts_at") or item.get("redeemed_at"),
        "expires_at": item.get("entitlement_expires_at"),
        "status_code": item.get("status_code"),
        **issuer,
    }


def _group_billing_details(items: list[dict]) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    by_issuer: dict[str, int] = {}
    by_bot: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        issuer = str(item.get("issuer_label") or item.get("issued_by_telegram_id") or "Không rõ")
        bot = str(item.get("bot_code") or "?")
        by_issuer[issuer] = by_issuer.get(issuer, 0) + 1
        by_bot[bot] = by_bot.get(bot, 0) + 1
    return (
        sorted(by_issuer.items(), key=lambda it: (-it[1], it[0].lower())),
        sorted(by_bot.items(), key=lambda it: (-it[1], it[0].lower())),
    )


async def _token_issuer_map_from_bot_data(
    bot_data: dict,
    *,
    partner_id: str,
    token_ids: list[str],
) -> dict[str, dict]:
    ids = [str(token_id or "").strip() for token_id in token_ids if str(token_id or "").strip()]
    if not ids:
        return {}
    sf = bot_data["session_factory"]

    def db_q() -> dict[str, dict]:
        with sf() as s:
            labels = _partner_member_label_map(s, partner_id)
            rows = (
                s.query(Token)
                .filter(Token.partner_id == partner_id)
                .filter(Token.jti.in_(ids[:500]))
                .all()
            )
            result: dict[str, dict] = {}
            for tk in rows:
                issuer_id = tk.issued_by_telegram_id
                issuer_username = tk.issued_by_username
                result[tk.jti] = {
                    "issued_by_telegram_id": str(issuer_id or "").strip() or None,
                    "issued_by_username": issuer_username,
                    "issuer_label": _partner_actor_label(labels, issuer_id, issuer_username),
                }
            return result

    return await asyncio.to_thread(db_q)


async def _partner_member_label_map_from_bot_data(bot_data: dict, partner_id: str) -> dict[str, str]:
    sf = bot_data["session_factory"]

    def db_q() -> dict[str, str]:
        with sf() as s:
            return _partner_member_label_map(s, partner_id)

    return await asyncio.to_thread(db_q)


async def _confirmed_paid_for_partner(
    bot_data: dict,
    *,
    partner_id: str,
) -> int:
    sf = bot_data["session_factory"]

    def db_q() -> int:
        with sf() as s:
            query = (
                s.query(PartnerPaymentProof)
                .filter_by(partner_id=partner_id, status="confirmed")
            )
            rows = query.all()
            return sum(int(row.amount_confirmed_usd or row.amount_due_snapshot_usd or 0) for row in rows)

    return await asyncio.to_thread(db_q)


async def _pending_payment_count(
    bot_data: dict,
    *,
    partner_id: str,
) -> int:
    sf = bot_data["session_factory"]

    def db_q() -> int:
        with sf() as s:
            query = (
                s.query(PartnerPaymentProof)
                .filter_by(partner_id=partner_id, status="submitted")
            )
            return query.count()

    return await asyncio.to_thread(db_q)


async def _partner_billing_snapshot_from_bot_data(bot_data: dict, partner: Partner) -> dict | None:
    settings: Settings = bot_data["settings"]
    now = _billing_local_now(settings)
    report = await _backend_partner_token_report_from_bot_data(
        bot_data,
        partner,
        scope="all",
        limit=5000,
    )
    if report is None:
        return None
    billing_anchor = await _partner_billing_anchor_from_bot_data(
        bot_data,
        partner_id=partner.id,
        report=report,
        now=now,
    )
    confirmed_paid = await _confirmed_paid_for_partner(
        bot_data,
        partner_id=partner.id,
    )
    pending_count = await _pending_payment_count(
        bot_data,
        partner_id=partner.id,
    )
    amounts = _billing_cycle_summary(
        bot_data,
        report,
        now=now,
        confirmed_paid_usd=confirmed_paid,
        stored_anchor=billing_anchor,
    )
    return {
        "partner_id": partner.id,
        "partner_name": partner.name,
        "week_key": _billing_week_key(now),
        "generated_at": now,
        "pending_payment_count": pending_count,
        "report": report,
        **amounts,
    }


async def _partner_billing_snapshot(ctx, partner: Partner) -> dict | None:
    return await _partner_billing_snapshot_from_bot_data(ctx.application.bot_data, partner)


def _partner_billing_text(snapshot: dict, *, title: str = "💳 Công nợ tuần") -> str:
    billing_month = str(snapshot.get("billing_month") or "")
    period_start = snapshot.get("period_start")
    period_end = snapshot.get("period_end")
    cycle_days = int(snapshot.get("cycle_days") or 30)
    billable_users = int(snapshot.get("billable_users") or 0)
    support_active_users = int(snapshot.get("support_active_users") or 0)
    block_size = int(snapshot.get("block_size") or 15)
    blocks = int(snapshot.get("blocks") or 0)
    pending_count = int(snapshot.get("pending_payment_count") or 0)
    due = int(snapshot.get("amount_due_usd") or 0)
    previous_due = int(snapshot.get("previous_due_usd") or 0)
    return (
        f"<b>{title}</b>\n"
        f"Chu kỳ tính phí: <b>{cycle_days} ngày</b>\n"
        f"Từ: <b>{period_start:%Y-%m-%d}</b> đến <b>{period_end:%Y-%m-%d}</b>\n"
        f"Mã chu kỳ: <code>{_h(billing_month)}</code>\n"
        f"Lượt user kích hoạt trong chu kỳ: <b>{billable_users}</b>\n"
        f"User còn hạn tính support/hạ tầng: <b>{support_active_users}</b>\n"
        f"Block support/hạ tầng: <b>{blocks}</b> block / {block_size} user còn hạn\n\n"
        f"Phí user kích hoạt: <b>{_usd(snapshot.get('user_fee_usd'))}</b>\n"
        f"Support/kỹ thuật: <b>{_usd(snapshot.get('support_fee_usd'))}</b>\n"
        f"Hạ tầng: <b>{_usd(snapshot.get('infra_fee_usd'))}</b>\n\n"
        f"Tổng chu kỳ hiện tại: <b>{_usd(snapshot.get('current_cycle_total_usd'))}</b>\n"
        f"Nợ chu kỳ trước còn treo: <b>{_usd(previous_due)}</b>\n"
        f"Tổng đã phát sinh: <b>{_usd(snapshot.get('accrued_total_usd'))}</b>\n"
        f"Đã admin xác nhận: <b>{_usd(snapshot.get('confirmed_paid_usd'))}</b>\n"
        f"Còn cần thanh toán: <b>{_usd(due)}</b>\n"
        f"Bill đang chờ duyệt: <b>{pending_count}</b>\n\n"
        f"<i>Phí user tính theo lượt kích hoạt. Support/hạ tầng tính theo user còn hạn giao với chu kỳ server "
        f"{cycle_days} ngày; nợ cũ không tự mất khi sang chu kỳ mới.</i>"
    )


def _partner_billing_keyboard(snapshot: dict, member_role: str | None = None) -> InlineKeyboardMarkup:
    kb: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton("📄 Xem chi tiết khách", callback_data="pbill:customers")],
        [InlineKeyboardButton("🧾 Lịch sử đã thanh toán", callback_data="pbill:history")],
    ]
    if int(snapshot.get("amount_due_usd") or 0) > 0 and (
        member_role is None or _partner_can(member_role, "billing_pay")
    ):
        kb.append(
            [
                InlineKeyboardButton(
                    "✅ Tôi đã chuyển khoản",
                    callback_data=f"pbill_pay:{snapshot['billing_month']}:{snapshot['week_key']}",
                )
            ]
        )
    kb.append([InlineKeyboardButton("⬅️ Menu", callback_data="pmenu:home")])
    return InlineKeyboardMarkup(kb)


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
        member_role = await _async_partner_member_role(ctx, partner, u.id)
        await update.message.reply_text(
            f"👋 Xin chào đối tác <b>{u.full_name}</b>\n"
            f"Mã đối tác: <code>{partner.id}</code>\n\n"
            f"Vai trò của bạn: <b>{_h(_partner_role_label(member_role))}</b>\n\n"
            f"📊 <b>Tổng quan</b>\n"
            f"  ✅ Mã đang mở: <b>{stats['active']}</b>\n"
            f"  🟢 Khách đang dùng bot: <b>{stats['running']}</b>\n"
            f"  📅 Lượt user tính phí chu kỳ này: <b>{stats['billable_customers']}</b>\n"
            f"  🚫 Đã khóa: <b>{stats['locked']}</b>\n\n"
            f"Chọn chức năng:",
            parse_mode=ParseMode.HTML,
            reply_markup=_partner_menu(member_role),
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
    role, partner = await _async_role(ctx, update.effective_user.id)
    if role == "admin":
        await update.message.reply_text("Menu chính:", reply_markup=_admin_menu())
    elif role == "partner":
        member_role = await _async_partner_member_role(ctx, partner, update.effective_user.id)
        await update.message.reply_text("Menu chính:", reply_markup=_partner_menu(member_role))
    else:
        await update.message.reply_text("Bạn chưa có quyền.")


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    role, partner = await _async_role(ctx, update.effective_user.id)
    if role == "admin":
        kb = _admin_menu()
    elif role == "partner":
        member_role = await _async_partner_member_role(ctx, partner, update.effective_user.id)
        kb = _partner_menu(member_role)
    else:
        kb = None
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

    if action == "billing":
        await _show_pending_payment_proofs_admin(q, ctx)
        return

    if action == "add_partner":
        ctx.user_data["awaiting"] = "add_partner_tg_id"
        await _safe_edit_message_text(q,
            "<b>➕ Thêm đối tác</b>\n\nGửi Telegram ID của đối tác (số nguyên).\n"
            "Gõ /cancel để hủy.",
            parse_mode=ParseMode.HTML,
        )
        return

    if action == "add_member":
        await _show_member_pick_partner(q, ctx)
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
                    s.query(PartnerMember)
                    .filter_by(partner_id=p.id, active=True)
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
    for p, g, member_count in rows:
        status = "✅" if p.active else "🚫"
        logical_members = member_count
        if p.telegram_user_id and logical_members <= 0:
            logical_members = 1
        lines.append(
            f"{status} <code>{p.id}</code> — {p.name}\n"
            f"   Owner: {p.telegram_user_id or '-'} · member: {logical_members} · quyền bot: {g}"
        )
    await _safe_edit_message_text(q,
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=_back_to_admin(),
    )


async def _show_bots_admin(q, ctx):
    items = await _available_bot_items_from_bot_data(ctx.application.bot_data)
    if not items:
        await _safe_edit_message_text(q,
            "Chưa có bot nào trong catalog để cấp quyền.\n"
            "Khi Windows runner hoặc bot-trading báo bot lên backend, danh sách này sẽ tự cập nhật.",
            parse_mode=ParseMode.HTML,
            reply_markup=_back_to_admin(),
        )
        return
    lines = ["<b>🤖 Kho bot hệ thống</b>"]
    for b in items:
        source = str(b.get("catalog_source") or "").strip()
        source_text = f" · {source}" if source else ""
        lines.append(
            f"• <code>{_h(_bot_item_code(b))}</code>\n"
            f"   {_h(_bot_item_label(b))}{_h(source_text)}"
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


async def _show_pending_payment_proofs_admin(q, ctx):
    sf = ctx.application.bot_data["session_factory"]

    def db_q():
        with sf() as s:
            rows = (
                s.query(PartnerPaymentProof)
                .filter_by(status="submitted")
                .order_by(PartnerPaymentProof.submitted_at.desc())
                .limit(20)
                .all()
            )
            return [
                {
                    "id": row.id,
                    "partner_id": row.partner_id,
                    "partner_name": row.partner.name if row.partner else row.partner_id,
                    "billing_month": row.billing_month,
                    "week_key": row.week_key,
                    "amount": row.amount_due_snapshot_usd,
                    "submitted_at": row.submitted_at,
                }
                for row in rows
            ]

    rows = await asyncio.to_thread(db_q)
    lines = ["<b>💳 Bill chuyển khoản chờ duyệt</b>"]
    kb: list[list[InlineKeyboardButton]] = []
    if not rows:
        lines.append("\nKhông có bill nào đang chờ admin xác nhận.")
    for row in rows:
        lines.append(
            f"• #{row['id']} · <b>{_h(row['partner_name'])}</b> "
            f"({row['partner_id']}) · {_h(row['billing_month'])} · {_usd(row['amount'])}"
        )
        kb.append(
            [
                InlineKeyboardButton(f"✅ Xác nhận #{row['id']}", callback_data=f"pbill_confirm:{row['id']}"),
                InlineKeyboardButton(f"❌ Từ chối #{row['id']}", callback_data=f"pbill_reject:{row['id']}"),
            ]
        )
    kb.append([InlineKeyboardButton("⬅️ Menu chính", callback_data="menu:home")])
    await _safe_edit_message_text(q, "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))


# ───── Partner member flow (admin) ─────

async def _show_member_pick_partner(q, ctx):
    sf = ctx.application.bot_data["session_factory"]

    def db_q():
        with sf() as s:
            return s.query(Partner).filter_by(active=True).order_by(Partner.created_at.desc()).all()

    partners = await asyncio.to_thread(db_q)
    if not partners:
        await _safe_edit_message_text(q, "Chưa có đối tác nào. Thêm đối tác trước.", reply_markup=_back_to_admin())
        return
    kb = [
        [InlineKeyboardButton(_fmt_partner_short(p), callback_data=f"member_p:{p.id}")]
        for p in partners[:30]
    ]
    kb.append([InlineKeyboardButton("⬅️ Quay lại", callback_data="menu:home")])
    await _safe_edit_message_text(
        q,
        "<b>👤 Thêm member đối tác</b>\n\nChọn team đối tác cần thêm người:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def cb_member_pick_partner(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await _is_admin(ctx, update.effective_user.id):
        return
    partner_id = q.data.split(":", 1)[1]
    ctx.user_data["member_partner_id"] = partner_id
    ctx.user_data["awaiting"] = "add_member_text"
    await _safe_edit_message_text(
        q,
        "<b>👤 Thêm member đối tác</b>\n\n"
        f"Đối tác: <code>{_h(partner_id)}</code>\n"
        "Gửi theo mẫu:\n"
        "<code>TELEGRAM_ID role</code>\n\n"
        "Role hợp lệ: <code>owner</code>, <code>operator</code>, <code>accountant</code>, <code>viewer</code>.\n"
        "Nếu bỏ role, hệ thống mặc định là <code>operator</code>.\n\n"
        "Ví dụ: <code>123456789 operator</code>",
        parse_mode=ParseMode.HTML,
    )


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

    items = await _available_bot_items_from_bot_data(ctx.application.bot_data)
    if not items:
        await _safe_edit_message_text(q, "Chưa có bot nào.", reply_markup=_back_to_admin())
        return
    kb = [
        [
            InlineKeyboardButton(
                _bot_item_label(b),
                callback_data=f"grant_b:{_bot_item_code(b)}",
            )
        ]
        for b in items
        if _bot_item_code(b)
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
    member_role = await _async_partner_member_role(ctx, partner, update.effective_user.id)
    action = q.data.split(":", 1)[1]

    if action == "home":
        ctx.user_data.clear()
        await _safe_edit_message_text(q, "Menu:", reply_markup=_partner_menu(member_role))
        return

    if action == "mybots":
        await _show_partner_bots(q, ctx, partner)
        return

    if action == "mytokens":
        await _show_partner_tokens(q, ctx, partner, filter_kind="active")
        return

    if action == "lock":
        if not _partner_can(member_role, "token_write"):
            await _safe_edit_message_text(
                q,
                _partner_permission_denied_text(member_role, "khóa bot khách"),
                parse_mode=ParseMode.HTML,
                reply_markup=_back_to_partner(),
            )
            return
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
        if not _partner_can(member_role, "token_write"):
            await _safe_edit_message_text(
                q,
                _partner_permission_denied_text(member_role, "tạo mã cho khách"),
                parse_mode=ParseMode.HTML,
                reply_markup=_back_to_partner(),
            )
            return
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

async def _show_partner_bots(q, ctx, partner: Partner):
    sf = ctx.application.bot_data["session_factory"]

    def db_q():
        with sf() as s:
            grants = (
                s.query(PartnerBotGrant)
                .filter_by(partner_id=partner.id, revoked=False)
                .all()
            )
            return [g.bot_id for g in grants]

    bot_ids = await asyncio.to_thread(db_q)
    available_items = await _available_bot_items_from_bot_data(ctx.application.bot_data)
    available_by_code = {_bot_item_code(item): item for item in available_items if _bot_item_code(item)}
    if not bot_ids:
        await _safe_edit_message_text(q,
            "Bạn chưa được cấp quyền bot nào.\nLiên hệ đội vận hành CNTx Labs.",
            reply_markup=_back_to_partner(),
        )
        return
    lines = ["<b>🤖 Bot bạn được phép cấp mã</b>"]
    for bid in bot_ids:
        item = available_by_code.get(bid)
        if item:
            lines.append(f"• <code>{_h(bid)}</code> — {_h(_bot_item_label(item))}")
        else:
            lines.append(f"• <code>{_h(bid)}</code> <i>(bot này chưa sẵn sàng trong catalog)</i>")
    await _safe_edit_message_text(q,
        "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=_back_to_partner()
    )


async def _async_partner_stats(ctx, partner: Partner) -> dict[str, int]:
    snapshot = await _partner_billing_snapshot(ctx, partner)
    if snapshot is not None:
        support_items = list(snapshot.get("current_support_items") or [])
        running = sum(1 for item in support_items if str(item.get("status_code") or "") == "running")
        revoked = sum(1 for item in support_items if str(item.get("status_code") or "") == "revoked")
        expired = sum(1 for item in support_items if str(item.get("status_code") or "") == "expired")
        support_active_users = int(snapshot.get("support_active_users") or 0)
        return {
            "active": max(0, support_active_users - revoked - expired),
            "running": running,
            "billable_customers": int(snapshot.get("billable_users") or 0),
            "billing_days": 0,
            "expiring_soon": 0,
            "expired": expired,
            "locked": revoked,
        }

    sf = ctx.application.bot_data["session_factory"]
    now = datetime.utcnow()

    def q():
        with sf() as s:
            tokens = (
                s.query(Token)
                .filter_by(partner_id=partner.id)
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
            f"<i>Chọn 1 mã để xem chi tiết hoặc khóa quyền.</i>"
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


async def _show_backend_token_detail(q, item: dict, member_role: str | None = None):
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
    if _partner_can(member_role, "token_write"):
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
            f"<i>Chọn 1 mã để xem chi tiết hoặc khóa quyền.</i>"
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
    member_role = await _async_partner_member_role(ctx, partner, update.effective_user.id)
    jti = q.data.split(":", 1)[1]
    backend_report = await _backend_partner_token_report(ctx, partner, scope="all", query=jti, limit=20)
    for item in list((backend_report or {}).get("items") or []):
        if str(item.get("token_id") or "") == jti:
            await _show_backend_token_detail(q, item, member_role)
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
    elif info["locked_at"] is not None:
        status = (
            f"🔒 Đã khóa do hết hạn lúc {info['locked_at']:%Y-%m-%d %H:%M}\n"
            f"   Bot đã tự dừng cho khách này. Hãy cấp mã mới để mở lại."
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
    can_revoke = not info["revoked"] and info["expires_at"] >= now
    if can_revoke and _partner_can(member_role, "token_write"):
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
    member_role = await _async_partner_member_role(ctx, partner, update.effective_user.id)
    if not _partner_can(member_role, "token_write"):
        await _safe_edit_message_text(
            q,
            _partner_permission_denied_text(member_role, "khóa bot khách"),
            parse_mode=ParseMode.HTML,
            reply_markup=_back_to_partner(),
        )
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
    member_role = await _async_partner_member_role(ctx, partner, update.effective_user.id)
    if not _partner_can(member_role, "token_write"):
        await _safe_edit_message_text(
            q,
            _partner_permission_denied_text(member_role, "khóa bot khách"),
            parse_mode=ParseMode.HTML,
            reply_markup=_back_to_partner(),
        )
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


# ───────────────────────── weekly partner billing ─────────────────────────

async def _partner_billing_chat_ids_from_bot_data(bot_data: dict, partner_id: str) -> list[int]:
    sf = bot_data["session_factory"]

    def db_q() -> list[int]:
        with sf() as s:
            partner = s.get(Partner, partner_id)
            if not partner or not partner.active:
                return []
            chat_ids: list[int] = []
            if partner.telegram_user_id:
                chat_ids.append(int(partner.telegram_user_id))
            members = (
                s.query(PartnerMember)
                .filter_by(partner_id=partner_id, active=True)
                .filter(PartnerMember.role.in_(["owner", "accountant"]))
                .all()
            )
            for member in members:
                chat_ids.append(int(member.telegram_user_id))
            unique: list[int] = []
            for chat_id in chat_ids:
                if chat_id not in unique:
                    unique.append(chat_id)
            return unique

    return await asyncio.to_thread(db_q)


async def _show_partner_billing(q, ctx, partner: Partner):
    snapshot = await _partner_billing_snapshot(ctx, partner)
    if snapshot is None:
        await _safe_edit_message_text(
            q,
            "Hệ thống công nợ chưa sẵn sàng. Vui lòng thử lại sau vài phút.",
            reply_markup=_back_to_partner(),
        )
        return
    member_role = await _async_partner_member_role(ctx, partner, q.from_user.id) if q.from_user else None
    await _safe_edit_message_text(
        q,
        _partner_billing_text(snapshot),
        parse_mode=ParseMode.HTML,
        reply_markup=_partner_billing_keyboard(snapshot, member_role),
    )


async def _show_partner_billing_customers(q, ctx, partner: Partner):
    snapshot = await _partner_billing_snapshot(ctx, partner)
    if snapshot is None:
        await _safe_edit_message_text(q, "Chưa lấy được dữ liệu công nợ.", reply_markup=_back_to_partner())
        return
    user_fee_items = list(
        snapshot.get("current_user_fee_items")
        or _billable_month_items(
            snapshot.get("report") or {},
            period_start=snapshot.get("period_start"),
            period_end=snapshot.get("period_end"),
        )
    )
    support_items = list(snapshot.get("current_support_items") or [])
    all_items = [*user_fee_items, *support_items]
    issuer_map = await _token_issuer_map_from_bot_data(
        ctx.application.bot_data,
        partner_id=partner.id,
        token_ids=[str(item.get("token_id") or "") for item in all_items],
    )
    member_labels = await _partner_member_label_map_from_bot_data(ctx.application.bot_data, partner.id)
    detail_items: list[dict] = []
    lines = [
        f"<b>📄 Chi tiết khách tính phí</b>",
        f"Chu kỳ: <code>{_h(snapshot.get('billing_month'))}</code>",
        "<i>Phí user theo lượt kích hoạt; block support/hạ tầng theo user còn hạn trong chu kỳ server.</i>",
        "",
        "<b>Lượt kích hoạt tính phí user</b>",
    ]
    seen: set[str] = set()
    for item in user_fee_items:
        key = _billable_fee_unit_key(item)
        if key in seen:
            continue
        seen.add(key)
        detail = _billing_detail_from_backend_item(item, issuer_map)
        if detail.get("issued_by_telegram_id"):
            detail["issuer_label"] = _partner_actor_label(
                member_labels,
                detail.get("issued_by_telegram_id"),
                detail.get("issued_by_username"),
            )
        detail_items.append(detail)
        if len(seen) <= 30:
            lines.append(
                f"• <b>{_h(item.get('customer_label') or 'Không tên')}</b> · "
                f"{_h(item.get('bot_code') or '?')} · "
                f"kích hoạt {_backend_short_date(item.get('redeemed_at'))} · "
                f"hết hạn {_backend_short_date(item.get('entitlement_expires_at'))}\n"
                f"  Tạo bởi: <code>{_h(detail.get('issuer_label') or 'Không rõ')}</code>"
            )
    if not seen:
        lines.append("Chưa có lượt kích hoạt nào trong chu kỳ này.")

    lines.extend(["", "<b>User còn hạn tính block support/hạ tầng</b>"])
    support_seen: set[str] = set()
    support_detail_items: list[dict] = []
    for item in support_items:
        key = _billable_seat_key(item)
        if key in support_seen:
            continue
        support_seen.add(key)
        detail = _billing_detail_from_backend_item(item, issuer_map)
        if detail.get("issued_by_telegram_id"):
            detail["issuer_label"] = _partner_actor_label(
                member_labels,
                detail.get("issued_by_telegram_id"),
                detail.get("issued_by_username"),
            )
        support_detail_items.append(detail)
        if len(support_seen) <= 30:
            lines.append(
                f"• <b>{_h(item.get('customer_label') or 'Không tên')}</b> · "
                f"{_h(item.get('bot_code') or '?')} · "
                f"từ {_backend_short_date(item.get('entitlement_starts_at') or item.get('redeemed_at'))} · "
                f"đến {_backend_short_date(item.get('entitlement_expires_at'))}"
            )
    if not support_seen:
        lines.append("Chưa có user còn hạn trong chu kỳ này.")

    detail_items.extend(support_detail_items)
    if detail_items:
        by_issuer, by_bot = _group_billing_details(detail_items)
        lines.extend(["", "<b>Theo người tạo</b>"])
        for label, count in by_issuer[:12]:
            lines.append(f"• <code>{_h(label)}</code>: <b>{count}</b> dòng")
        lines.extend(["", "<b>Theo bot</b>"])
        for bot_code, count in by_bot[:12]:
            lines.append(f"• <code>{_h(bot_code)}</code>: <b>{count}</b> dòng")
    if len(user_fee_items) > 30:
        lines.append(f"... còn {len(user_fee_items) - 30} lượt kích hoạt khác.")
    if len(support_items) > 30:
        lines.append(f"... còn {len(support_items) - 30} user support/hạ tầng khác.")
    kb = [
        [InlineKeyboardButton("💳 Quay lại công nợ", callback_data="pbill:summary")],
        [InlineKeyboardButton("⬅️ Menu", callback_data="pmenu:home")],
    ]
    await _safe_edit_message_text(q, "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))


async def _show_partner_billing_history(q, ctx, partner: Partner):
    sf = ctx.application.bot_data["session_factory"]

    def db_q():
        with sf() as s:
            rows = (
                s.query(PartnerBillingSnapshot)
                .filter_by(partner_id=partner.id)
                .order_by(PartnerBillingSnapshot.created_at.desc())
                .limit(12)
                .all()
            )
            return [
                {
                    "id": row.id,
                    "period": row.billing_period_key,
                    "week": row.week_key,
                    "users": row.billable_users,
                    "support_users": row.support_active_users,
                    "blocks": row.blocks,
                    "user_fee": row.user_fee_usd,
                    "support_fee": row.support_fee_usd,
                    "infra_fee": row.infra_fee_usd,
                    "total": row.total_fee_usd,
                    "confirmed": row.confirmed_amount_usd,
                    "due_after": row.amount_due_after_usd,
                    "created_at": row.created_at,
                }
                for row in rows
            ]

    rows = await asyncio.to_thread(db_q)
    lines = ["<b>🧾 Lịch sử đã thanh toán</b>"]
    kb: list[list[InlineKeyboardButton]] = []
    if not rows:
        lines.append("\nChưa có kỳ nào được admin xác nhận thanh toán.")
    for row in rows:
        lines.append(
            f"• <code>#{row['id']}</code> · <code>{_h(row['period'])}</code>\n"
            f"  Chu kỳ này: lượt user <b>{row['users']}</b> · "
            f"user còn hạn <b>{row['support_users']}</b> · Block <b>{row['blocks']}</b> · "
            f"User fee {_usd(row['user_fee'])} · Support {_usd(row['support_fee'])} · "
            f"Hạ tầng {_usd(row['infra_fee'])}\n"
            f"  Đã xác nhận: <b>{_usd(row['confirmed'])}</b> · Còn lại sau xác nhận: <b>{_usd(row['due_after'])}</b>"
        )
        kb.append([InlineKeyboardButton(f"📄 Chi tiết #{row['id']}", callback_data=f"pbill_snap:{row['id']}")])
    kb.append([InlineKeyboardButton("💳 Công nợ hiện tại", callback_data="pbill:summary")])
    kb.append([InlineKeyboardButton("⬅️ Menu", callback_data="pmenu:home")])
    await _safe_edit_message_text(q, "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))


async def _show_partner_billing_snapshot_detail(q, ctx, partner: Partner, snapshot_id: int):
    sf = ctx.application.bot_data["session_factory"]

    def db_q():
        with sf() as s:
            row = s.get(PartnerBillingSnapshot, snapshot_id)
            if not row or row.partner_id != partner.id:
                return None
            return {
                "id": row.id,
                "period": row.billing_period_key,
                "week": row.week_key,
                "period_start_at": row.period_start_at,
                "period_end_at": row.period_end_at,
                "cycle_days": row.cycle_days,
                "users": row.billable_users,
                "support_users": row.support_active_users,
                "block_size": row.block_size,
                "blocks": row.blocks,
                "user_fee": row.user_fee_usd,
                "support_fee": row.support_fee_usd,
                "infra_fee": row.infra_fee_usd,
                "total": row.total_fee_usd,
                "paid_before": row.confirmed_paid_before_usd,
                "confirmed": row.confirmed_amount_usd,
                "due_after": row.amount_due_after_usd,
                "items": _json_list(row.item_details_json),
                "created_at": row.created_at,
            }

    row = await asyncio.to_thread(db_q)
    if not row:
        await _safe_edit_message_text(q, "Không tìm thấy bản đối soát này.", reply_markup=_back_to_partner())
        return
    lines = [
        f"<b>📄 Chi tiết đối soát #{row['id']}</b>",
        f"Chu kỳ: <code>{_h(row['period'])}</code>",
        f"Từ <b>{row['period_start_at']:%Y-%m-%d}</b> đến <b>{row['period_end_at']:%Y-%m-%d}</b>",
        "",
        f"Lượt user kích hoạt: <b>{row['users']}</b>",
        f"User còn hạn tính support/hạ tầng: <b>{row['support_users']}</b>",
        f"Block: <b>{row['blocks']}</b> / {row['block_size']} user còn hạn",
        f"Phí user kích hoạt: <b>{_usd(row['user_fee'])}</b>",
        f"Support/kỹ thuật: <b>{_usd(row['support_fee'])}</b>",
        f"Hạ tầng: <b>{_usd(row['infra_fee'])}</b>",
        f"Tổng đã phát sinh tới lúc xác nhận: <b>{_usd(row['total'])}</b>",
        f"Đã xác nhận trước đó: <b>{_usd(row['paid_before'])}</b>",
        f"Bill này xác nhận: <b>{_usd(row['confirmed'])}</b>",
        f"Còn lại sau xác nhận: <b>{_usd(row['due_after'])}</b>",
    ]
    by_issuer, by_bot = _group_billing_details(row["items"])
    if by_issuer:
        lines.extend(["", "<b>Theo người tạo</b>"])
        for label, count in by_issuer[:12]:
            lines.append(f"• <code>{_h(label)}</code>: <b>{count}</b> dòng")
    if by_bot:
        lines.extend(["", "<b>Theo bot</b>"])
        for bot_code, count in by_bot[:12]:
            lines.append(f"• <code>{_h(bot_code)}</code>: <b>{count}</b> dòng")
    if by_issuer or by_bot:
        lines.extend(["", "<b>Khách trong bản đối soát</b>"])
    else:
        lines.extend(["", "<b>Khách trong bản đối soát</b>"])
    for item in row["items"][:30]:
        if not isinstance(item, dict):
            continue
        charge_label = "user fee" if item.get("charge_kind") == "user_fee" else "support"
        lines.append(
            f"• <code>{_h(charge_label)}</code> · <b>{_h(item.get('customer_label') or 'Không tên')}</b> · "
            f"{_h(item.get('bot_code') or '?')} · "
            f"kỳ {_h(item.get('billing_period_key') or row['period'])} · "
            f"kích hoạt {_h(item.get('activated_at') or '-')}\n"
            f"  Tạo bởi: <code>{_h(item.get('issuer_label') or item.get('issued_by_telegram_id') or 'Không rõ')}</code>"
        )
    if not row["items"]:
        lines.append("Không có chi tiết khách được lưu.")
    if len(row["items"]) > 30:
        lines.append(f"... còn {len(row['items']) - 30} khách khác.")
    kb = [
        [InlineKeyboardButton("🧾 Lịch sử đã thanh toán", callback_data="pbill:history")],
        [InlineKeyboardButton("⬅️ Menu", callback_data="pmenu:home")],
    ]
    await _safe_edit_message_text(q, "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))


async def cb_partner_billing_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    role, partner = await _async_role(ctx, update.effective_user.id)
    if role != "partner":
        await _safe_edit_message_text(q, "Chỉ đối tác đã đăng ký mới dùng được chức năng này.")
        return
    action = q.data.split(":", 1)[1]
    if action == "customers":
        await _show_partner_billing_customers(q, ctx, partner)
        return
    if action == "history":
        await _show_partner_billing_history(q, ctx, partner)
        return
    await _show_partner_billing(q, ctx, partner)


async def cb_partner_billing_snapshot_detail(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    role, partner = await _async_role(ctx, update.effective_user.id)
    if role != "partner":
        return
    try:
        snapshot_id = int(q.data.split(":", 1)[1])
    except Exception:
        return
    await _show_partner_billing_snapshot_detail(q, ctx, partner, snapshot_id)


async def cb_partner_billing_pay(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    role, partner = await _async_role(ctx, update.effective_user.id)
    if role != "partner":
        return
    member_role = await _async_partner_member_role(ctx, partner, update.effective_user.id)
    if not _partner_can(member_role, "billing_pay"):
        await _safe_edit_message_text(
            q,
            _partner_permission_denied_text(member_role, "gửi bill thanh toán"),
            parse_mode=ParseMode.HTML,
            reply_markup=_back_to_partner(),
        )
        return
    snapshot = await _partner_billing_snapshot(ctx, partner)
    if snapshot is None:
        await _safe_edit_message_text(q, "Chưa lấy được công nợ. Vui lòng thử lại sau vài phút.", reply_markup=_back_to_partner())
        return
    amount_due = int(snapshot.get("amount_due_usd") or 0)
    if amount_due <= 0:
        await _safe_edit_message_text(q, "Tháng này chưa còn khoản nào cần thanh toán.", reply_markup=_back_to_partner())
        return
    if int(snapshot.get("pending_payment_count") or 0) > 0:
        await _safe_edit_message_text(
            q,
            "Đối tác đang có bill chuyển khoản chờ admin duyệt.\n"
            "Vui lòng chờ admin xác nhận hoặc từ chối bill cũ trước khi gửi bill mới.",
            reply_markup=_back_to_partner(),
        )
        return
    ctx.user_data["awaiting"] = "partner_payment_photo"
    ctx.user_data["payment_partner_id"] = partner.id
    ctx.user_data["payment_billing_month"] = snapshot["billing_month"]
    ctx.user_data["payment_week_key"] = snapshot["week_key"]
    ctx.user_data["payment_amount_due_usd"] = amount_due
    await _safe_edit_message_text(
        q,
        "<b>✅ Gửi bill chuyển khoản</b>\n\n"
        f"Số tiền đang cần thanh toán: <b>{_usd(amount_due)}</b>\n"
        "Bạn vui lòng gửi <b>ảnh bill chuyển khoản</b> vào chat này.\n\n"
        "<i>Chỉ sau khi admin xác nhận ảnh bill, khoản này mới được tính là đã thanh toán.</i>",
        parse_mode=ParseMode.HTML,
    )


async def payment_photo_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if ctx.user_data.get("awaiting") != "partner_payment_photo":
        return
    role, partner = await _async_role(ctx, update.effective_user.id)
    expected_partner_id = str(ctx.user_data.get("payment_partner_id") or "")
    if role != "partner" or partner.id != expected_partner_id:
        ctx.user_data.clear()
        return
    member_role = await _async_partner_member_role(ctx, partner, update.effective_user.id)
    if not _partner_can(member_role, "billing_pay"):
        ctx.user_data.clear()
        await update.message.reply_text(
            _partner_permission_denied_text(member_role, "gửi bill thanh toán"),
            parse_mode=ParseMode.HTML,
            reply_markup=_back_to_partner(),
        )
        return
    if not update.message or not update.message.photo:
        return
    photo = update.message.photo[-1]
    billing_month = str(ctx.user_data.get("payment_billing_month") or "")
    week_key = str(ctx.user_data.get("payment_week_key") or "")
    amount_due = int(ctx.user_data.get("payment_amount_due_usd") or 0)
    sf = ctx.application.bot_data["session_factory"]

    def save() -> int:
        with sf() as s:
            row = PartnerPaymentProof(
                partner_id=partner.id,
                billing_month=billing_month,
                week_key=week_key,
                amount_due_snapshot_usd=amount_due,
                telegram_file_id=photo.file_id,
                telegram_file_unique_id=photo.file_unique_id,
                submitted_by_telegram_id=update.effective_user.id if update.effective_user else None,
                submitted_at=datetime.utcnow(),
                status="submitted",
            )
            s.add(row)
            s.commit()
            return int(row.id)

    proof_id = await asyncio.to_thread(save)
    for key in (
        "awaiting",
        "payment_partner_id",
        "payment_billing_month",
        "payment_week_key",
        "payment_amount_due_usd",
    ):
        ctx.user_data.pop(key, None)

    admin_kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Xác nhận đã nhận tiền", callback_data=f"pbill_confirm:{proof_id}"),
                InlineKeyboardButton("❌ Từ chối bill", callback_data=f"pbill_reject:{proof_id}"),
            ]
        ]
    )
    caption = (
        f"<b>💳 Bill chuyển khoản mới</b>\n"
        f"Proof: <code>#{proof_id}</code>\n"
        f"Đối tác: <b>{_h(partner.name)}</b> (<code>{_h(partner.id)}</code>)\n"
        f"Chu kỳ: <code>{_h(billing_month)}</code> · Tuần: <code>{_h(week_key)}</code>\n"
        f"Số tiền snapshot: <b>{_usd(amount_due)}</b>"
    )
    sent_admin = 0
    for admin_id in ctx.application.bot_data["settings"].admin_id_set():
        try:
            await ctx.bot.send_photo(
                chat_id=admin_id,
                photo=photo.file_id,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=admin_kb,
            )
            sent_admin += 1
        except Exception:
            log.exception("payment_proof_admin_send_failed proof_id=%s admin_id=%s", proof_id, admin_id)
    suffix = "Admin đã nhận bill và sẽ xác nhận." if sent_admin else "Bill đã lưu, nhưng chưa cấu hình admin nhận thông báo."
    await update.message.reply_text(
        f"✅ Đã nhận ảnh bill <code>#{proof_id}</code>.\n{suffix}",
        parse_mode=ParseMode.HTML,
        reply_markup=_back_to_partner(),
    )


async def _edit_billing_admin_message(q, text: str, reply_markup: InlineKeyboardMarkup | None = None):
    try:
        if getattr(q.message, "photo", None):
            return await q.edit_message_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
        return await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
    except BadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return None
        raise


async def cb_partner_billing_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await _is_admin(ctx, update.effective_user.id):
        return
    try:
        proof_id = int(q.data.split(":", 1)[1])
    except Exception:
        return
    sf = ctx.application.bot_data["session_factory"]

    def fetch_context():
        with sf() as s:
            proof = s.get(PartnerPaymentProof, proof_id)
            if not proof:
                return None
            return {
                "status": proof.status,
                "partner_id": proof.partner_id,
                "partner_name": proof.partner.name if proof.partner else proof.partner_id,
                "partner_chat_id": proof.partner.telegram_user_id if proof.partner else None,
            }

    context = await asyncio.to_thread(fetch_context)
    if not context:
        await _edit_billing_admin_message(q, "Không tìm thấy bill này.")
        return
    if context.get("status") != "submitted":
        await _edit_billing_admin_message(q, f"Bill này đã ở trạng thái <b>{_h(context.get('status'))}</b>.")
        return

    partner_ref = SimpleNamespace(id=context["partner_id"], name=context["partner_name"])
    billing_snapshot = await _partner_billing_snapshot_from_bot_data(ctx.application.bot_data, partner_ref)
    if billing_snapshot is None:
        await _edit_billing_admin_message(
            q,
            "Chưa lấy được dữ liệu đối soát từ backend nên chưa xác nhận bill. Vui lòng thử lại sau vài phút.",
        )
        return

    def mark():
        with sf() as s:
            proof = s.get(PartnerPaymentProof, proof_id)
            if not proof:
                return None
            if proof.status != "submitted":
                return {"already_done": True, "status": proof.status}
            confirmed_amount = int(proof.amount_due_snapshot_usd or 0)
            snapshot_id = None
            period_items = list(
                billing_snapshot.get("all_billable_items")
                or _billable_month_items(
                    billing_snapshot.get("report") or {},
                    period_start=billing_snapshot.get("period_start"),
                    period_end=billing_snapshot.get("period_end"),
                )
            )
            token_ids = [
                str(item.get("token_id") or "").strip()
                for item in period_items
                if str(item.get("token_id") or "").strip()
            ]
            labels = _partner_member_label_map(s, proof.partner_id)
            issuer_map: dict[str, dict] = {}
            if token_ids:
                for tk in (
                    s.query(Token)
                    .filter(Token.partner_id == proof.partner_id)
                    .filter(Token.jti.in_(token_ids[:500]))
                    .all()
                ):
                    issuer_map[tk.jti] = {
                        "issued_by_telegram_id": str(tk.issued_by_telegram_id or "").strip() or None,
                        "issued_by_username": tk.issued_by_username,
                        "issuer_label": _partner_actor_label(
                            labels,
                            tk.issued_by_telegram_id,
                            tk.issued_by_username,
                        ),
                    }
            seen: set[str] = set()
            item_details: list[dict] = []
            for item in period_items:
                key = _billing_charge_detail_key(item)
                if not key or key in seen:
                    continue
                seen.add(key)
                detail = _billing_detail_from_backend_item(item, issuer_map)
                if detail.get("issued_by_telegram_id"):
                    detail["issuer_label"] = _partner_actor_label(
                        labels,
                        detail.get("issued_by_telegram_id"),
                        detail.get("issued_by_username"),
                    )
                item_details.append(detail)
            paid_before = int(billing_snapshot.get("confirmed_paid_usd") or 0)
            total_fee = int(
                billing_snapshot.get("accrued_total_usd")
                or billing_snapshot.get("monthly_total_usd")
                or 0
            )
            snapshot_row = PartnerBillingSnapshot(
                partner_id=proof.partner_id,
                payment_proof_id=proof.id,
                billing_period_key=str(billing_snapshot.get("billing_month") or proof.billing_month),
                week_key=str(billing_snapshot.get("week_key") or proof.week_key),
                period_start_at=billing_snapshot["period_start"].replace(tzinfo=None),
                period_end_at=billing_snapshot["period_end"].replace(tzinfo=None),
                cycle_days=int(billing_snapshot.get("cycle_days") or 30),
                billable_users=int(billing_snapshot.get("billable_users") or 0),
                support_active_users=int(billing_snapshot.get("support_active_users") or 0),
                block_size=int(billing_snapshot.get("block_size") or 15),
                blocks=int(billing_snapshot.get("blocks") or 0),
                user_fee_usd=int(billing_snapshot.get("user_fee_usd") or 0),
                support_fee_usd=int(billing_snapshot.get("support_fee_usd") or 0),
                infra_fee_usd=int(billing_snapshot.get("infra_fee_usd") or 0),
                total_fee_usd=total_fee,
                confirmed_paid_before_usd=paid_before,
                confirmed_amount_usd=confirmed_amount,
                amount_due_after_usd=max(0, total_fee - paid_before - confirmed_amount),
                item_details_json=json.dumps(item_details, ensure_ascii=False),
                created_at=datetime.utcnow(),
                created_by_admin_telegram_id=update.effective_user.id if update.effective_user else None,
            )
            s.add(snapshot_row)
            s.flush()
            snapshot_id = int(snapshot_row.id)
            proof.status = "confirmed"
            proof.amount_confirmed_usd = confirmed_amount
            proof.confirmed_by_admin_telegram_id = update.effective_user.id if update.effective_user else None
            proof.confirmed_at = datetime.utcnow()
            notify_chat_ids: list[int] = []
            if proof.partner and proof.partner.telegram_user_id:
                notify_chat_ids.append(int(proof.partner.telegram_user_id))
            if proof.submitted_by_telegram_id:
                notify_chat_ids.append(int(proof.submitted_by_telegram_id))
            members = (
                s.query(PartnerMember)
                .filter_by(partner_id=proof.partner_id, active=True)
                .filter(PartnerMember.role.in_(["owner", "accountant"]))
                .all()
            )
            for member in members:
                notify_chat_ids.append(int(member.telegram_user_id))
            unique_notify_ids: list[int] = []
            for chat_id in notify_chat_ids:
                if chat_id not in unique_notify_ids:
                    unique_notify_ids.append(chat_id)
            s.commit()
            return {
                "notify_chat_ids": unique_notify_ids,
                "partner_name": proof.partner.name if proof.partner else proof.partner_id,
                "amount": proof.amount_confirmed_usd,
                "billing_month": proof.billing_month,
                "snapshot_id": snapshot_id,
            }

    result = await asyncio.to_thread(mark)
    if not result:
        await _edit_billing_admin_message(q, "Không tìm thấy bill này.")
        return
    if result.get("already_done"):
        await _edit_billing_admin_message(q, f"Bill này đã ở trạng thái <b>{_h(result.get('status'))}</b>.")
        return
    text = (
        f"✅ Đã xác nhận bill <code>#{proof_id}</code>\n"
        f"Đối tác: <b>{_h(result.get('partner_name'))}</b>\n"
        f"Chu kỳ: <code>{_h(result.get('billing_month'))}</code>\n"
        f"Số tiền: <b>{_usd(result.get('amount'))}</b>\n"
        f"Snapshot đối soát: <code>#{result.get('snapshot_id') or '-'}</code>"
    )
    await _edit_billing_admin_message(q, text)
    for chat_id in list(result.get("notify_chat_ids") or []):
        try:
            await ctx.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"✅ Admin đã xác nhận bill <code>#{proof_id}</code>.\n"
                    f"Số tiền đã ghi nhận: <b>{_usd(result.get('amount'))}</b>"
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=_back_to_partner(),
            )
        except Exception:
            log.exception("payment_confirm_partner_notify_failed proof_id=%s chat_id=%s", proof_id, chat_id)


async def cb_partner_billing_reject(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await _is_admin(ctx, update.effective_user.id):
        return
    try:
        proof_id = int(q.data.split(":", 1)[1])
    except Exception:
        return
    sf = ctx.application.bot_data["session_factory"]

    def mark():
        with sf() as s:
            proof = s.get(PartnerPaymentProof, proof_id)
            if not proof:
                return None
            if proof.status != "submitted":
                return {"already_done": True, "status": proof.status}
            proof.status = "rejected"
            proof.rejected_by_admin_telegram_id = update.effective_user.id if update.effective_user else None
            proof.rejected_at = datetime.utcnow()
            notify_chat_ids: list[int] = []
            if proof.partner and proof.partner.telegram_user_id:
                notify_chat_ids.append(int(proof.partner.telegram_user_id))
            if proof.submitted_by_telegram_id:
                notify_chat_ids.append(int(proof.submitted_by_telegram_id))
            members = (
                s.query(PartnerMember)
                .filter_by(partner_id=proof.partner_id, active=True)
                .filter(PartnerMember.role.in_(["owner", "accountant"]))
                .all()
            )
            for member in members:
                notify_chat_ids.append(int(member.telegram_user_id))
            unique_notify_ids: list[int] = []
            for chat_id in notify_chat_ids:
                if chat_id not in unique_notify_ids:
                    unique_notify_ids.append(chat_id)
            s.commit()
            return {
                "notify_chat_ids": unique_notify_ids,
                "partner_name": proof.partner.name if proof.partner else proof.partner_id,
                "billing_month": proof.billing_month,
            }

    result = await asyncio.to_thread(mark)
    if not result:
        await _edit_billing_admin_message(q, "Không tìm thấy bill này.")
        return
    if result.get("already_done"):
        await _edit_billing_admin_message(q, f"Bill này đã ở trạng thái <b>{_h(result.get('status'))}</b>.")
        return
    await _edit_billing_admin_message(
        q,
        f"❌ Đã từ chối bill <code>#{proof_id}</code>\nĐối tác: <b>{_h(result.get('partner_name'))}</b>",
    )
    for chat_id in list(result.get("notify_chat_ids") or []):
        try:
            await ctx.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"❌ Admin đã từ chối bill <code>#{proof_id}</code>.\n"
                    "Vui lòng kiểm tra lại ảnh chuyển khoản và gửi bill mới."
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=_back_to_partner(),
            )
        except Exception:
            log.exception("payment_reject_partner_notify_failed proof_id=%s chat_id=%s", proof_id, chat_id)


async def _weekly_billing_tick(app: Application):
    settings: Settings = app.bot_data["settings"]
    if not bool(settings.partner_weekly_billing_enabled):
        return
    bc: BackendClient | None = app.bot_data.get("backend_client")
    if bc is None or not bc.enabled:
        return
    now = _billing_local_now(settings)
    if now.weekday() != int(settings.partner_billing_notice_weekday or 6):
        return
    if now.hour < int(settings.partner_billing_notice_hour or 18):
        return
    sf = app.bot_data["session_factory"]

    def fetch_partners():
        with sf() as s:
            return (
                s.query(Partner)
                .filter_by(active=True)
                .order_by(Partner.created_at.asc())
                .all()
            )

    partners = await asyncio.to_thread(fetch_partners)
    for partner in partners:
        snapshot = await _partner_billing_snapshot_from_bot_data(app.bot_data, partner)
        if snapshot is None or int(snapshot.get("amount_due_usd") or 0) <= 0:
            continue
        billing_month = str(snapshot.get("billing_month") or _billing_month_key(now, settings=settings))
        week_key = str(snapshot.get("week_key") or _billing_week_key(now))

        def notice_exists() -> bool:
            with sf() as s:
                return (
                    s.query(PartnerBillingNotice)
                    .filter_by(partner_id=partner.id, billing_month=billing_month, week_key=week_key)
                    .first()
                    is not None
                )

        if await asyncio.to_thread(notice_exists):
            continue
        chat_ids = await _partner_billing_chat_ids_from_bot_data(app.bot_data, partner.id)
        if not chat_ids:
            continue
        text = _partner_billing_text(snapshot, title="📌 Nhắc thanh toán công nợ tuần")
        sent_message_id = None
        sent_count = 0
        for chat_id in chat_ids:
            try:
                msg = await app.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=_partner_billing_keyboard(snapshot),
                )
                sent_count += 1
                if sent_message_id is None:
                    sent_message_id = msg.message_id
            except Exception:
                log.exception("weekly_billing_notice_send_failed partner_id=%s chat_id=%s", partner.id, chat_id)
        if sent_count <= 0:
            continue

        def save_notice():
            with sf() as s:
                s.add(
                    PartnerBillingNotice(
                        partner_id=partner.id,
                        billing_month=billing_month,
                        week_key=week_key,
                        billable_users=int(snapshot.get("billable_users") or 0),
                        support_active_users=int(snapshot.get("support_active_users") or 0),
                        amount_due_usd=int(snapshot.get("amount_due_usd") or 0),
                        telegram_message_id=sent_message_id,
                        sent_at=datetime.utcnow(),
                    )
                )
                s.commit()

        try:
            await asyncio.to_thread(save_notice)
        except Exception:
            log.exception("weekly_billing_notice_save_failed partner_id=%s", partner.id)


WEEKLY_BILLING_INTERVAL_SEC = 3600


async def _weekly_billing_loop(app: Application):
    log.info("weekly_billing loop started (interval=%ds)", WEEKLY_BILLING_INTERVAL_SEC)
    while True:
        try:
            await _weekly_billing_tick(app)
        except Exception:
            log.exception("weekly_billing_tick_failed")
        await asyncio.sleep(WEEKLY_BILLING_INTERVAL_SEC)


# Issue flow: pick bot button
async def cb_issue_pick_bot(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    role, partner = await _async_role(ctx, update.effective_user.id)
    if role != "partner":
        return
    member_role = await _async_partner_member_role(ctx, partner, update.effective_user.id)
    if not _partner_can(member_role, "token_write"):
        await _safe_edit_message_text(
            q,
            _partner_permission_denied_text(member_role, "tạo mã cho khách"),
            parse_mode=ParseMode.HTML,
            reply_markup=_back_to_partner(),
        )
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
    member_role = await _async_partner_member_role(ctx, partner, update.effective_user.id)
    if not _partner_can(member_role, "token_write"):
        await _safe_edit_message_text(
            q,
            _partner_permission_denied_text(member_role, "tạo mã cho khách"),
            parse_mode=ParseMode.HTML,
            reply_markup=_back_to_partner(),
        )
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
    available_items = await _available_bot_items_from_bot_data(ctx.application.bot_data)
    available_codes = {_bot_item_code(item) for item in available_items if _bot_item_code(item)}
    if _norm_bot_code(bot_id) not in available_codes:
        await _safe_edit_message_text(q,
            f"Bot {bot_id} hiện chưa sẵn sàng trong catalog.",
            reply_markup=_back_to_partner(),
        )
        return
    allowed_bot_ids = [item for item in allowed_bot_ids if _norm_bot_code(item) in available_codes]

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
                    issued_by_telegram_id=update.effective_user.id if update.effective_user else None,
                    issued_by_username=update.effective_user.username if update.effective_user else None,
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


# ───────────────────────── free-text router (conv state) ─────────────────────────

async def msg_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    state = ctx.user_data.get("awaiting")
    if not state:
        return
    text = update.message.text.strip()

    if state == "partner_payment_photo":
        await update.message.reply_text(
            "Bạn cần gửi ảnh bill chuyển khoản để admin xác nhận. Gõ /cancel nếu muốn hủy.",
            reply_markup=_back_to_partner(),
        )
        return

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
                partner = Partner(
                    id=pid,
                    name=name,
                    contact=f"tg:{tg_id}",
                    active=True,
                    telegram_user_id=tg_id,
                )
                s.add(partner)
                s.flush()
                _sync_owner_member(s, partner)
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

    if state == "add_member_text":
        if not await _is_admin(ctx, update.effective_user.id):
            ctx.user_data.clear()
            return
        partner_id = str(ctx.user_data.pop("member_partner_id", "") or "")
        ctx.user_data.pop("awaiting", None)
        if not partner_id:
            await update.message.reply_text("Phiên thao tác đã hết hạn. Thử lại nhé.", reply_markup=_admin_menu())
            return
        parts = text.split()
        if not parts:
            await update.message.reply_text("Gửi theo mẫu: TELEGRAM_ID role. Ví dụ: 123456789 operator")
            return
        try:
            member_tg_id = int(parts[0])
        except ValueError:
            await update.message.reply_text("Telegram ID phải là số nguyên. Thử lại bằng menu thêm member nhé.", reply_markup=_admin_menu())
            return
        member_role = "operator"
        member_username = None
        for part in parts[1:]:
            role_candidate = part.strip().lower()
            if role_candidate in PARTNER_MEMBER_ROLES:
                member_role = role_candidate
            elif part.startswith("@"):
                member_username = part.lstrip("@")[:64]
        sf = ctx.application.bot_data["session_factory"]

        def do_add_member():
            with sf() as s:
                partner = s.get(Partner, partner_id)
                if not partner:
                    return "❌ Không tìm thấy đối tác."
                other_owner = (
                    s.query(Partner)
                    .filter(Partner.id != partner_id)
                    .filter(Partner.active == True)  # noqa: E712
                    .filter(Partner.telegram_user_id == member_tg_id)
                    .first()
                )
                if other_owner:
                    return (
                        f"⚠️ Telegram ID này đang là owner của partner "
                        f"<code>{other_owner.id}</code> ({_h(other_owner.name)})."
                    )
                other_member = (
                    s.query(PartnerMember)
                    .join(Partner, PartnerMember.partner_id == Partner.id)
                    .filter(PartnerMember.partner_id != partner_id)
                    .filter(PartnerMember.telegram_user_id == member_tg_id)
                    .filter(PartnerMember.active == True)  # noqa: E712
                    .filter(Partner.active == True)  # noqa: E712
                    .first()
                )
                if other_member:
                    return (
                        f"⚠️ Telegram ID này đang là member của partner "
                        f"<code>{other_member.partner_id}</code>."
                    )
                member = (
                    s.query(PartnerMember)
                    .filter_by(partner_id=partner_id, telegram_user_id=member_tg_id)
                    .first()
                )
                if member is None:
                    member = PartnerMember(
                        partner_id=partner_id,
                        telegram_user_id=member_tg_id,
                        telegram_username=member_username,
                        role=member_role,
                        active=True,
                        added_by_admin_telegram_id=update.effective_user.id if update.effective_user else None,
                    )
                    s.add(member)
                    action = "Đã thêm"
                else:
                    member.role = member_role
                    member.active = True
                    member.revoked_at = None
                    if member_username:
                        member.telegram_username = member_username
                    action = "Đã cập nhật"
                s.commit()
                return (
                    f"✅ {action} member cho <code>{partner_id}</code>\n"
                    f"Telegram: <code>{member_tg_id}</code>\n"
                    f"Vai trò: <b>{_h(_partner_role_label(member_role))}</b>\n\n"
                    "Billing vẫn gom theo partner, không tách theo từng người tạo."
                )

        text_out = await asyncio.to_thread(do_add_member)
        await update.message.reply_text(text_out, parse_mode=ParseMode.HTML, reply_markup=_admin_menu())
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
        member_role = await _async_partner_member_role(ctx, partner, update.effective_user.id)
        if not _partner_can(member_role, "token_write"):
            ctx.user_data.clear()
            await update.message.reply_text(
                _partner_permission_denied_text(member_role, "tạo mã cho khách"),
                parse_mode=ParseMode.HTML,
                reply_markup=_back_to_partner(),
            )
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

        def db_q():
            with sf() as s:
                return [
                    g.bot_id
                    for g in s.query(PartnerBotGrant)
                    .filter_by(partner_id=partner.id, revoked=False)
                    .all()
                ]

        bot_ids = await asyncio.to_thread(db_q)
        available_items = await _available_bot_items_from_bot_data(ctx.application.bot_data)
        available_by_code = {_bot_item_code(item): item for item in available_items if _bot_item_code(item)}
        bot_ids = [b for b in bot_ids if _norm_bot_code(b) in available_by_code]
        if not bot_ids:
            await update.message.reply_text(
                "Bạn chưa có bot nào đang dùng được. Liên hệ đội vận hành CNTx Labs.",
                reply_markup=_partner_menu(member_role),
            )
            return
        kb = [
            [InlineKeyboardButton(_bot_item_label(available_by_code.get(b, {"bot_code": b})), callback_data=f"issue_b:{b}")]
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
                f"Vào /start → <b>🎫 Tạo mã</b> để cấp mã mới."
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
                f"Vào /start → <b>🎫 Tạo mã</b> để cấp lại quyền."
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
        asyncio.create_task(_weekly_billing_loop(app), name="weekly_billing"),
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
    initialize_schema(engine, Base.metadata)
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

    if settings.telegram_force_ipv4:
        _install_telegram_ipv4_preference()
        log.info("telegram_request_ipv4_preference enabled")

    request = HTTPXRequest(
        connection_pool_size=max(4, int(settings.telegram_connection_pool_size or 32)),
        connect_timeout=float(settings.telegram_connect_timeout_sec or 15.0),
        read_timeout=float(settings.telegram_read_timeout_sec or 25.0),
        write_timeout=float(settings.telegram_write_timeout_sec or 25.0),
        pool_timeout=float(settings.telegram_pool_timeout_sec or 10.0),
    )
    get_updates_request = HTTPXRequest(
        connection_pool_size=4,
        connect_timeout=float(settings.telegram_connect_timeout_sec or 15.0),
        read_timeout=float(settings.telegram_get_updates_read_timeout_sec or 35.0),
        write_timeout=float(settings.telegram_write_timeout_sec or 25.0),
        pool_timeout=float(settings.telegram_pool_timeout_sec or 10.0),
    )
    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .request(request)
        .get_updates_request(get_updates_request)
        .post_init(_post_init)
        .build()
    )
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
    app.add_handler(CallbackQueryHandler(cb_member_pick_partner, pattern=r"^member_p:"))
    app.add_handler(CallbackQueryHandler(cb_grant_partner, pattern=r"^grant_p:"))
    app.add_handler(CallbackQueryHandler(cb_grant_bot, pattern=r"^grant_b:"))
    app.add_handler(CallbackQueryHandler(cb_revoke_pick_bot, pattern=r"^rg_p:"))
    app.add_handler(CallbackQueryHandler(cb_revoke_confirm, pattern=r"^rg_b:"))
    app.add_handler(CallbackQueryHandler(cb_issue_pick_bot, pattern=r"^issue_b:"))
    app.add_handler(CallbackQueryHandler(cb_issue_days, pattern=r"^issue_d:"))
    app.add_handler(CallbackQueryHandler(cb_admin_tokens_filter, pattern=r"^atok:"))
    app.add_handler(CallbackQueryHandler(cb_partner_tokens_filter, pattern=r"^ptok_f:"))
    app.add_handler(CallbackQueryHandler(cb_partner_tokens_summary, pattern=r"^ptok_sum:"))
    app.add_handler(CallbackQueryHandler(cb_partner_token_detail, pattern=r"^ptok_d:"))
    app.add_handler(CallbackQueryHandler(cb_partner_token_revoke_confirm, pattern=r"^ptok_rvc:"))
    app.add_handler(CallbackQueryHandler(cb_partner_token_revoke, pattern=r"^ptok_rv:"))
    app.add_handler(CallbackQueryHandler(cb_partner_billing_snapshot_detail, pattern=r"^pbill_snap:"))
    app.add_handler(CallbackQueryHandler(cb_partner_billing_menu, pattern=r"^pbill:"))
    app.add_handler(CallbackQueryHandler(cb_partner_billing_pay, pattern=r"^pbill_pay:"))
    app.add_handler(CallbackQueryHandler(cb_partner_billing_confirm, pattern=r"^pbill_confirm:"))
    app.add_handler(CallbackQueryHandler(cb_partner_billing_reject, pattern=r"^pbill_reject:"))

    app.add_handler(MessageHandler(filters.PHOTO, payment_photo_router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, msg_router))
    app.add_error_handler(on_error)

    return app
