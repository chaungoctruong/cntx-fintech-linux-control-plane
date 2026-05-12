"""One-off: dispatch a tiny PLACE_ORDER on deployment 190 (account 17, XAUUSDm, buy, 0.01)."""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.schemas.control_plane import CommandType  # noqa: E402
from app.services.control_plane_service import get_control_plane_service  # noqa: E402


async def main() -> int:
    service = get_control_plane_service()
    deployment_id = 190
    symbol = "XAUUSDm"
    side = "buy"
    volume = 0.01

    trace_id = f"linux-smoke-place-order:{int(time.time())}"

    payload = {
        "request": {
            "symbol": symbol,
            "side": side,
            "volume": volume,
        },
        "source": "linux_backend_smoke",
        "note": "manual end-to-end PLACE_ORDER smoke test (deployment 190 / slot-03)",
    }

    print(f"[dispatch] deployment={deployment_id} symbol={symbol} side={side} volume={volume} trace_id={trace_id}", flush=True)
    result = await service.send_deployment_command(
        telegram_id="5573261363",
        username=None,
        deployment_id=deployment_id,
        command_type=CommandType.PLACE_ORDER,
        payload=payload,
        priority=50,
        trace_id=trace_id,
        command_id=None,
    )
    print("[result]", json.dumps(result, default=str, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
