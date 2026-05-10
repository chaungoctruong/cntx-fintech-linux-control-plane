#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import sys
from datetime import datetime, timezone


_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(os.path.dirname(_BACKEND_DIR))
os.chdir(_BACKEND_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ["CNTX_ROLE"] = "mt5-runner-stub"

from app.logging_config import configure_service_logging

configure_service_logging(
    "mt5-runner-stub",
    subdir="backend",
    level=os.getenv("LOG_LEVEL", "INFO"),
)
log = logging.getLogger("mt5_runner_stub")


def _int_env(*names: str, default: int) -> int:
    for name in names:
        raw = os.environ.get(name, "").strip()
        if not raw:
            continue
        try:
            return max(1, int(raw))
        except ValueError:
            log.warning("invalid integer env %s=%r; using default=%s", name, raw, default)
            break
    return default


def _slot_ids(max_slots: int) -> list[str]:
    prefix = os.environ.get("MT5_RUNNER_SLOT_PREFIX", "slot-").strip() or "slot-"
    return [f"{prefix}{idx:02d}" for idx in range(1, max(1, int(max_slots)) + 1)]


def _catalog_fields(catalog: dict) -> dict:
    bots = [item for item in catalog.get("bots", []) if isinstance(item, dict)]
    bot_ids = [
        str(item.get("bot_id") or item.get("bot_code") or "").strip()
        for item in bots
        if str(item.get("bot_id") or item.get("bot_code") or "").strip()
    ]
    return {
        "available_bots": bot_ids,
        "available_bot_names": list(bot_ids),
        "bot_catalog": catalog,
    }


def _slot_payloads(slot_ids: list[str]) -> list[dict]:
    return [
        {
            "slot_id": slot_id,
            "status": "ready",
            "allowed_profile_classes": ["light", "normal", "heavy"],
            "metadata": {
                "storage_slot_id": slot_id,
                "start_eligible": True,
                "ipc_ready": True,
                "phase": "windows_p1_catalog_handoff",
                "catalog_only": True,
            },
        }
        for slot_id in slot_ids
    ]


async def _heartbeat_loop(stop_event: asyncio.Event, client, runner_id: str, slot_id: str, catalog_provider) -> None:
    while not stop_event.is_set():
        try:
            catalog = catalog_provider.discover()
            fields = _catalog_fields(catalog)
            await client.heartbeat(
                {
                    "runner_id": runner_id,
                    "slot_id": slot_id,
                    "payload": {
                        "source": "mt5_runner_stub",
                        "phase": "windows_p1_catalog_handoff",
                        "catalog_only": True,
                        "ts": datetime.now(timezone.utc).isoformat(),
                        **fields,
                    },
                }
            )
        except Exception as exc:
            log.warning("heartbeat failed: %s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=15)
        except asyncio.TimeoutError:
            continue


async def _main() -> None:
    from app.main import _run_schema_migration_or_fail
    from app.runner import MT5RunnerControlPlaneClient, MT5RunnerRedisQueueConsumer
    from app.services.store_service import make_store
    from runner.bot_catalog import BotCatalogProvider

    _run_schema_migration_or_fail()
    make_store().init()

    runner_id = os.environ.get("RUNNER_ID") or os.environ.get("MT5_RUNNER_ID") or "runner-win-01"
    max_slots = _int_env("RUNNER_MAX_SLOTS", "MAX_SLOTS", default=10)
    slot_ids = _slot_ids(max_slots)
    slot_id = os.environ.get("MT5_RUNNER_SLOT_ID") or (slot_ids[0] if slot_ids else "slot-01")
    catalog_provider = BotCatalogProvider.from_env()
    catalog = catalog_provider.discover()
    catalog_fields = _catalog_fields(catalog)
    if catalog.get("errors"):
        log.warning("bot catalog discovery had errors: %s", catalog.get("errors"))
    log.info(
        "bot catalog discovered root=%s bots=%s",
        catalog.get("bot_trading_root"),
        catalog_fields["available_bots"],
    )
    client = MT5RunnerControlPlaneClient()
    consumer = MT5RunnerRedisQueueConsumer(runner_id=runner_id)

    await client.register_runner(
        {
            "runner_id": runner_id,
            "label": os.environ.get("RUNNER_LABEL", "Windows MT5 Runner 01"),
            "host": os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "windows-runner",
            "status": "online",
            "supported_profiles": ["light", "normal", "heavy"],
            "capability_tags": ["windows", "mt5", "http_poll", "redis_queue", "catalog_disk"],
            "capabilities": {
                "stub": True,
                "os": "windows",
                "transport": "http_poll",
                "supported_transports": ["http_poll", "redis_queue"],
                "mt5_ready": False,
                "runtime_login_required": True,
                "stop_policy": "end_task",
                "phase": "windows_p1_catalog_handoff",
                "catalog_only": True,
                "bot_trading_root": str(catalog_provider.bot_trading_root),
                "available_bots": catalog_fields["available_bots"],
            },
            **catalog_fields,
            "max_slots": max_slots,
            "slots": _slot_payloads(slot_ids),
        }
    )
    log.info("registered runner_id=%s slot_id=%s", runner_id, slot_id)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_stop() -> None:
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except (NotImplementedError, OSError):
            pass

    heartbeat_task = asyncio.create_task(
        _heartbeat_loop(stop_event, client, runner_id, slot_id, catalog_provider),
        name="mt5_stub_heartbeat",
    )

    try:
        while not stop_event.is_set():
            envelope = await consumer.pop_next(timeout_sec=5)
            if envelope is None:
                continue

            if envelope.queue_kind == "verification" and envelope.verification is not None:
                verification = envelope.verification
                bundle = await client.fetch_account_bundle(verification.account_id)
                log.info("verify job_id=%s account_id=%s login=%s server=%s", verification.job_id, verification.account_id, bundle.get("login"), bundle.get("server"))
                await client.submit_verification_result(
                    {
                        "job_id": verification.job_id,
                        "ok": True,
                        "runner_id": runner_id,
                        "slot_id": verification.slot_id,
                        "payload": {"source": "mt5_runner_stub", "verified_at": datetime.now(timezone.utc).isoformat()},
                    }
                )
                await consumer.ack(envelope)
                continue

            if envelope.queue_kind == "command" and envelope.command is not None:
                command = envelope.command
                try:
                    await client.update_command_delivery(
                        command.command_id,
                        {
                            "runner_id": runner_id,
                            "slot_id": command.slot_id,
                            "delivery_status": "dispatched",
                            "payload": {"source": "mt5_runner_stub", "queue": envelope.queue_name},
                        },
                    )
                    latest_command = await client.get_command(command.command_id)
                    package = await client.fetch_deployment_package(command.deployment_id)
                    log.info(
                        "command=%s type=%s deployment=%s login=%s bot=%s runtime_entry=%s",
                        command.command_id,
                        command.command_type,
                        command.deployment_id,
                        package.get("account", {}).get("login"),
                        package.get("bot", {}).get("bot_name"),
                        package.get("bot", {}).get("runtime_entry"),
                    )
                    await client.update_command_delivery(
                        command.command_id,
                        {
                            "runner_id": runner_id,
                            "slot_id": command.slot_id,
                            "delivery_status": "acknowledged",
                            "payload": {
                                "source": "mt5_runner_stub",
                                "queue": envelope.queue_name,
                                "observed_delivery_status": latest_command.get("delivery_status"),
                            },
                        },
                    )
                    await consumer.ack(envelope)
                    event_type = "BOT_STARTED" if command.command_type == "START_BOT" else "BOT_STOPPED"
                    await client.emit_event(
                        {
                            "event_type": event_type,
                            "account_id": command.account_id,
                            "deployment_id": command.deployment_id,
                            "bot_id": command.bot_id,
                            "runner_id": runner_id,
                            "slot_id": command.slot_id,
                            "severity": "info",
                            "payload": {
                                "source": "mt5_runner_stub",
                                "command_id": command.command_id,
                                "bot_name": package.get("bot", {}).get("bot_name"),
                                "runtime_entry": package.get("bot", {}).get("runtime_entry"),
                            },
                            "trace_id": command.trace_id,
                        }
                    )
                except Exception as exc:
                    log.exception("command failed command_id=%s", command.command_id)
                    with contextlib.suppress(Exception):
                        await client.update_command_delivery(
                            command.command_id,
                            {
                                "runner_id": runner_id,
                                "slot_id": command.slot_id,
                                "delivery_status": "failed",
                                "error_text": str(exc),
                                "payload": {"source": "mt5_runner_stub"},
                            },
                        )
                        await consumer.ack(envelope)
    finally:
        stop_event.set()
        heartbeat_task.cancel()
        with contextlib.suppress(Exception):
            await heartbeat_task


if __name__ == "__main__":
    asyncio.run(_main())
