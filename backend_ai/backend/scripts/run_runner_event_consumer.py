#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys


_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_BACKEND_DIR)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ["CNTX_ROLE"] = "runner-event-consumer"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
log = logging.getLogger("runner_event_consumer_script")


async def _main() -> None:
    from app.events.runner_event_consumer import RunnerEventConsumerService
    from app.main import _run_schema_migration_or_fail
    from app.services.store_service import make_store
    from app.settings import settings

    _run_schema_migration_or_fail()
    make_store().init()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_stop() -> None:
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except (NotImplementedError, OSError):
            pass

    consumer = RunnerEventConsumerService(
        group_name=str(getattr(settings, "CONTROL_PLANE_EVENT_CONSUMER_GROUP", "control-plane-event-audit") or "control-plane-event-audit"),
        block_ms=int(getattr(settings, "CONTROL_PLANE_EVENT_CONSUMER_BLOCK_MS", 5000) or 5000),
    )
    log.info("runner event consumer started group=%s", getattr(settings, "CONTROL_PLANE_EVENT_CONSUMER_GROUP", "control-plane-event-audit"))
    await consumer.run_forever(stop_event)


if __name__ == "__main__":
    asyncio.run(_main())
