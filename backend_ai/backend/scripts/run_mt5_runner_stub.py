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
os.chdir(_BACKEND_DIR)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ["CNTX_ROLE"] = "mt5-runner-stub"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
log = logging.getLogger("mt5_runner_stub")


async def _heartbeat_loop(stop_event: asyncio.Event, client, runner_id: str, slot_id: str) -> None:
    while not stop_event.is_set():
        try:
            await client.heartbeat(
                {
                    "runner_id": runner_id,
                    "slot_id": slot_id,
                    "payload": {
                        "source": "mt5_runner_stub",
                        "ts": datetime.now(timezone.utc).isoformat(),
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

    _run_schema_migration_or_fail()
    make_store().init()

    runner_id = os.environ.get("MT5_RUNNER_ID", "mt5-runner-stub")
    slot_id = os.environ.get("MT5_RUNNER_SLOT_ID", "slot-01")
    client = MT5RunnerControlPlaneClient()
    consumer = MT5RunnerRedisQueueConsumer(runner_id=runner_id)

    await client.register_runner(
        {
            "runner_id": runner_id,
            "label": "MT5 Runner Stub",
            "host": "stub",
            "status": "online",
            "supported_profiles": ["light", "normal", "heavy"],
            "capability_tags": ["isolated", "heavy", "indicator", "dca"],
            "capabilities": {"stub": True, "mt5_ready": False},
            "max_slots": 1,
            "slots": [
                {
                    "slot_id": slot_id,
                    "status": "ready",
                    "allowed_profile_classes": ["light", "normal", "heavy"],
                    "metadata": {"stub": True},
                }
            ],
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

    heartbeat_task = asyncio.create_task(_heartbeat_loop(stop_event, client, runner_id, slot_id), name="mt5_stub_heartbeat")

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
