# -*- coding: utf-8 -*-
"""Runtime hooks for Telegram Application startup/shutdown."""
from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from collections.abc import Callable, Awaitable

from telegram import MenuButtonWebApp, WebAppInfo
from telegram.ext import Application

from app.consumer import consume_rabbitmq_commands


def build_runtime_hooks(miniapp_url: str, logger: logging.Logger) -> tuple[
    Callable[[Application], Awaitable[None]],
    Callable[[Application], Awaitable[None]],
]:
    async def _run_rabbitmq_consumer() -> None:
        """Wrap consumer so crashes are logged without changing main polling flow."""
        try:
            await consume_rabbitmq_commands()
        except asyncio.CancelledError:
            logger.info("RabbitMQ consumer task cancelled.")
            raise
        except Exception:
            logger.exception("RabbitMQ consumer task crashed.")
            raise

    async def post_init(app: Application) -> None:
        """Create background consumer after initialize(); manage it manually on shutdown."""
        try:
            await app.bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="Mở Mini App",
                    web_app=WebAppInfo(url=miniapp_url),
                )
            )
            logger.info("Telegram menu button configured for Mini App home.")
        except Exception:
            logger.exception("Failed to configure Telegram menu button.")

        consumer_task = asyncio.create_task(
            _run_rabbitmq_consumer(),
            name="hubbot-rabbitmq-consumer",
        )
        setattr(app, "_rabbitmq_consumer_task", consumer_task)

    async def post_stop(app: Application) -> None:
        """Cancel manually-created background tasks before final shutdown."""
        consumer_task = getattr(app, "_rabbitmq_consumer_task", None)
        if consumer_task is None:
            return

        try:
            delattr(app, "_rabbitmq_consumer_task")
        except Exception:
            pass

        consumer_task.cancel()
        with suppress(asyncio.CancelledError):
            await consumer_task

    return post_init, post_stop
