# -*- coding: utf-8 -*-
"""
Hubbot entry point: path setup, load_dotenv, build Application, register handlers, run_polling.
All command/callback/message/consumer/lifecycle logic lives in app/.
"""
from __future__ import annotations

import atexit
import hashlib
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters
from telegram.error import Conflict

# Ensure project root in path for shared ops helpers
_project_root = Path(__file__).resolve().parents[1]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

load_dotenv()

from app.config import (
    LOG_LEVEL,
    BOT_TOKEN,
    USE_WEBHOOK,
    WEBHOOK_URL,
    WEBHOOK_PATH,
    RADAR_LOG_ALL_MESSAGES,
    TELEGRAM_MAX_CONCURRENT_UPDATES,
    SYSTEM_BOT_TOKEN,
    DEV_CHAT_ID,
)
from app.commands import cmd_start, cmd_ping, cmd_server_status
from app.callback import cb
from app.message import on_message
from app.keyboards import miniapp_home_url
from app.lifecycle import (
    on_shutdown,
    build_runtime_hooks,
    build_error_handlers,
    acquire_single_instance_lock,
    release_single_instance_lock,
    configure_runtime_alerts,
    maybe_send_update_ops_alert,
    notify_started,
    notify_main_crash,
)
from app.debug import _dbg, _dbg_lock

configure_runtime_alerts(
    system_bot_token=SYSTEM_BOT_TOKEN,
    bot_token=BOT_TOKEN,
    dev_chat_id=DEV_CHAT_ID,
)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    level=getattr(logging, LOG_LEVEL, logging.INFO),
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("hubbot")
raw_message_logger, global_error_handler = build_error_handlers(
    logger=log,
    dbg=_dbg,
    alert_sender=maybe_send_update_ops_alert,
)


def _build_application(*, post_init, post_stop) -> Application:
    builder = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(max(1, TELEGRAM_MAX_CONCURRENT_UPDATES))
        .post_init(post_init)
        .post_stop(post_stop)
        .post_shutdown(on_shutdown)
    )
    return builder.build()


def _register_handlers(app: Application) -> None:
    app.add_error_handler(global_error_handler)
    if RADAR_LOG_ALL_MESSAGES:
        app.add_handler(MessageHandler(filters.ALL, raw_message_logger), group=-1)
    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CommandHandler(["trangthai", "sys"], cmd_server_status))
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(cb))
    app.add_handler(MessageHandler(~filters.COMMAND, on_message))


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("Missing TELEGRAM_BOT_TOKEN in hubbot/.env")

    token_fingerprint = hashlib.sha256(BOT_TOKEN.encode("utf-8")).hexdigest()[:12]
    acquired, lock_path = acquire_single_instance_lock(token_fingerprint)

    _dbg(
        "hubbot single-instance lock status",
        {
            "pid": os.getpid(),
            "acquired": bool(acquired),
            "lock_path": lock_path,
            "token_fingerprint": token_fingerprint,
        },
        hypothesis_id="H1",
        run_id="post-fix",
    )

    if not acquired:
        raise SystemExit(
            "Another hubbot polling instance is already running for this token. "
            f"Lock file: {lock_path}"
        )

    lock_released = False

    def _release_lock_once() -> None:
        nonlocal lock_released
        if lock_released:
            return
        lock_released = True
        try:
            release_single_instance_lock()
        except Exception:
            pass

    atexit.register(_release_lock_once)

    _dbg(
        "hubbot main startup",
        {
            "pid": os.getpid(),
            "cwd": os.getcwd(),
            "use_webhook": bool(USE_WEBHOOK and WEBHOOK_URL),
            "webhook_url_configured": bool(WEBHOOK_URL),
            "webhook_path": WEBHOOK_PATH,
            "token_fingerprint": token_fingerprint,
        },
        hypothesis_id="H1_H2_H4_H5",
        run_id="post-fix",
    )

    post_init, post_stop = build_runtime_hooks(miniapp_home_url(), log)
    app = _build_application(post_init=post_init, post_stop=post_stop)
    _register_handlers(app)

    log.info("Starting Hubbot Polling...")
    _dbg(
        "hubbot selected polling mode",
        {"pid": os.getpid(), "drop_pending_updates": True},
        hypothesis_id="H1_H2_H4",
        run_id="post-fix",
    )

    try:
        notify_started()
        try:
            app.run_polling(
                close_loop=False,
                allowed_updates=[Update.MESSAGE, Update.EDITED_MESSAGE, Update.CALLBACK_QUERY],
                drop_pending_updates=True,
            )
        except Conflict:
            _dbg_lock(
                "telegram conflict caught in run_polling",
                {"pid": os.getpid()},
                hypothesis_id="H5",
                run_id="pre-fix",
            )
            log.error(
                "Bot Telegram dang chay o noi khac (vi du VPS Windows hoac server khac). "
                "Hay kiem tra va tat instance cu truoc khi khoi dong Hubbot moi."
            )
            raise SystemExit("Telegram Conflict: Token is already in use elsewhere.")
    except Exception as exc:
        notify_main_crash(exc)
        _dbg(
            "hubbot main run exception",
            {
                "pid": os.getpid(),
                "error_type": type(exc).__name__,
                "error_text": str(exc)[:220],
            },
            hypothesis_id="H1_H2_H3_H4_H5",
            run_id="post-fix",
        )
        raise
    except KeyboardInterrupt:
        log.info("[HUB] stopped by Ctrl+C")
    finally:
        _release_lock_once()


if __name__ == "__main__":
    main()
