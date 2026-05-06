# -*- coding: utf-8 -*-
"""AI chat via backend."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Optional

from app.config import AI_MAX_CHARS
from app.api.client import api_json

log = logging.getLogger("hubbot")

_DEFAULT_EMPTY_REPLY = "Dạ CNTx labs đây, Sếp cứ nhắn cụ thể hơn 1 chút là em xử tiếp ngay."
_DEFAULT_ERROR_REPLY = "⚠️ CNTx labs đang bận đường truyền, Sếp thử lại sau ít phút nhé!"
_DEFAULT_BUSY_REPLY = "🤖 CNTx labs đang hơi quá tải. Sếp nhắn lại giúp em nhé."


def _clean_text(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _clip_prompt(prompt: str) -> str:
    prompt = _clean_text(prompt)
    if not prompt:
        return ""
    if len(prompt) > AI_MAX_CHARS:
        prompt = prompt[:AI_MAX_CHARS].rstrip() + "\n(…cắt bớt vì quá dài)"
    return prompt


def _extract_reply(js: dict[str, Any]) -> str:
    """
    Hỗ trợ nhiều shape response khác nhau từ backend:
    - {"reply": "..."}
    - {"data": {"reply": "..."}}
    - {"data": "..."}
    - {"message": "..."}
    """
    if not isinstance(js, dict):
        return ""

    candidates = [
        js.get("reply"),
        (js.get("data") or {}).get("reply") if isinstance(js.get("data"), dict) else None,
        js.get("message"),
        js.get("data") if isinstance(js.get("data"), str) else None,
    ]

    for item in candidates:
        if isinstance(item, str) and item.strip():
            return _clean_text(item)

    return ""


async def backend_ai_chat(
    prompt: str,
    user_id: int,
    *,
    mode: str = "chat",
    channel: str = "telegram",
    use_search: Optional[bool] = None,
    context: Optional[dict[str, Any]] = None,
    retries: int = 0,
) -> str:
    """
    Gửi prompt lên backend AI.

    mode gợi ý:
    - chat
    - support
    - sales
    - market
    - complaint
    - retention

    use_search:
    - True  -> backend có thể bật Gemini Google Search
    - False -> backend chỉ dùng model thường
    - None  -> để backend tự quyết
    """
    cleaned_prompt = _clip_prompt(prompt)
    if not cleaned_prompt:
        return _DEFAULT_EMPTY_REPLY

    payload: dict[str, Any] = {
        "message": cleaned_prompt,
        "user_id": str(user_id),
        "mode": str(mode or "chat").strip().lower(),
        "channel": str(channel or "telegram").strip().lower(),
    }

    if use_search is not None:
        payload["use_search"] = bool(use_search)

    if context:
        payload["context"] = context

    attempts = max(1, int(retries) + 1)

    for attempt in range(1, attempts + 1):
        try:
            js = await api_json("POST", "/ai/chat", json_body=payload)

            if not isinstance(js, dict):
                log.error("Backend AI invalid response type: %r", type(js))
                return _DEFAULT_ERROR_REPLY

            if js.get("ok") is False:
                status = str(js.get("status") or "").strip().lower()
                error_code = str(js.get("error") or "").strip().lower()
                if status in {"queued", "processing"} or error_code in {"ai_queued", "ai_preparing"}:
                    log.info("Backend AI pending response: status=%s error=%s job_id=%s", status, error_code, js.get("job_id"))
                else:
                    log.warning("Backend AI Error: %s", js)

                # Nếu backend có message cụ thể thì ưu tiên dùng
                reply = _extract_reply(js)
                if reply:
                    return reply

                # Lỗi tạm thời thì retry nhẹ
                if attempt < attempts:
                    await asyncio.sleep(0.8 * attempt)
                    continue

                return _DEFAULT_ERROR_REPLY

            reply = _extract_reply(js)
            if reply:
                return reply

            log.warning("Backend AI returned empty reply: %s", js)
            return "Dạ CNTx labs nhận được tín hiệu rồi, nhưng câu trả lời đang bị rỗng. Sếp nhắn lại giúp em 1 câu ngắn nhé."

        except Exception as e:
            log.warning("backend_ai_chat failed (attempt %s/%s): %s", attempt, attempts, str(e)[:200])
            if attempt < attempts:
                await asyncio.sleep(0.8 * attempt)
                continue

    return _DEFAULT_BUSY_REPLY
