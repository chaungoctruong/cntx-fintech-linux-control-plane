# -*- coding: utf-8 -*-
"""Small, shared Telegram ops alerts with Vietnamese, low-noise messages.

This module is intentionally standalone so backend and hubbot can import it
without coupling their app packages together.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import os
import re
import threading
import time
import traceback
import urllib.error
import urllib.request
from typing import Any, Optional

_DEFAULT_COOLDOWN_SEC = 300
_MAX_BODY_LEN = 1800
_TELEGRAM_TEXT_LIMIT = 3900
_STATE_LOCK = threading.Lock()
_COOLDOWNS: dict[str, float] = {}

_TOKEN = ""
_CHAT_ID = ""
_SERVICE_NAME = "CNTx labs"
_ENABLED: Optional[bool] = None

_SENSITIVE_KEY_RE = re.compile(
    r"(password|passwd|pwd|token|secret|api[_-]?key|authorization|credential|redis[_-]?url|database[_-]?url|dsn)",
    re.IGNORECASE,
)
_URL_SECRET_RE = re.compile(
    r"\b(redis|postgresql|postgres)://([^:\s/@]+):([^@\s]+)@",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE)
_TOKEN_LIKE_RE = re.compile(r"\b\d{7,}:[A-Za-z0-9_-]{20,}\b")


def configure_telegram_alerts(
    *,
    token: str | None = None,
    chat_id: str | None = None,
    service_name: str | None = None,
    enabled: bool | None = None,
) -> None:
    """Configure the shared alert sender from each process' own settings."""
    global _TOKEN, _CHAT_ID, _SERVICE_NAME, _ENABLED
    if token is not None:
        _TOKEN = str(token or "").strip()
    if chat_id is not None:
        _CHAT_ID = str(chat_id or "").strip()
    if service_name is not None and str(service_name or "").strip():
        _SERVICE_NAME = str(service_name or "").strip()
    if enabled is not None:
        _ENABLED = bool(enabled)


def _env_token() -> str:
    return (
        _TOKEN
        or os.getenv("SYSTEM_BOT_TOKEN", "")
        or os.getenv("TELEGRAM_BOT_TOKEN", "")
        or ""
    ).strip()


def _env_chat_id() -> str:
    return (_CHAT_ID or os.getenv("OPS_TELEGRAM_CHAT_ID", "") or os.getenv("DEV_CHAT_ID", "") or "").strip()


def _enabled() -> bool:
    if _ENABLED is not None:
        return _ENABLED
    raw = str(os.getenv("OPS_TELEGRAM_ALERTS_ENABLED", "1") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off", "disabled"}


def _cleanup_cooldowns(now: float) -> None:
    expired = [key for key, exp in _COOLDOWNS.items() if exp <= now]
    for key in expired[:512]:
        _COOLDOWNS.pop(key, None)


def _allow(alert_key: str, cooldown_sec: int | float) -> bool:
    cooldown = max(0.0, float(cooldown_sec or 0))
    if cooldown <= 0:
        return True
    now = time.time()
    with _STATE_LOCK:
        _cleanup_cooldowns(now)
        current = float(_COOLDOWNS.get(alert_key) or 0.0)
        if current > now:
            return False
        _COOLDOWNS[alert_key] = now + cooldown
        return True


def redact_text(value: Any, *, limit: int = _MAX_BODY_LEN) -> str:
    text = str(value or "")
    text = _URL_SECRET_RE.sub(lambda m: f"{m.group(1)}://***:***@", text)
    text = _BEARER_RE.sub("Bearer ***", text)
    text = _TOKEN_LIKE_RE.sub("***:***", text)
    text = re.sub(
        r"(?i)(password|passwd|pwd|token|secret|api[_-]?key|authorization|credential)\s*[:=]\s*([^\s,;]+)",
        r"\1=***",
        text,
    )
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text


def redact_payload(value: Any, *, limit: int = _MAX_BODY_LEN) -> str:
    def _scrub(item: Any) -> Any:
        if isinstance(item, dict):
            out: dict[str, Any] = {}
            for key, val in item.items():
                key_s = str(key or "")
                if _SENSITIVE_KEY_RE.search(key_s):
                    out[key_s] = "***"
                else:
                    out[key_s] = _scrub(val)
            return out
        if isinstance(item, list):
            return [_scrub(v) for v in item[:10]]
        if isinstance(item, str):
            return redact_text(item, limit=600)
        return item

    try:
        rendered = json.dumps(_scrub(value), ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        rendered = str(value or "")
    return redact_text(rendered, limit=limit)


def _severity_label(severity: str) -> str:
    value = str(severity or "").strip().lower()
    if value in {"critical", "crash", "fatal", "error"}:
        return "Cần kiểm tra ngay"
    if value in {"warning", "warn"}:
        return "Cần theo dõi"
    return "Thông tin"


def _friendly_summary(
    *,
    area: str,
    summary: str,
    exc: BaseException | None,
    status_code: int | None,
) -> str:
    if summary:
        return summary.strip()
    if status_code in {502, 503, 504}:
        return "Backend đang phản hồi không ổn định."
    if status_code and status_code >= 500:
        return "Backend đang gặp lỗi xử lý."
    if exc is not None:
        name = exc.__class__.__name__
        text = str(exc or "").lower()
        if "timeout" in name.lower() or "timeout" in text:
            return "Kết nối bị chậm hoặc hết thời gian chờ."
        if "connect" in name.lower() or "network" in text:
            return "Không kết nối được tới một dịch vụ cần thiết."
        if name == "Conflict":
            return "Bot Telegram có thể đang chạy ở nơi khác."
        return "Hệ thống gặp lỗi cần kiểm tra."
    area_s = str(area or "").strip()
    return f"{area_s} cần kiểm tra." if area_s else "Hệ thống cần kiểm tra."


def _build_message(
    *,
    service: str,
    area: str,
    summary: str,
    severity: str,
    exc: BaseException | None = None,
    status_code: int | None = None,
    path: str | None = None,
    user_id: str | int | None = None,
    deployment_id: str | int | None = None,
    account_id: str | int | None = None,
    runner_id: str | None = None,
    slot_id: str | None = None,
    impact: str | None = None,
    action: str | None = None,
    detail: Any = None,
    include_trace: bool = False,
    alert_key: str | None = None,
) -> str:
    lines = [
        f"{service}: {_severity_label(severity)}",
        f"Khu vực: {area or 'Hệ thống'}",
        f"Lỗi: {_friendly_summary(area=area, summary=summary, exc=exc, status_code=status_code)}",
    ]
    if impact:
        lines.append(f"Ảnh hưởng: {str(impact).strip()}")
    if path:
        lines.append(f"Đường dẫn: {str(path).strip()}")
    if status_code:
        lines.append(f"Mã phản hồi: {int(status_code)}")
    ids: list[str] = []
    if user_id not in (None, ""):
        ids.append(f"user={user_id}")
    if account_id not in (None, ""):
        ids.append(f"account={account_id}")
    if deployment_id not in (None, ""):
        ids.append(f"deployment={deployment_id}")
    if runner_id:
        ids.append(f"runner={runner_id}")
    if slot_id:
        ids.append(f"slot={slot_id}")
    if ids:
        lines.append("Liên quan: " + ", ".join(str(x) for x in ids))
    if action:
        lines.append(f"Nên làm: {str(action).strip()}")
    if exc is not None:
        lines.append(f"Loại lỗi: {exc.__class__.__name__}")
    if detail not in (None, ""):
        lines.append("")
        lines.append("Chi tiết ngắn:")
        lines.append(redact_payload(detail) if isinstance(detail, (dict, list)) else redact_text(detail))
    if include_trace and exc is not None:
        trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        if trace:
            lines.append("")
            lines.append("Mã tra cứu:")
            lines.append(redact_text(trace, limit=900))
    if alert_key:
        lines.append("")
        lines.append(f"Mã cảnh báo: {redact_text(alert_key, limit=180)}")
    text = "\n".join(line for line in lines if line is not None).strip()
    if len(text) > _TELEGRAM_TEXT_LIMIT:
        return text[: _TELEGRAM_TEXT_LIMIT - 1].rstrip() + "…"
    return text


def _alert_key(
    *,
    service: str,
    area: str,
    summary: str,
    exc: BaseException | None,
    status_code: int | None,
    path: str | None,
    explicit: str | None,
) -> str:
    if explicit:
        return str(explicit).strip()
    raw = "|".join(
        [
            service,
            area,
            summary,
            str(status_code or ""),
            path or "",
            exc.__class__.__name__ if exc else "",
            str(exc or "")[:160] if exc else "",
        ]
    )
    return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()


def _send_text(text: str) -> bool:
    if not _enabled():
        return False
    token = _env_token()
    chat_id = _env_chat_id()
    if not token or not chat_id or not text.strip():
        return False

    payload = json.dumps(
        {
            "chat_id": chat_id,
            "text": html.escape(text, quote=False),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            return 200 <= int(response.status or 0) < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def notify_error_sync(
    *,
    area: str,
    summary: str = "",
    service: str | None = None,
    severity: str = "critical",
    exc: BaseException | None = None,
    status_code: int | None = None,
    path: str | None = None,
    user_id: str | int | None = None,
    deployment_id: str | int | None = None,
    account_id: str | int | None = None,
    runner_id: str | None = None,
    slot_id: str | None = None,
    impact: str | None = None,
    action: str | None = None,
    detail: Any = None,
    alert_key: str | None = None,
    cooldown_sec: int | float = _DEFAULT_COOLDOWN_SEC,
    include_trace: bool = False,
) -> bool:
    service_name = str(service or _SERVICE_NAME or "CNTx labs").strip()
    final_key = _alert_key(
        service=service_name,
        area=area,
        summary=summary,
        exc=exc,
        status_code=status_code,
        path=path,
        explicit=alert_key,
    )
    if not _allow(final_key, cooldown_sec):
        return False
    text = _build_message(
        service=service_name,
        area=area,
        summary=summary,
        severity=severity,
        exc=exc,
        status_code=status_code,
        path=path,
        user_id=user_id,
        deployment_id=deployment_id,
        account_id=account_id,
        runner_id=runner_id,
        slot_id=slot_id,
        impact=impact,
        action=action,
        detail=detail,
        include_trace=include_trace,
        alert_key=final_key,
    )
    return _send_text(text)


async def notify_error_async(**kwargs: Any) -> bool:
    return await asyncio.to_thread(notify_error_sync, **kwargs)


def schedule_error_alert(**kwargs: Any) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        notify_error_sync(**kwargs)
        return
    task = loop.create_task(notify_error_async(**kwargs), name="ops-telegram-alert")

    def _consume_result(done: asyncio.Task) -> None:
        try:
            done.result()
        except Exception:
            pass

    task.add_done_callback(_consume_result)


def notify_event_sync(
    *,
    area: str,
    summary: str,
    service: str | None = None,
    severity: str = "info",
    detail: Any = None,
    alert_key: str | None = None,
    cooldown_sec: int | float = _DEFAULT_COOLDOWN_SEC,
) -> bool:
    return notify_error_sync(
        area=area,
        summary=summary,
        service=service,
        severity=severity,
        detail=detail,
        alert_key=alert_key,
        cooldown_sec=cooldown_sec,
    )


async def notify_event_async(**kwargs: Any) -> bool:
    return await asyncio.to_thread(notify_event_sync, **kwargs)
