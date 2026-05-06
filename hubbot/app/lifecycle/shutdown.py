# -*- coding: utf-8 -*-
"""Shutdown: close API and backend clients."""
from __future__ import annotations

import logging
from telegram.ext import Application

log = logging.getLogger("hubbot")


async def on_shutdown(app: Application) -> None:
    """Close runtime clients on app shutdown."""
    from app.api import client as api_client_module
    from app.consumer import rabbitmq_commands as rabbitmq_commands_module
    try:
        if api_client_module._api_client is not None:
            await api_client_module._api_client.aclose()
    except Exception:
        pass
    finally:
        api_client_module._api_client = None
    try:
        await rabbitmq_commands_module.close_rabbitmq_command_http_client()
    except Exception:
        pass
