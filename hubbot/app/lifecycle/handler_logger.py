"""Pre-handler logging Telegram updates with structured context.

Runs at the lowest group (most-priority) so every incoming update is logged
before the real command/callback/message handlers fire. Binds request-scoped
identifiers (request_id, user_id, chat_id, update_id, handler) into contextvars
so any log line emitted by downstream handlers inherits them automatically.

This handler never raises and never short-circuits. If it fails it logs the
failure and lets the framework continue dispatching.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes, TypeHandler

from app.log_context import bind_log_context, new_request_id


_log = logging.getLogger("hubbot.update")

_HANDLER_LOG_GROUP_PRE = -100
_HANDLER_LOG_GROUP_POST = 9999


def update_logger_enabled() -> bool:
    raw = (os.getenv("HUBBOT_HANDLER_LOG_ENABLED") or "1").strip().lower()
    return raw in ("1", "true", "yes", "y", "on")


def _summarize_update(update: Update) -> dict[str, Any]:
    callback = update.callback_query
    msg = update.message or update.edited_message
    text = ""
    kind = "other"
    if callback is not None:
        kind = "callback_query"
        text = (callback.data or "")[:200]
    elif msg is not None:
        raw_text = (msg.text or msg.caption or "")
        kind = "command" if raw_text.startswith("/") else "message"
        text = raw_text.strip()[:200]
    elif update.inline_query is not None:
        kind = "inline_query"
        text = (update.inline_query.query or "")[:200]
    return {"kind": kind, "preview": text}


async def _pre_log(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not isinstance(update, Update):
            return
        request_id = new_request_id()
        user = update.effective_user
        chat = update.effective_chat
        user_id = user.id if user else None
        chat_id = chat.id if chat else None
        update_id = getattr(update, "update_id", None)
        meta = _summarize_update(update)

        bind_log_context(
            request_id=request_id,
            user_id=user_id,
            chat_id=chat_id,
            update_id=update_id,
            handler=meta["kind"],
        )

        # Stash start time for the post-hook latency measurement
        try:
            if context is not None and context.user_data is not None:
                context.user_data["_cntx_started_at"] = time.monotonic()
                context.user_data["_cntx_request_id"] = request_id
        except Exception:
            pass

        _log.info(
            "telegram.update.received kind=%s preview=%r user=%s chat=%s",
            meta["kind"],
            meta["preview"],
            user_id,
            chat_id,
            extra={
                "telegram_handler_kind": meta["kind"],
                "telegram_preview": meta["preview"],
                "username": (user.username or "") if user else "",
            },
        )
    except Exception:
        try:
            _log.exception("telegram.update.pre_log_failed")
        except Exception:
            pass


async def _post_log(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not isinstance(update, Update):
            return
        started_at = None
        try:
            if context is not None and context.user_data is not None:
                started_at = context.user_data.pop("_cntx_started_at", None)
                context.user_data.pop("_cntx_request_id", None)
        except Exception:
            started_at = None
        elapsed_ms = None
        if started_at is not None:
            try:
                elapsed_ms = round((time.monotonic() - float(started_at)) * 1000, 1)
            except Exception:
                elapsed_ms = None
        _log.info(
            "telegram.update.processed elapsed_ms=%s",
            elapsed_ms if elapsed_ms is not None else "?",
            extra={"elapsed_ms": elapsed_ms},
        )
    except Exception:
        pass


def install_update_logger(app) -> None:
    """Idempotent — register the pre/post update loggers if enabled."""
    if not update_logger_enabled():
        return
    app.add_handler(TypeHandler(Update, _pre_log), group=_HANDLER_LOG_GROUP_PRE)
    app.add_handler(TypeHandler(Update, _post_log), group=_HANDLER_LOG_GROUP_POST)
