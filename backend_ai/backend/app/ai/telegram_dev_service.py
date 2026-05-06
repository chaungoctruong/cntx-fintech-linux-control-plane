import asyncio
import hashlib
import html
import httpx
import logging
import re
import time
from typing import Optional

from ..settings import settings

log = logging.getLogger("dev_service")

_client: Optional[httpx.AsyncClient] = None
_LOCAL_ALERT_MARKERS: dict[str, float] = {}

TELEGRAM_MAX_LEN = 4096
SAFE_CHUNK_LEN = 3500


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=15.0,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
        )
    return _client


def _now() -> float:
    return time.time()


def _normalize_text(text: str) -> str:
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _escape_html_if_needed(text: str, allow_html: bool) -> str:
    if allow_html:
        return text
    return html.escape(text, quote=False)


def _fingerprint(alert_key: str, message: str) -> str:
    raw = f"{alert_key}|{message}".encode("utf-8", errors="ignore")
    return hashlib.sha1(raw).hexdigest()


def _cleanup_local_markers() -> None:
    now = _now()
    expired = [k for k, exp in _LOCAL_ALERT_MARKERS.items() if exp <= now]
    for k in expired:
        _LOCAL_ALERT_MARKERS.pop(k, None)


def _allow_local_alert(alert_key: str, cooldown_sec: int) -> bool:
    if not alert_key or cooldown_sec <= 0:
        return True

    _cleanup_local_markers()
    now = _now()
    key = f"dev_alert:{alert_key}"
    exp = float(_LOCAL_ALERT_MARKERS.get(key) or 0.0)
    if exp > now:
        return False

    _LOCAL_ALERT_MARKERS[key] = now + float(cooldown_sec)
    return True


def _chunk_message(text: str, max_len: int = SAFE_CHUNK_LEN) -> list[str]:
    text = _normalize_text(text)
    if not text:
        return []

    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break

        split_at = remaining.rfind("\n", 0, max_len)
        if split_at < max_len * 0.5:
            split_at = remaining.rfind(" ", 0, max_len)
        if split_at < max_len * 0.5:
            split_at = max_len

        chunk = remaining[:split_at].rstrip()
        remaining = remaining[split_at:].lstrip()

        if chunk:
            chunks.append(chunk)

    return chunks


async def _post_telegram(payload: dict, retries: int = 2) -> bool:
    client = get_client()
    token = str(getattr(settings, "SYSTEM_BOT_TOKEN", "") or getattr(settings, "TELEGRAM_BOT_TOKEN", "") or "").strip()
    if not token:
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    for attempt in range(retries + 1):
        try:
            response = await client.post(url, json=payload)

            if response.status_code == 429:
                retry_after = 2
                try:
                    body = response.json()
                    retry_after = int((body.get("parameters") or {}).get("retry_after") or 2)
                except Exception:
                    pass

                if attempt < retries:
                    await asyncio.sleep(max(1, retry_after))
                    continue

            response.raise_for_status()
            return True

        except httpx.HTTPStatusError as e:
            body = e.response.text[:500] if e.response is not None else ""
            log.error(
                "Telegram API từ chối tin nhắn (status=%s): %s",
                getattr(e.response, "status_code", "unknown"),
                body,
            )
            return False
        except Exception as e:
            log.error("Lỗi mạng khi gửi Telegram alert (attempt=%s): %s", attempt + 1, e)
            if attempt < retries:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            return False

    return False


async def send_dev_alert(
    message: str,
    *,
    title: Optional[str] = None,
    alert_key: Optional[str] = None,
    cooldown_sec: int = 0,
    allow_html: bool = False,
    disable_web_page_preview: bool = True,
) -> bool:
    """
    Gửi cảnh báo tới Telegram dev chat.

    Args:
        message: nội dung cảnh báo
        title: tiêu đề ngắn, ví dụ "AI ROUTER ERROR"
        alert_key: khóa chống spam. Ví dụ "ai_router_timeout"
        cooldown_sec: nếu > 0 thì cùng alert_key chỉ gửi 1 lần trong khoảng này
        allow_html: True nếu message đã là HTML an toàn
    """
    token = str(getattr(settings, "SYSTEM_BOT_TOKEN", "") or getattr(settings, "TELEGRAM_BOT_TOKEN", "") or "").strip()
    chat_id = str(getattr(settings, "DEV_CHAT_ID", "") or "").strip()

    if not token or not chat_id:
        log.warning("Bỏ qua cảnh báo Dev vì thiếu SYSTEM_BOT_TOKEN/TELEGRAM_BOT_TOKEN hoặc DEV_CHAT_ID.")
        return False

    raw_message = _normalize_text(message)
    if not raw_message:
        return False

    if alert_key and cooldown_sec > 0:
        if not _allow_local_alert(alert_key, cooldown_sec):
            log.debug("Bỏ qua dev alert vì đang trong cooldown: key=%s", alert_key)
            return False

    safe_title = _escape_html_if_needed(_normalize_text(title or ""), allow_html=False)
    safe_body = _escape_html_if_needed(raw_message, allow_html=allow_html)

    if safe_title:
        composed = f"🚨 <b>{safe_title}</b>\n\n{safe_body}"
    else:
        composed = safe_body

    # Giữ đủ ngắn và chia chunk an toàn cho Telegram
    chunks = _chunk_message(composed, max_len=SAFE_CHUNK_LEN)
    if not chunks:
        return False

    total = len(chunks)
    all_ok = True

    for idx, chunk in enumerate(chunks, start=1):
        final_text = chunk
        if total > 1:
            prefix = f"<b>[{idx}/{total}]</b>\n"
            if len(prefix) + len(final_text) > TELEGRAM_MAX_LEN:
                final_text = final_text[: TELEGRAM_MAX_LEN - len(prefix) - 20].rstrip() + "…"
            final_text = prefix + final_text

        payload = {
            "chat_id": chat_id,
            "text": final_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": disable_web_page_preview,
        }

        ok = await _post_telegram(payload)
        if not ok:
            all_ok = False
            break

    return all_ok


async def send_dev_alert_once(
    message: str,
    *,
    title: Optional[str] = None,
    alert_key: Optional[str] = None,
    cooldown_sec: int = 300,
    allow_html: bool = False,
) -> bool:
    """
    Helper nhanh để chống spam:
    - mặc định cùng 1 alert_key chỉ báo 1 lần / 5 phút
    """
    final_key = alert_key or _fingerprint(title or "", message)
    return await send_dev_alert(
        message=message,
        title=title,
        alert_key=final_key,
        cooldown_sec=cooldown_sec,
        allow_html=allow_html,
    )
