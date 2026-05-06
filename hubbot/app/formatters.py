# -*- coding: utf-8 -*-
"""Status icons, message formatting, safe edit helpers."""
from __future__ import annotations

import asyncio
import html
import json
from typing import Any, Optional

from telegram.error import BadRequest


def h(s: Any) -> str:
    return html.escape("" if s is None else str(s))


TELEGRAM_MAX_TEXT_LEN = 4096
SAFE_PLAIN_TEXT_CHUNK_LEN = 3900


def _normalize_message_text(text: Any) -> str:
    value = "" if text is None else str(text)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    while "\n\n\n" in value:
        value = value.replace("\n\n\n", "\n\n")
    return value.strip()


def chunk_plain_text(text: Any, max_len: int = SAFE_PLAIN_TEXT_CHUNK_LEN) -> list[str]:
    """Split plain Telegram text without parse_mode so long AI replies do not fail."""
    normalized = _normalize_message_text(text)
    if not normalized:
        return []
    limit = max(1000, min(int(max_len or SAFE_PLAIN_TEXT_CHUNK_LEN), TELEGRAM_MAX_TEXT_LEN))
    if len(normalized) <= limit:
        return [normalized]

    chunks: list[str] = []
    remaining = normalized
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, limit)
        if split_at < limit * 0.5:
            split_at = remaining.rfind(" ", 0, limit)
        if split_at < limit * 0.5:
            split_at = limit
        chunk = remaining[:split_at].rstrip()
        remaining = remaining[split_at:].lstrip()
        if chunk:
            chunks.append(chunk)
    return chunks


async def safe_reply_plain_text(message, text: Any, *, reply_markup: Optional[Any] = None) -> None:
    chunks = chunk_plain_text(text)
    if not chunks:
        return
    total = len(chunks)
    for idx, chunk in enumerate(chunks, start=1):
        final_text = chunk
        if total > 1:
            prefix = f"[{idx}/{total}]\n"
            if len(prefix) + len(final_text) > TELEGRAM_MAX_TEXT_LEN:
                final_text = final_text[: TELEGRAM_MAX_TEXT_LEN - len(prefix) - 1].rstrip()
            final_text = prefix + final_text
        markup = reply_markup if idx == 1 else None
        try:
            await message.reply_text(final_text, reply_markup=markup)
        except BadRequest as exc:
            if "message is too long" not in str(exc).lower():
                raise
            fallback = final_text[: TELEGRAM_MAX_TEXT_LEN - 80].rstrip() + "\n\n...noi dung qua dai, da cat bot."
            await message.reply_text(fallback, reply_markup=markup)
        if total > 1:
            await asyncio.sleep(0.05)


async def safe_edit_text(
    q,
    text: str,
    reply_markup: Optional[Any] = None,
    parse_mode: str = "HTML",
) -> None:
    try:
        await q.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)
    except BadRequest as e:
        err = str(e)
        if "not modified" in err or "Message is not modified" in err:
            return
        raise


def extract_id(js: Any) -> Optional[str]:
    if not js:
        return None
    if isinstance(js, str):
        if js.startswith("prof_"):
            return js
        try:
            js = json.loads(js)
        except Exception:
            return None
    if isinstance(js, dict):
        val = js.get("profile_id") or js.get("id") or js.get("uuid") or js.get("token")
        if val:
            return str(val)
        d = js.get("data", {})
        if isinstance(d, dict):
            val = d.get("profile_id") or d.get("id") or d.get("token")
            if val:
                return str(val)
    return None
