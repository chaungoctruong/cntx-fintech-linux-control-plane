# -*- coding: utf-8 -*-
"""Keyboards and miniapp URL for main.py flow (entry-point)."""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from app.config import BACKEND_URL, PUBLIC_BASE_URL


def miniapp_home_url() -> str:
    base_url = (PUBLIC_BASE_URL or BACKEND_URL or "http://127.0.0.1:8001").strip().rstrip("/")
    return f"{base_url}/"


def miniapp_url(uid: int) -> str:
    return f"{miniapp_home_url()}?tg={uid}&sig=stub&v=home"


def main_menu_keyboard(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🕸️ MỞ MINI APP QUẢN LÝ BOT", web_app=WebAppInfo(url=miniapp_url(uid)))],
    ])
