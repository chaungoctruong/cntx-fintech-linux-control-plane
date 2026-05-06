import logging
import asyncio
import hmac
import httpx
import json
import time
from typing import Optional, Any, Dict
import re
import unicodedata

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.ai.chat_memory import append_chat_exchange, enrich_context_with_memory
from app.ai.care_campaign_service import looks_like_market_quote_query
from app.ai.deferred_queue import deferred_ai_queue, get_deferred_ai_job
from app.ai.errors import AIOverloadedError, AIProviderUnavailableError
from app.ai.executor import ai_executor
from app.ai.persistent_chat_memory import load_learned_answers, lookup_cached_answer
from app.settings import settings
from app.core.log_hygiene import append_debug_trace

log = logging.getLogger("api_gateway.ai_routes")

router = APIRouter()

START_CTA = "👉 Bấm /start để quay lại menu chính."
AI_DEBUG_CONTEXT_KEYS = ("user_role", "role", "actor_role", "debug", "debug_mode")
_TELEGRAM_MAX_TEXT_LEN = 4096
_TELEGRAM_SAFE_TEXT_CHUNK_LEN = 3900


def _telegram_plain_text_chunks(text: Any, max_len: int = _TELEGRAM_SAFE_TEXT_CHUNK_LEN) -> list[str]:
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    while "\n\n\n" in value:
        value = value.replace("\n\n\n", "\n\n")
    if not value:
        return []
    limit = max(1000, min(int(max_len or _TELEGRAM_SAFE_TEXT_CHUNK_LEN), _TELEGRAM_MAX_TEXT_LEN))
    if len(value) <= limit:
        return [value]

    chunks: list[str] = []
    remaining = value
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, limit)
        if split_at < limit * 0.5:
            split_at = remaining.rfind(" ", 0, limit)
        if split_at < limit * 0.5:
            split_at = limit
        chunk = remaining[:split_at].rstrip()
        remaining = remaining[split_at:].lstrip()
        if chunk:
            chunks.append(chunk)
    return chunks


# =========================================================
# SHARED HTTP CLIENT
# =========================================================
_HTTP_CLIENT: Optional[httpx.AsyncClient] = None


def get_http_client() -> httpx.AsyncClient:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        _HTTP_CLIENT = httpx.AsyncClient(
            timeout=10.0,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
        )
    return _HTTP_CLIENT


async def close_http_client() -> None:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is not None:
        try:
            await _HTTP_CLIENT.aclose()
        except Exception:
            pass
        _HTTP_CLIENT = None


# =========================================================
# OPTIONAL TELEGRAM NOTIFY
# Mặc định TẮT để tránh spam kép:
# user đã nhận HTTP response rồi thì không cần bot bắn thêm 1 tin nữa.
# =========================================================
async def _notify_user_telegram(user_id: str, text: str) -> bool:
    token = str(getattr(settings, "TELEGRAM_BOT_TOKEN", "") or "").strip()
    chat_id = str(user_id or "").strip()
    if not token or not chat_id.isdigit():
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks = _telegram_plain_text_chunks(text)
    if not chunks:
        return False

    try:
        client = get_http_client()
        for idx, chunk in enumerate(chunks, start=1):
            final_text = chunk
            if len(chunks) > 1:
                prefix = f"[{idx}/{len(chunks)}]\n"
                if len(prefix) + len(final_text) > _TELEGRAM_MAX_TEXT_LEN:
                    final_text = final_text[: _TELEGRAM_MAX_TEXT_LEN - len(prefix) - 1].rstrip()
                final_text = prefix + final_text
            res = await client.post(url, json={"chat_id": chat_id, "text": final_text})
            if not res.is_success:
                return False
            if len(chunks) > 1:
                await asyncio.sleep(0.05)
        return True
    except Exception:
        return False


# =========================================================
# DEBUG LOGGING
# =========================================================
def _write_debug_log(message: str, data: dict, hypothesis_id: str) -> None:
    append_debug_trace(
        location="backend_ai/backend/app/ai/routes_ai.py",
        message=message,
        data=data,
        hypothesis_id=hypothesis_id,
    )


def _dbg_fc(message: str, data: dict, *, hypothesis_id: str) -> None:
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(asyncio.to_thread(_write_debug_log, message, data, hypothesis_id))
    except Exception:
        pass


def _request_allows_ai_debug(request: Request) -> bool:
    expected = str(getattr(settings, "BACKEND_API_KEY", "") or "").strip()
    if not expected:
        return False
    headers = getattr(request, "headers", {}) or {}
    try:
        provided = str(headers.get("x-backend-api-key") or headers.get("X-Backend-Api-Key") or "").strip()
        if not provided:
            authorization = str(headers.get("authorization") or headers.get("Authorization") or "").strip()
            if authorization.lower().startswith("bearer "):
                provided = authorization[7:].strip()
    except Exception:
        provided = ""
    return bool(provided and hmac.compare_digest(provided, expected))


def _apply_ai_debug_context(
    context: Optional[Dict[str, Any]],
    payload: Any,
    *,
    debug_allowed: bool,
) -> Dict[str, Any]:
    clean = dict(context or {}) if isinstance(context, dict) else {}
    if not debug_allowed:
        for key in AI_DEBUG_CONTEXT_KEYS:
            clean.pop(key, None)
        return clean

    if payload.user_role:
        clean["user_role"] = _safe_str(payload.user_role)
    if payload.debug:
        clean["debug"] = True
    return clean


# =========================================================
# REQUEST / RESPONSE MODELS
# =========================================================
class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    user_id: Optional[str] = Field("Guest", max_length=50)
    error_code: Optional[str] = Field(None, max_length=100)

    # Khớp hệ thống mới
    mode: Optional[str] = Field("chat", max_length=30)  # chat/support/sales/market/complaint/retention
    channel: Optional[str] = Field("telegram", max_length=30)
    use_search: Optional[bool] = None
    context: Optional[Dict[str, Any]] = None
    user_role: Optional[str] = Field(None, max_length=30)
    debug: Optional[bool] = False

    # Mặc định false để tránh spam thêm 1 tin Telegram ngoài response
    notify_on_failure: bool = False


class ChatResponse(BaseModel):
    ok: bool
    reply: str
    error: Optional[str] = None
    detail: Optional[str] = None
    status: Optional[str] = None
    job_id: Optional[str] = None


def _retry_after_sec() -> int:
    return max(1, int(getattr(settings, "AI_CHAT_RETRY_AFTER_SEC", 15) or 15))


def _deferred_retry_after_sec() -> int:
    return max(1, int(getattr(settings, "AI_DEFERRED_QUEUE_RETRY_AFTER_SEC", 5) or 5))


def _sync_generation_enabled() -> bool:
    return bool(getattr(settings, "AI_CHAT_SYNC_GENERATION_ENABLED", True))


def _learned_direct_reply_enabled() -> bool:
    return bool(getattr(settings, "AI_CHAT_LEARNED_DIRECT_REPLY_ENABLED", True))


def _learned_direct_reply_min_score() -> float:
    return max(0.0, min(float(getattr(settings, "AI_CHAT_LEARNED_DIRECT_REPLY_MIN_SCORE", 0.76) or 0.76), 1.0))


def _best_direct_learned_answer(learned_answers: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not _learned_direct_reply_enabled():
        return None
    threshold = _learned_direct_reply_min_score()
    best: Optional[dict[str, Any]] = None
    best_score = 0.0
    for item in learned_answers or []:
        if not isinstance(item, dict):
            continue
        answer = _safe_str(item.get("answer"))
        if not answer:
            continue
        try:
            score = float(item.get("score") or 0.0)
        except Exception:
            score = 0.0
        if score >= threshold and score >= best_score:
            best = item
            best_score = score
    return best


# =========================================================
# NORMALIZERS / HELPERS
# =========================================================
NEWS_TOPIC_HINTS = [
    "chien su",
    "chien tranh",
    "iran",
    "israel",
    "ukraine",
    "russia",
    "fed",
    "fomc",
    "cpi",
    "nfp",
    "nonfarm",
    "lai suat",
    "vang",
    "gia vang",
    "xau",
    "xauusd",
    "dau",
    "gia dau",
    "brent",
    "wti",
    "bitcoin",
    "btc",
    "crypto",
    "forex",
    "usd",
    "dollar",
    "usdjpy",
    "eurusd",
]

NEWS_TIME_HINTS = [
    "hom nay",
    "co gi moi",
    "co tin gi moi",
    "tin tuc",
    "tin moi",
    "tin nong",
    "co gi hot",
    "news",
    "moi nhat",
    "cap nhat",
    "vua ra",
    "toi nay",
    "sang nay",
]

SEARCH_REQUEST_HINTS = [
    "google",
    "tra google",
    "tim google",
    "tìm google",
    "tim kiem",
    "tìm kiếm",
    "tra cuu",
    "tra cứu",
    "search",
    "tim giup",
    "tìm giúp",
    "tim dum",
    "tìm dùm",
    "nguon",
    "nguồn",
    "bai bao",
    "bài báo",
    "moi nhat",
    "mới nhất",
    "cap nhat",
    "cập nhật",
    "hom nay",
    "hôm nay",
]

SUPPORT_TROUBLESHOOT_HINTS = [
    "bot",
    "vao lenh",
    "vao lenh it",
    "khong vao lenh",
    "khong mo lenh",
    "roi lenh",
    "loi",
    "lỗi",
    "check",
    "kiem tra",
    "kiểm tra",
    "lam sao",
    "làm sao",
    "can gi",
    "cần gì",
    "em nen",
    "em nên",
    "giup em",
    "giúp em",
    "chuoi nao",
    "chuỗi nào",
    "bo sot",
    "bỏ sót",
    "running",
    "off",
    "drawdown",
    "mat khau",
    "mật khẩu",
    "server",
    "login",
]


def _safe_str(value: Optional[str], default: str = "") -> str:
    return str(value or default).strip()


def _safe_mode(mode: Optional[str]) -> str:
    mode = _safe_str(mode, "chat").lower()
    allowed = {"chat", "support", "sales", "market", "complaint", "retention"}
    return mode if mode in allowed else "chat"


def _normalize_vi(text: Optional[str]) -> str:
    text = _safe_str(text).lower()
    text = text.replace("đ", "d")
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _ensure_start_cta(text: Optional[str]) -> str:
    clean_text = _safe_str(text)
    if not clean_text:
        return START_CTA
    if "/start" in clean_text.lower():
        return clean_text
    return f"{clean_text}\n\n{START_CTA}"


def _looks_like_news_query(text: Optional[str]) -> bool:
    msg = _normalize_vi(text)
    if not msg:
        return False

    if looks_like_market_quote_query(text):
        return False

    if any(phrase in msg for phrase in ("khong dau", "co dau")) and not any(
        oil_phrase in msg for oil_phrase in ("gia dau", "dau tho", "gia xang dau", "oil", "brent", "wti")
    ):
        return False

    if any(hint in msg for hint in SUPPORT_TROUBLESHOOT_HINTS):
        return False

    has_topic = any(hint in msg for hint in NEWS_TOPIC_HINTS)
    has_timeliness = any(hint in msg for hint in NEWS_TIME_HINTS)
    has_search_request = any(hint in msg for hint in SEARCH_REQUEST_HINTS)

    if has_topic and (has_timeliness or has_search_request):
        return True

    soft_patterns = [
        r"\btin\b.*\b(vang|dau|btc|bitcoin|crypto|forex|usd|fed|cpi|nfp)\b",
        r"\bhom nay\b.*\bgi moi\b",
        r"\bco\b.*\btin\b.*\bmoi\b",
        r"\btin\b.*\bhom nay\b",
        r"\bmarket\b.*\bhom nay\b",
        r"\b(cap nhat|moi nhat)\b.*\b(vang|dau|btc|bitcoin|crypto|forex|usd|fed|cpi|nfp)\b",
    ]
    return any(re.search(p, msg) for p in soft_patterns)


def _looks_like_search_request(text: Optional[str]) -> bool:
    msg = _normalize_vi(text)
    if not msg:
        return False
    return any(hint in msg for hint in SEARCH_REQUEST_HINTS)


def _effective_mode(payload: ChatRequest) -> str:
    mode = _safe_mode(payload.mode)

    # Nếu client đã chỉ định rõ mode khác chat thì giữ nguyên
    if mode != "chat":
        return mode

    if looks_like_market_quote_query(payload.message):
        return "market"

    # Tự nâng cấp sang market nếu câu hỏi mang tính thời sự/tin nóng
    if _looks_like_news_query(payload.message):
        return "market"

    return mode


def _should_use_search(payload: ChatRequest) -> bool:
    if payload.use_search is not None:
        return bool(payload.use_search)

    if looks_like_market_quote_query(payload.message):
        return False

    mode = _effective_mode(payload)
    if mode == "market":
        return True

    if _looks_like_search_request(payload.message):
        return True

    return _looks_like_news_query(payload.message)


async def _call_executor(
    payload: ChatRequest,
    *,
    context_override: Optional[Dict[str, Any]] = None,
    debug_allowed: bool = False,
) -> str:
    """
    Tương thích cả executor cũ lẫn executor mới.
    """
    user_msg = _safe_str(payload.message)
    error_code = _safe_str(payload.error_code) or None
    user_id = _safe_str(payload.user_id, "Guest")
    mode = _effective_mode(payload)
    channel = _safe_str(payload.channel, "telegram").lower()
    use_search = _should_use_search(payload)
    context = _apply_ai_debug_context(
        context_override if isinstance(context_override, dict) else (payload.context or {}),
        payload,
        debug_allowed=debug_allowed,
    )

    try:
        return await ai_executor.handle_user_issue(
            user_msg=user_msg,
            error_code=error_code,
            user_id=user_id,
            mode=mode,
            channel=channel,
            use_search=use_search,
            context=context,
        )
    except TypeError as exc:
        msg = str(exc)
        signature_mismatch = (
            "unexpected keyword argument" in msg
            or "positional arguments" in msg
            or "required positional argument" in msg
        )
        if not signature_mismatch:
            raise

        _dbg_fc(
            "ai.chat.executor_signature_fallback",
            {
                "user_id": user_id,
                "mode": mode,
                "channel": channel,
                "use_search": use_search,
                "error": msg[:180],
            },
            hypothesis_id="H2_FALLBACK",
        )

        return await ai_executor.handle_user_issue(
            user_msg=user_msg,
            error_code=error_code,
            user_id=user_id,
        )


async def _try_enqueue_overloaded_request(
    payload: ChatRequest,
    *,
    mode: str,
    use_search: bool,
    context_override: Optional[Dict[str, Any]] = None,
    debug_allowed: bool = False,
) -> tuple[str, int]:
    user_id = _safe_str(payload.user_id, "Guest")
    channel = _safe_str(payload.channel, "telegram").lower()
    if not deferred_ai_queue.can_accept(user_id=user_id, channel=channel):
        return "", 0

    try:
        return await deferred_ai_queue.submit(
            user_msg=_safe_str(payload.message),
            error_code=_safe_str(payload.error_code) or None,
            user_id=user_id,
            mode=mode,
            channel=channel,
            use_search=bool(use_search),
            context=_apply_ai_debug_context(
                context_override if isinstance(context_override, dict) else (payload.context or {}),
                payload,
                debug_allowed=debug_allowed,
            ),
        )
    except asyncio.QueueFull:
        return "", 0


async def _queue_background_prepared_answer(
    payload: ChatRequest,
    *,
    mode: str,
    use_search: bool,
    context_override: Dict[str, Any],
    debug_allowed: bool,
) -> JSONResponse:
    job_id, queued_position = await _try_enqueue_overloaded_request(
        payload,
        mode=mode,
        use_search=use_search,
        context_override=context_override,
        debug_allowed=debug_allowed,
    )
    if queued_position > 0:
        user_id = _safe_str(payload.user_id, "Guest")
        channel = _safe_str(payload.channel, "telegram").lower()
        if deferred_ai_queue.can_notify_telegram(user_id=user_id, channel=channel):
            msg = _ensure_start_cta(
                "⏳ Dạ, em đã nhận câu hỏi. Em đang chuẩn bị câu trả lời và sẽ gửi lại ngay khi xong."
            )
        else:
            msg = _ensure_start_cta(
                "⏳ Dạ, em đã nhận câu hỏi. Câu này cần thêm chút thời gian, Sếp kiểm tra lại sau ít phút giúp em nhé."
            )
        log.info(
            "[AI ROUTER] Background prepared-answer queued user=%s mode=%s position=%s job_id=%s",
            user_id,
            mode,
            queued_position,
            job_id,
        )
        return JSONResponse(
            status_code=202,
            headers={"Retry-After": str(_deferred_retry_after_sec())},
            content=ChatResponse(
                ok=False,
                reply=msg,
                error="ai_preparing",
                status="queued",
                job_id=job_id,
                detail=f"queued_position={queued_position}",
            ).model_dump(),
        )

    msg = _ensure_start_cta(
        "⚠️ Dạ, hàng đợi chuẩn bị câu trả lời đang đầy. Sếp thử lại sau ít phút giúp em nhé!"
    )
    return JSONResponse(
        status_code=503,
        headers={"Retry-After": str(_deferred_retry_after_sec())},
        content=ChatResponse(
            ok=False,
            reply=msg,
            error="ai_queue_unavailable",
            status="queue_unavailable",
            detail="retry_later",
        ).model_dump(),
    )


# =========================================================
# ROUTE
# =========================================================
@router.post("/chat", response_model=ChatResponse, summary="Hubbot giao tiếp với CNTx labs")
async def ai_chat_endpoint(payload: ChatRequest, request: Request):
    service_online = bool(getattr(request.app.state, "is_service_online", False))
    mode = _effective_mode(payload)
    use_search = _should_use_search(payload)
    user_id = _safe_str(payload.user_id, "Guest")
    debug_allowed = _request_allows_ai_debug(request)
    resolved_context = await enrich_context_with_memory(user_id, payload.context)
    resolved_context = _apply_ai_debug_context(
        resolved_context,
        payload,
        debug_allowed=debug_allowed,
    )

    cached_answer = await lookup_cached_answer(
        user_id=user_id,
        question=_safe_str(payload.message),
        mode=mode,
        context=resolved_context,
        use_search=use_search,
    )
    if cached_answer and _safe_str(cached_answer.reply):
        reply = ai_executor._finalize_response(
            _safe_str(cached_answer.reply),
            context=resolved_context,
            user_msg=_safe_str(payload.message),
        )
        await append_chat_exchange(
            user_id,
            _safe_str(payload.message),
            reply,
            mode=mode,
            status="cached",
            source="db_cache",
            context=resolved_context,
            use_search=use_search,
        )
        _dbg_fc(
            "ai.chat.db_cache_hit",
            {
                "user_id": user_id,
                "mode": mode,
                "cache_id": cached_answer.cache_id,
                "hit_count": cached_answer.hit_count,
            },
            hypothesis_id="H2_DB_CACHE",
        )
        return ChatResponse(ok=True, reply=reply, status="cached")

    learned_answers = await load_learned_answers(
        user_id=user_id,
        question=_safe_str(payload.message),
        mode=mode,
        context=resolved_context,
        use_search=use_search,
    )
    if learned_answers:
        resolved_context["learned_answers"] = learned_answers

    _dbg_fc(
        "ai.chat.request",
        {
            "service_online": service_online,
            "user_id": user_id,
            "mode": mode,
            "use_search": use_search,
            "learned_answers": len(learned_answers),
            "channel": _safe_str(payload.channel, "telegram").lower(),
        },
        hypothesis_id="H2",
    )

    direct_learned = _best_direct_learned_answer(learned_answers)
    if direct_learned is not None:
        reply = ai_executor._finalize_response(
            _safe_str(direct_learned.get("answer")),
            context=resolved_context,
            user_msg=_safe_str(payload.message),
        )
        await append_chat_exchange(
            user_id,
            _safe_str(payload.message),
            reply,
            mode=mode,
            status="learned",
            source="learned_memory",
            context=resolved_context,
            use_search=use_search,
        )
        _dbg_fc(
            "ai.chat.learned_direct_hit",
            {
                "user_id": user_id,
                "mode": mode,
                "cache_id": direct_learned.get("cache_id"),
                "score": direct_learned.get("score"),
                "scope": direct_learned.get("scope"),
            },
            hypothesis_id="H2_LEARNED_DIRECT",
        )
        return ChatResponse(
            ok=True,
            reply=reply,
            status="learned",
            detail=f"score={float(direct_learned.get('score') or 0.0):.4f}",
        )

    if not _sync_generation_enabled():
        return await _queue_background_prepared_answer(
            payload,
            mode=mode,
            use_search=use_search,
            context_override=resolved_context,
            debug_allowed=debug_allowed,
        )

    try:
        reply = await asyncio.wait_for(
            _call_executor(payload, context_override=resolved_context, debug_allowed=debug_allowed),
            timeout=float(getattr(settings, "AI_CHAT_TIMEOUT_SEC", 60.0) or 60.0),
        )

        reply = _safe_str(reply)
        if not reply:
            reply = "Dạ CNTx labs nhận lệnh rồi, nhưng câu trả lời đang bị rỗng. Sếp nhắn lại 1 câu ngắn giúp em nhé."

        await append_chat_exchange(
            user_id,
            _safe_str(payload.message),
            reply,
            mode=mode,
            status="done",
            source="executor",
            context=resolved_context,
            use_search=use_search,
        )

        return ChatResponse(ok=True, reply=reply, status="done")

    except asyncio.TimeoutError:
        if bool(getattr(settings, "AI_CHAT_IMMEDIATE_FALLBACK_ENABLED", True)):
            fallback = ai_executor.quick_fallback_reply(
                _safe_str(payload.message),
                mode=mode,
                context=resolved_context,
                reason="timeout",
            )
            fallback = _safe_str(fallback)
            if fallback:
                await append_chat_exchange(
                    user_id,
                    _safe_str(payload.message),
                    fallback,
                    mode=mode,
                    status="fallback",
                    source="fallback",
                    context=resolved_context,
                    use_search=use_search,
                )
                log.warning("[AI ROUTER] Timeout fallback served user=%s mode=%s", user_id, mode)
                return ChatResponse(ok=True, reply=fallback, status="fallback")

        notify_msg = _ensure_start_cta(
            "⏳ Dạ, CNTx labs đang phân tích hơi sâu nên bị timeout. Sếp hỏi lại em sau 1 phút nhé!"
        )
        delivered = False

        if payload.notify_on_failure:
            delivered = await _notify_user_telegram(user_id, notify_msg)

        log.warning("[AI ROUTER] Timeout khi xử lý request user=%s mode=%s", user_id, mode)

        _dbg_fc(
            "ai.chat.timeout",
            {
                "user_id": user_id,
                "mode": mode,
                "use_search": use_search,
                "notified": bool(delivered),
            },
            hypothesis_id="H_TIMEOUT",
        )

        return JSONResponse(
            status_code=504,
            headers={"Retry-After": str(_retry_after_sec())},
            content=ChatResponse(
                ok=False,
                reply=notify_msg,
                error="ai_timeout",
            ).model_dump(),
        )

    except AIOverloadedError as e:
        if bool(getattr(settings, "AI_CHAT_IMMEDIATE_FALLBACK_ENABLED", True)):
            fallback = ai_executor.quick_fallback_reply(
                _safe_str(payload.message),
                mode=mode,
                context=resolved_context,
                reason="overloaded",
            )
            fallback = _safe_str(fallback)
            if fallback:
                await append_chat_exchange(
                    user_id,
                    _safe_str(payload.message),
                    fallback,
                    mode=mode,
                    status="fallback",
                    source="fallback",
                    context=resolved_context,
                    use_search=use_search,
                )
                log.warning(
                    "[AI ROUTER] Overload fallback served user=%s mode=%s detail=%s",
                    user_id,
                    mode,
                    getattr(e, "detail", str(e)),
                )
                return ChatResponse(ok=True, reply=fallback, status="fallback")

        job_id, queued_position = await _try_enqueue_overloaded_request(
            payload,
            mode=mode,
            use_search=use_search,
            context_override=resolved_context,
            debug_allowed=debug_allowed,
        )
        if queued_position > 0:
            channel = _safe_str(payload.channel, "telegram").lower()
            if deferred_ai_queue.can_notify_telegram(user_id=user_id, channel=channel):
                msg = _ensure_start_cta(
                    "⏳ Dạ, hiện đang có nhiều câu hỏi. Em đã nhận câu này và sẽ trả lời ngay khi tới lượt."
                )
            else:
                msg = _ensure_start_cta(
                    "⏳ Dạ, hiện đang có nhiều câu hỏi. Em đã nhận câu này, Sếp kiểm tra lại sau ít phút giúp em nhé."
                )
            log.info("[AI ROUTER] Deferred queue accepted user=%s mode=%s position=%s job_id=%s", user_id, mode, queued_position, job_id)
            return JSONResponse(
                status_code=202,
                headers={"Retry-After": str(_deferred_retry_after_sec())},
                content=ChatResponse(
                    ok=False,
                    reply=msg,
                    error="ai_queued",
                    status="queued",
                    job_id=job_id,
                    detail=f"queued_position={queued_position}",
                ).model_dump(),
            )

        retry_after = max(1, int(getattr(e, "retry_after_sec", _retry_after_sec()) or _retry_after_sec()))
        msg = _ensure_start_cta(
            "⚠️ Dạ, CNTx labs đang quá tải tạm thời. Sếp chờ chút rồi nhắn lại giúp em nhé!"
        )
        log.warning("[AI ROUTER] Overloaded user=%s mode=%s detail=%s", user_id, mode, getattr(e, "detail", str(e)))
        return JSONResponse(
            status_code=503,
            headers={"Retry-After": str(retry_after)},
            content=ChatResponse(
                ok=False,
                reply=msg,
                error="ai_overloaded",
                status="overloaded",
                detail="retry_later",
            ).model_dump(),
        )

    except AIProviderUnavailableError as e:
        retry_after = max(1, int(getattr(e, "retry_after_sec", _retry_after_sec()) or _retry_after_sec()))
        msg = _ensure_start_cta(
            "⚠️ Dạ, hệ thống AI đang tạm thời chưa sẵn sàng. Sếp thử lại sau ít phút giúp em nhé!"
        )
        log.error("[AI ROUTER] Provider unavailable user=%s mode=%s detail=%s", user_id, mode, getattr(e, "detail", str(e)))
        return JSONResponse(
            status_code=503,
            headers={"Retry-After": str(retry_after)},
            content=ChatResponse(
                ok=False,
                reply=msg,
                error="ai_provider_unavailable",
                status="provider_unavailable",
                detail="provider_unavailable",
            ).model_dump(),
        )

    except Exception as e:
        notify_msg = _ensure_start_cta(
            "⚠️ Dạ, hệ thống AI đang khởi động lại đường truyền. Sếp thử lại sau 30s giúp em nhé!"
        )
        delivered = False

        if payload.notify_on_failure:
            delivered = await _notify_user_telegram(user_id, notify_msg)

        log.error("[AI ROUTER] Lỗi xử lý: %s", e, exc_info=True)
        log.info("ai_chat notify_user sent=%s user_id=%s", bool(delivered), user_id)

        _dbg_fc(
            "ai.chat.exception",
            {
                "user_id": user_id,
                "mode": mode,
                "use_search": use_search,
                "error": str(e)[:180],
                "notified": bool(delivered),
            },
            hypothesis_id="H3",
        )

        return ChatResponse(
            ok=False,
            reply=notify_msg,
            error="ai_execution_failed",
            status="failed",
            detail="internal_error",
        )


@router.get("/chat/jobs/{job_id}", response_model=ChatResponse, summary="Kiểm tra trạng thái job AI đang xếp hàng")
async def ai_chat_job_status(job_id: str):
    state = await get_deferred_ai_job(job_id)
    if not state:
        return JSONResponse(
            status_code=404,
            content=ChatResponse(
                ok=False,
                reply="⚠️ Không tìm thấy job này hoặc job đã hết hạn.",
                error="ai_job_not_found",
                status="not_found",
                job_id=job_id,
            ).model_dump(),
        )

    status = str(state.get("status") or "").strip().lower()
    raw_reply = _safe_str(state.get("reply") or "")
    error = str(state.get("error") or "") or None
    detail = str(state.get("detail") or "") or None

    if status in {"queued", "processing"}:
        pending_text = "⏳ Job này vẫn đang chờ tới lượt xử lý." if status == "queued" else "⏳ CNTx labs đang xử lý câu hỏi này, Sếp chờ thêm chút giúp em nhé."
        pending_reply = _ensure_start_cta(raw_reply or pending_text)
        return JSONResponse(
            status_code=202,
            headers={"Retry-After": str(_deferred_retry_after_sec())},
            content=ChatResponse(
                ok=False,
                reply=pending_reply,
                error=error,
                detail=detail,
                status=status,
                job_id=job_id,
            ).model_dump(),
        )

    if status == "done":
        return ChatResponse(
            ok=True,
            reply=_ensure_start_cta(raw_reply),
            status="done",
            job_id=job_id,
            detail=detail,
        )

    return ChatResponse(
        ok=False,
        reply=_ensure_start_cta(raw_reply or "⚠️ Job này đã xử lý xong nhưng có lỗi."),
        error=error or "ai_job_failed",
        detail=detail,
        status=status or "failed",
        job_id=job_id,
    )
