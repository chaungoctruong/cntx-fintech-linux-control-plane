# -*- coding: utf-8 -*-
"""RabbitMQ consumer: process backend bot commands."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

import httpx

from app.config import (
    BACKEND_URL,
    BACKEND_API_KEY,
    API_TIMEOUT_SEC,
    API_MAX_KEEPALIVE,
    API_MAX_CONNECTIONS,
)

log = logging.getLogger("hubbot")

COMMANDS_QUEUE = "spider_commands"
_command_http_client: Optional[httpx.AsyncClient] = None


async def _get_command_http_client() -> httpx.AsyncClient:
    global _command_http_client
    if _command_http_client is None:
        headers = {"Accept": "application/json", "X-From-RabbitMQ-Consumer": "1"}
        if BACKEND_API_KEY:
            headers["X-API-Key"] = BACKEND_API_KEY
        _command_http_client = httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(API_TIMEOUT_SEC, connect=8.0),
            limits=httpx.Limits(
                max_keepalive_connections=max(10, API_MAX_KEEPALIVE),
                max_connections=max(50, API_MAX_CONNECTIONS),
            ),
        )
    return _command_http_client


async def close_rabbitmq_command_http_client() -> None:
    global _command_http_client
    if _command_http_client is None:
        return
    try:
        await _command_http_client.aclose()
    finally:
        _command_http_client = None


async def consume_rabbitmq_commands() -> None:
    """Connect to the legacy command queue and route messages to Backend."""
    try:
        from shared.rabbitmq_manager import get_rabbitmq_connection
    except ImportError:
        log.warning("RabbitMQ consumer skipped: shared.rabbitmq_manager not found")
        return
    while True:
        try:
            conn = await get_rabbitmq_connection()
            if conn is None:
                await asyncio.sleep(5.0)
                continue
            async with conn.channel() as channel:
                await channel.set_qos(prefetch_count=1)
                queue = await channel.declare_queue(COMMANDS_QUEUE, durable=True)
                async with queue.iterator() as queue_iter:
                    async for message in queue_iter:
                        try:
                            async with message.process():
                                body = message.body.decode("utf-8")
                                data = json.loads(body)
                                action = (data.get("action") or "").strip().lower()
                                payload = dict(data.get("payload") or data)
                                telegram_id = str(data.get("telegram_id") or payload.get("telegram_id") or "").strip()
                                profile_id = str(data.get("profile_id") or payload.get("profile_id") or "").strip()
                                if not telegram_id or not profile_id:
                                    log.warning("RabbitMQ command missing telegram_id or profile_id: %s", data)
                                    continue
                                payload["telegram_id"] = telegram_id
                                payload["profile_id"] = profile_id
                                url_start = f"{BACKEND_URL}/bot/start"
                                url_stop = f"{BACKEND_URL}/bot/stop"
                                client = await _get_command_http_client()
                                if action == "start":
                                    resp = await client.post(url_start, json=payload)
                                elif action == "stop":
                                    resp = await client.post(url_stop, json=payload)
                                else:
                                    log.warning("RabbitMQ unknown action: %s", action)
                                    continue
                                resp.raise_for_status()
                                log.info("RabbitMQ command processed: action=%s profile_id=%s", action, profile_id)
                        except Exception as e:
                            log.warning("RabbitMQ message processing failed: %s", str(e)[:200])
                            raise
        except asyncio.CancelledError:
            log.info("RabbitMQ consumer task cancelled")
            break
        except Exception as e:
            log.warning("RabbitMQ consumer connection error (will reconnect): %s", str(e)[:200])
            await asyncio.sleep(5.0)
