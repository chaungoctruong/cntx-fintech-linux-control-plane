"""Public status page endpoint cho landing/uptime monitor.

THIET KE TOI THIEU:
- Khong yeu cau auth (public).
- Khong leak runner_id, host, IP, queue depth, deployment count.
- Cache 30s tai-process de chong DDoS (1 process can dieu chinh, ngoai TTL).
- Tra ve gon: {status: ok|degraded|maintenance, message, since, version}.

Tach module rieng de KHONG anh huong public.py (bao gom logic ctrader landing).
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends

from app.api.v2.control_plane_deps import service_dep
from app.api.v2.system import _build_health_badge
from app.services.control_plane_service import MT5ControlPlaneService

router = APIRouter(prefix="/public", tags=["public-v2"])

_CACHE_TTL_SEC = 30
_CACHE: dict[str, Any] = {"ts": 0.0, "value": None}


def _public_payload(level: str, message_vi: str, message_en: str) -> dict[str, Any]:
    return {
        "status": level,  # ok | degraded | maintenance
        "message_vi": message_vi,
        "message_en": message_en,
        "since": int(time.time()),
        "service": "cntx-labs-saas",
    }


def _from_badge(badge: dict[str, Any]) -> dict[str, Any]:
    """Strip details, KHONG expose runner_id/queue_depth khi public.

    Map mau public-friendly:
      - level "ok"           -> "ok" + "Hệ thống ổn định."
      - level "degraded"     -> "degraded" + message_vi/en cua badge (vd. backlog)
      - level "maintenance"  -> "maintenance" + message_vi/en
    """
    level = str(badge.get("level") or "ok")
    message_vi = str(badge.get("message_vi") or "Hệ thống ổn định.")
    message_en = str(badge.get("message_en") or "All systems normal.")
    return _public_payload(level=level, message_vi=message_vi, message_en=message_en)


@router.get("/status")
async def public_status(
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    """Public status endpoint cho landing/uptime monitor (vd: UptimeRobot, BetterStack).

    Cache process-local 30s. Khong require auth. Khong leak internal detail.
    """
    now = time.time()
    if _CACHE.get("value") is not None and (now - float(_CACHE.get("ts") or 0.0)) < _CACHE_TTL_SEC:
        return dict(_CACHE["value"])
    try:
        dashboard = service.runner_health_dashboard()
        badge = _build_health_badge(dashboard)
        payload = _from_badge(badge)
    except Exception:
        payload = _public_payload(
            level="maintenance",
            message_vi="Hệ thống đang khôi phục. Vui lòng quay lại sau.",
            message_en="System recovering. Please check back shortly.",
        )
    _CACHE["ts"] = now
    _CACHE["value"] = payload
    return payload
