# -*- coding: utf-8 -*-
"""Backend API client, cache, and callback dedup."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Dict, Optional, Tuple

import httpx

from app.config import (
    BACKEND_API_KEY,
    BACKEND_URL,
    API_TIMEOUT_SEC,
    API_RETRIES,
    API_MAX_KEEPALIVE,
    API_MAX_CONNECTIONS,
    API_MAX_CONCURRENCY,
    API_CACHE_TTL_SEC,
    CALLBACK_DEDUP_TTL_SEC,
)
from app.error_log import log_agent_event, log_agent_failure
from ops_telegram_alerts import notify_error_sync

log = logging.getLogger("hubbot.api_client")

_api_client: Optional[httpx.AsyncClient] = None
_api_semaphore = asyncio.Semaphore(max(10, API_MAX_CONCURRENCY))
_api_cache: Dict[str, Tuple[float, dict]] = {}
_api_singleflight: Dict[str, asyncio.Future] = {}
_callback_dedup: Dict[str, float] = {}
_message_dedup: Dict[str, float] = {}


def _maybe_send_backend_ops_alert(
    *,
    alert_key: str,
    summary: str,
    detail: str,
    cooldown_sec: float = 300.0,
) -> None:
    notify_error_sync(
        area="Mini App kết nối Backend",
        summary=summary,
        severity="critical",
        impact="Mini App hoặc chat Telegram có thể phản hồi chậm/lỗi.",
        action="Kiểm tra backend /ready, PM2 và nginx nếu lỗi lặp lại.",
        detail=detail,
        alert_key=alert_key,
        cooldown_sec=cooldown_sec,
    )


def _cacheable_path(path: str) -> bool:
    return path in ("/bot/list", "/bot/status", "/bot/refresh")


def _cache_key(method: str, path: str, json_body: dict | None) -> str:
    try:
        body = json.dumps(json_body or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        body = "{}"
    return f"{method.upper()}|{path}|{body}"


def _cache_get(key: str) -> Optional[dict]:
    now = time.time()
    item = _api_cache.get(key)
    if not item:
        return None
    expires_at, data = item
    if expires_at <= now:
        _api_cache.pop(key, None)
        return None
    return dict(data) if isinstance(data, dict) else None


def _cache_set(key: str, value: dict, ttl_sec: float) -> None:
    if ttl_sec <= 0:
        return
    _api_cache[key] = (time.time() + ttl_sec, dict(value))


def callback_is_duplicate(uid: int, data: str) -> bool:
    key = f"{uid}:{data}"
    now = time.time()
    expired = [k for k, ts in _callback_dedup.items() if ts <= now]
    for k in expired[:256]:
        _callback_dedup.pop(k, None)
    expires = _callback_dedup.get(key)
    if expires and expires > now:
        return True
    _callback_dedup[key] = now + max(0.2, CALLBACK_DEDUP_TTL_SEC)
    return False


def message_is_duplicate(uid: int, message_id: int | None, text: str) -> bool:
    text_key = str(text or "").strip()
    if not uid or (message_id is None and not text_key):
        return False
    key = f"{uid}:{message_id}" if message_id is not None else f"{uid}:text:{text_key}"
    now = time.time()
    expired = [k for k, ts in _message_dedup.items() if ts <= now]
    for k in expired[:256]:
        _message_dedup.pop(k, None)
    expires = _message_dedup.get(key)
    if expires and expires > now:
        return True
    _message_dedup[key] = now + max(0.5, CALLBACK_DEDUP_TTL_SEC)
    return False


def _api_headers() -> Dict[str, str]:
    headers: Dict[str, str] = {"Accept": "application/json"}
    if BACKEND_API_KEY:
        headers["X-API-Key"] = BACKEND_API_KEY
    return headers


async def get_api_client() -> httpx.AsyncClient:
    global _api_client
    if _api_client is None:
        limits = httpx.Limits(
            max_keepalive_connections=max(10, API_MAX_KEEPALIVE),
            max_connections=max(50, API_MAX_CONNECTIONS),
        )
        _api_client = httpx.AsyncClient(
            timeout=httpx.Timeout(API_TIMEOUT_SEC, connect=8.0),
            limits=limits,
            headers=_api_headers(),
        )
    return _api_client


async def api_json(method: str, path: str, *, json_body: dict | None = None) -> dict:
    url = f"{BACKEND_URL}{path}"
    key = _cache_key(method, path, json_body)
    use_cache = _cacheable_path(path)
    if use_cache:
        cached = _cache_get(key)
        if isinstance(cached, dict):
            return cached
        inflight = _api_singleflight.get(key)
        if inflight is not None and not inflight.done():
            try:
                return await asyncio.wait_for(asyncio.shield(inflight), timeout=max(0.2, API_TIMEOUT_SEC))
            except Exception:
                pass
    client = await get_api_client()
    retries = max(0, min(API_RETRIES, 5))
    attempts = 1 + retries
    last_err: dict = {"ok": False, "error": "unknown_error", "detail": ""}
    loop = asyncio.get_running_loop()
    fut: Optional[asyncio.Future] = None
    if use_cache:
        fut = loop.create_future()
        _api_singleflight[key] = fut

    started_at = time.monotonic()
    try:
        for attempt in range(1, attempts + 1):
            try:
                async with _api_semaphore:
                    r = await client.request(method, url, json=json_body)
                if r.status_code in (502, 503, 504) and attempt < attempts:
                    await asyncio.sleep(0.2 * attempt + (0.05 * (attempt % 3)))
                    continue
                r.raise_for_status()
                try:
                    js = r.json()
                except Exception:
                    text = (r.text or "")[:600]
                    log_agent_failure(
                        log,
                        "hubbot.backend.bad_json",
                        error=Exception("non_json_body"),
                        error_code="backend_bad_json",
                        operation="hubbot_backend_call",
                        hint=(
                            "Backend returned a non-JSON body where JSON was expected. Often happens "
                            "if reverse proxy returns an HTML error page or backend crashed mid-response. "
                            "Inspect the body snippet and check backend logs for the same path/timestamp."
                        ),
                        http_method=method,
                        http_path=path,
                        http_status=r.status_code,
                        body_preview=text[:220],
                        elapsed_ms=round((time.monotonic() - started_at) * 1000, 1),
                    )
                    _maybe_send_backend_ops_alert(
                        alert_key=f"hubbot_backend_bad_json:{path}",
                        summary=f"Backend returned non-JSON for {path}",
                        detail=f"status={r.status_code}\nbody={text}",
                        cooldown_sec=300.0,
                    )
                    result = {"ok": False, "error": "bad_json", "detail": text}
                    if fut is not None and not fut.done():
                        fut.set_result(result)
                    return result
                result = js if isinstance(js, dict) else {"ok": True, "data": js}
                if use_cache and isinstance(result, dict):
                    _cache_set(key, result, API_CACHE_TTL_SEC)
                if fut is not None and not fut.done():
                    fut.set_result(result if isinstance(result, dict) else {"ok": True, "data": result})
                # Light-touch trace at debug; the per-update logger already covers correlation.
                log.debug(
                    "hubbot.backend.ok %s %s -> %s",
                    method, path, r.status_code,
                    extra={
                        "event": "hubbot.backend.ok",
                        "operation": "hubbot_backend_call",
                        "outcome": "ok",
                        "http_method": method,
                        "http_path": path,
                        "http_status": r.status_code,
                        "elapsed_ms": round((time.monotonic() - started_at) * 1000, 1),
                    },
                )
                return result
            except httpx.HTTPStatusError as e:
                text = ""
                try:
                    text = (e.response.text or "")[:600]
                except Exception:
                    pass
                status_code = int(getattr(e.response, "status_code", 0) or 0)
                if status_code >= 500:
                    log_agent_failure(
                        log,
                        "hubbot.backend.5xx",
                        error=e,
                        error_code=f"backend_http_{status_code}",
                        operation="hubbot_backend_call",
                        hint=(
                            "Backend returned 5xx to hubbot. Check `logs/backend/api-instance-*.jsonl` "
                            "for the same `http_path` near this timestamp; the error stack should be "
                            "in the matching `request.exception` line."
                        ),
                        http_method=method,
                        http_path=path,
                        http_status=status_code,
                        body_preview=text[:220],
                        elapsed_ms=round((time.monotonic() - started_at) * 1000, 1),
                    )
                    _maybe_send_backend_ops_alert(
                        alert_key=f"hubbot_backend_http5xx:{path}",
                        summary=f"Backend {status_code} on {path}",
                        detail=text or "(empty response body)",
                        cooldown_sec=300.0,
                    )
                else:
                    log_agent_event(
                        log,
                        logging.WARNING,
                        "hubbot.backend.4xx",
                        hint=(
                            "Backend rejected the call (4xx). Either hubbot sent a malformed body or "
                            "backend tightened validation. Inspect the response body and the path."
                        ),
                        operation="hubbot_backend_call",
                        outcome="client_error",
                        http_method=method,
                        http_path=path,
                        http_status=status_code,
                        body_preview=text[:220],
                    )
                result = {"ok": False, "error": f"HTTP {e.response.status_code}", "detail": text}
                if fut is not None and not fut.done():
                    fut.set_result(result)
                return result
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
                last_err = {"ok": False, "error": "network_error", "detail": str(e)[:200]}
                log_agent_failure(
                    log,
                    "hubbot.backend.network_error",
                    error=e,
                    error_code="backend_unreachable",
                    operation="hubbot_backend_call",
                    hint=(
                        "Hubbot could not reach backend (connect/read timeout / protocol error). "
                        "Check that `BACKEND_URL` resolves, port 8001 is open, and backend is up "
                        "(`docker compose ps`, `curl /ready`)."
                    ),
                    http_method=method,
                    http_path=path,
                    attempt=attempt,
                    elapsed_ms=round((time.monotonic() - started_at) * 1000, 1),
                )
                _maybe_send_backend_ops_alert(
                    alert_key="hubbot_backend_network_error",
                    summary=f"Hubbot could not reach backend for {path}",
                    detail=str(e),
                    cooldown_sec=300.0,
                )
                if attempt < attempts:
                    await asyncio.sleep(0.15 * attempt)
                    continue
                if fut is not None and not fut.done():
                    fut.set_result(last_err)
                return last_err
            except Exception as e:
                log_agent_failure(
                    log,
                    "hubbot.backend.unexpected_exception",
                    error=e,
                    error_code="backend_client_exception",
                    operation="hubbot_backend_call",
                    hint=(
                        "Unexpected exception in hubbot's backend client. Read the stack trace below; "
                        "if it's an httpx variant we don't handle, add it to the explicit branches."
                    ),
                    http_method=method,
                    http_path=path,
                    elapsed_ms=round((time.monotonic() - started_at) * 1000, 1),
                )
                _maybe_send_backend_ops_alert(
                    alert_key="hubbot_backend_client_exception",
                    summary=f"Unexpected backend client exception on {path}",
                    detail=str(e),
                    cooldown_sec=300.0,
                )
                result = {"ok": False, "error": "exception", "detail": str(e)[:200]}
                if fut is not None and not fut.done():
                    fut.set_result(result)
                return result
        if fut is not None and not fut.done():
            fut.set_result(last_err)
        return last_err
    finally:
        if use_cache:
            _api_singleflight.pop(key, None)
