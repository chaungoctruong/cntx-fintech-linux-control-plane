# -*- coding: utf-8 -*-
"""Telemetry Redis access, cache key, response formatting, and DB fallback for miniapp."""
from __future__ import annotations

import json
from typing import Any

try:
    from redis import asyncio as redis_asyncio
except Exception:
    redis_asyncio = None

from app.core.redis_client import get_redis
from app.services.mt5_service import make_store
from app.settings import settings

_LOCATION = "app.services.miniapp.miniapp_telemetry"


def telemetry_cache_key(profile_id: str) -> str:
    prefix = str(getattr(settings, "TELEMETRY_CACHE_PREFIX", "telemetry:profile:") or "telemetry:profile:")
    return f"{prefix}{str(profile_id or '').strip()}"


async def telemetry_redis() -> Any:
    if redis_asyncio is None:
        return None
    return await get_redis(decode_responses=True)


async def command_redis() -> Any:
    if redis_asyncio is None:
        raise RuntimeError("redis_asyncio_unavailable")
    redis = await get_redis(decode_responses=True)
    if redis is None:
        raise RuntimeError("Redis connection unavailable")
    return redis


def _normalize_status(value: Any) -> str:
    return str(value or "").strip().upper()


def format_telemetry_response(
    *,
    profile_id: str,
    source: str,
    payload: dict[str, Any],
    status: str = "",
) -> dict[str, Any]:
    source_norm = str(source or "").strip().lower()

    # Ưu tiên status do caller truyền vào.
    actual_status = _normalize_status(status)

    # Nếu caller chưa truyền, thử lấy từ payload để không đánh rơi trạng thái đã có sẵn.
    if not actual_status:
        actual_status = (
            _normalize_status(payload.get("status"))
            or _normalize_status(payload.get("current_status"))
        )

    # Chỉ fallback khi hoàn toàn chưa có trạng thái rõ ràng.
    # Tuyệt đối không suy STOPPED chỉ vì source không phải redis_hot_cache.
    if not actual_status:
        actual_status = "RUNNING" if source_norm == "redis_hot_cache" else "AWAITING_DATA"

    # Nếu là Redis hot cache mà caller/payload vẫn để trạng thái mơ hồ,
    # ưu tiên hiển thị RUNNING vì đây là tín hiệu sống mới nhất.
    if source_norm == "redis_hot_cache" and actual_status in {"UNKNOWN", "AWAITING_DATA", "IDLE"}:
        actual_status = "RUNNING"

    return {
        "ok": True,
        "profile_id": profile_id,
        "status": actual_status,
        "source": source,
        "telemetry": {
            "equity": None,
            "balance": None,
            "margin": None,
            "open_positions": None,
            "slot_id": payload.get("slot_id") if payload.get("slot_id") is not None else payload.get("slot"),
            "node_id": payload.get("node_id"),
            "ts": payload.get("ts"),
        },
    }


def db_fallback_telemetry(profile_id: str) -> dict[str, Any]:
    store = make_store()

    def _query(con: Any, cur: Any) -> dict[str, Any]:
        cur.execute(
            store._sql(
                """
                SELECT slot, updated_at, meta_json
                FROM slot_health
                WHERE profile_id=?
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ),
            (profile_id,),
        )
        row = cur.fetchone()
        if not row:
            return {}
        out = dict(row)
        meta = out.get("meta_json")
        if isinstance(meta, str):
            try:
                out["meta_json"] = json.loads(meta)
            except Exception:
                out["meta_json"] = {}
        return out

    return store._with_retry_read(_query)