# -*- coding: utf-8 -*-
"""Keyboards and miniapp URL for main.py flow (entry-point)."""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from app.config import BACKEND_URL, MINIAPP_RELEASE, PUBLIC_BASE_URL


def _cache_bust_value() -> str:
    if MINIAPP_RELEASE:
        return MINIAPP_RELEASE[:40]
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def _with_query(url: str, **params: object) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    for key, value in params.items():
        text = str(value or "").strip()
        if text:
            query[key] = text
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def miniapp_home_url() -> str:
    base_url = (PUBLIC_BASE_URL or BACKEND_URL or "http://127.0.0.1:8001").strip().rstrip("/")
    return _with_query(f"{base_url}/", v=_cache_bust_value())


def miniapp_url(uid: int) -> str:
    return _with_query(miniapp_home_url(), tg=uid, sig="stub")


def main_menu_keyboard(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🕸️ MỞ MINI APP QUẢN LÝ BOT", web_app=WebAppInfo(url=miniapp_url(uid)))],
    ])
