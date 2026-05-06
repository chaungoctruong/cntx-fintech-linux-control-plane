"""Server-Sent Events (SSE) cho realtime account events.

Muc tieu: thay polling tu FE Mini App. Mot connection SSE thay cho ~30 lan poll/phut/user.

Pattern:
- Endpoint GET /api/v2/streams/account/{account_id}/events
- Auth: tma user_dep (FE phai dung fetch streaming + readable stream, hoac EventSource
  polyfill ho tro custom header). KHONG dung query-string token o ban dau
  vi tang attack surface.
- Backend doc Redis stream `mt5:execution:events` qua XREAD (no group),
  filter account_id, push ra client format SSE.
- Heartbeat comment ":hb\\n\\n" moi 15s de keep-alive proxy/CDN.
- Disconnect detection: starlette se cancel async generator khi client dong;
  ta bat asyncio.CancelledError + cleanup.

Limitation:
- Day la implementation initial (single instance). Voi multi-instance, cac event
  van di qua Redis stream nen moi instance van xem duoc. KHONG can pub/sub bridge
  (XREAD da blocking + multi-consumer safe).
- Khong giu offset cho client (each connect resume tu "$" = tail). FE muon replay
  kha nang catch missed events thi can endpoint khac (vd /events?since=...).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.v2.control_plane_deps import service_dep, translate_control_plane_error, user_dep
from app.core.redis_client import get_redis_read
from app.infra.redis_streams import EVENT_STREAM_KEY
from app.services.control_plane_service import MT5ControlPlaneService

log = logging.getLogger("api.v2.streams")

router = APIRouter(prefix="/streams", tags=["streams"])


# Channel events FE quan tam (filter ngay tai backend de tiet kiem network).
_INTERESTING_EVENT_TYPES = {
    "BOT_STARTED",
    "BOT_STOPPED",
    "ORDER_SENT",
    "ORDER_FILLED",
    "ORDER_REJECTED",
    "POSITION_UPDATED",
    "SLOT_DEGRADED",
    "SLOT_BROKEN",
    "SLOT_STATE_CHANGED",
    "COMMAND_REJECTED",
}

_HEARTBEAT_INTERVAL_SEC = 15
_REDIS_BLOCK_MS = 5000


def _format_sse(event_id: str, data: dict[str, Any], *, event_name: str | None = None) -> bytes:
    """Format 1 SSE message theo spec."""
    parts: list[str] = []
    if event_id:
        parts.append(f"id: {event_id}")
    if event_name:
        parts.append(f"event: {event_name}")
    parts.append("data: " + json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    parts.append("")
    parts.append("")
    return "\n".join(parts).encode("utf-8")


def _heartbeat_bytes() -> bytes:
    return b": heartbeat\n\n"


def _build_event_payload(stream_id: str, fields: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Convert Redis stream entry -> SSE payload (filtered, FE-friendly).

    Tra None neu event khong thuoc loai user quan tam (vd HEARTBEAT, RUNTIME_LOG)
    de tiet kiem bandwidth.
    """
    event_type = str(fields.get("event_type") or "").strip()
    if event_type not in _INTERESTING_EVENT_TYPES:
        return None
    payload_raw = str(fields.get("payload_json") or "{}")
    try:
        payload = json.loads(payload_raw)
    except Exception:
        payload = {"_raw": payload_raw}
    return {
        "stream_id": str(stream_id or ""),
        "event_id": str(fields.get("event_id") or "").strip(),
        "event_type": event_type,
        "deployment_id": fields.get("deployment_id") or None,
        "runner_id": fields.get("runner_id") or None,
        "slot_id": fields.get("slot_id") or None,
        "command_id": fields.get("command_id") or None,
        "severity": str(fields.get("severity") or "info"),
        "trace_id": fields.get("trace_id") or None,
        "payload": payload,
    }


async def _account_event_generator(
    *,
    request: Request,
    account_id: int,
) -> AsyncIterator[bytes]:
    last_id = "$"  # bat dau tu tail (chi event tuong lai)
    last_heartbeat = time.monotonic()
    yield _format_sse(
        event_id="",
        data={"type": "stream_open", "account_id": account_id, "ts": int(time.time())},
        event_name="open",
    )
    target_account = str(account_id)
    while True:
        if await request.is_disconnected():
            log.debug("SSE client disconnected account_id=%s", account_id)
            return
        try:
            redis = await get_redis_read(decode_responses=True)
        except Exception as exc:
            log.warning("SSE get_redis_read failed: %s", exc)
            await asyncio.sleep(2.0)
            continue
        if redis is None:
            await asyncio.sleep(2.0)
            continue
        try:
            response = await redis.xread({EVENT_STREAM_KEY: last_id}, count=20, block=_REDIS_BLOCK_MS)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("SSE xread failed: %s", exc)
            await asyncio.sleep(1.0)
            continue

        if not response:
            # Khong co message moi - heartbeat neu can
            if time.monotonic() - last_heartbeat >= _HEARTBEAT_INTERVAL_SEC:
                yield _heartbeat_bytes()
                last_heartbeat = time.monotonic()
            continue

        for _stream_key, messages in response:
            for msg_id, fields in messages:
                last_id = msg_id
                if str(fields.get("account_id") or "") != target_account:
                    continue
                payload = _build_event_payload(msg_id, fields)
                if payload is None:
                    continue
                yield _format_sse(
                    event_id=str(msg_id),
                    data=payload,
                    event_name=str(payload.get("event_type") or "message"),
                )
                last_heartbeat = time.monotonic()


@router.get("/account/{account_id}/events")
async def stream_account_events(
    account_id: int,
    request: Request,
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
):
    """SSE stream cac event lien quan toi 1 account cua user hien tai.

    Headers tra ve:
      - Content-Type: text/event-stream
      - Cache-Control: no-cache
      - X-Accel-Buffering: no  (cho nginx khong buffer)

    Format moi message:
      id: <redis stream id>
      event: <event_type>
      data: <json>
    """
    try:
        account = service.get_account(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
            account_id=int(account_id),
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc
    if not account:
        raise translate_control_plane_error(ValueError("account_not_found"))

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    generator = _account_event_generator(request=request, account_id=int(account_id))
    return StreamingResponse(generator, media_type="text/event-stream", headers=headers)
