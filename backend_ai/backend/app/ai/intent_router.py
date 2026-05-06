from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.ai.query_normalizer import QueryVariants, strip_vi_tones


@dataclass(frozen=True)
class AIRouteDecision:
    intent: str
    preferred_provider: str
    needs_backend_context: bool = False
    needs_knowledge_context: bool = False
    needs_search: bool = False
    needs_stronger_model: bool = False


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _context_text(context: Optional[dict]) -> str:
    if not isinstance(context, dict) or not context:
        return ""
    chunks: list[str] = []
    for key in ("recent_messages", "chat_history", "history", "messages", "conversation", "thread"):
        value = context.get(key)
        if isinstance(value, str):
            chunks.append(value)
            continue
        if isinstance(value, list):
            for item in value[-6:]:
                if isinstance(item, str):
                    chunks.append(item)
                elif isinstance(item, dict):
                    chunks.append(
                        str(
                            item.get("content")
                            or item.get("message")
                            or item.get("text")
                            or item.get("body")
                            or ""
                        )
                    )
    return strip_vi_tones(" ".join(chunk for chunk in chunks if chunk))


def _context_has_product_signal(context: Optional[dict]) -> bool:
    text = _context_text(context)
    if not text:
        return False
    return _contains_any(
        text,
        (
            "cntx",
            "bot",
            "mt5",
            "runner",
            "slot",
            "vps",
            "trading",
            "trade",
            "forex",
            "xauusd",
            "eurusd",
            "lot",
            "margin",
            "spread",
            "swap",
            "tai khoan",
            "account",
            "broker",
            "login",
            "server",
            "lenh",
            "giao dich",
        ),
    )


def _looks_like_contextual_followup(text: str) -> bool:
    if not text:
        return False
    if len(text.split()) <= 4:
        return True
    return _contains_any(
        text,
        (
            "vay",
            "the nao",
            "sao nua",
            "gio sao",
            "tiep theo",
            "con neu",
            "van vay",
            "no bi sao",
            "loi do",
            "cai do",
        ),
    )


def classify_ai_intent(
    variants: QueryVariants,
    *,
    mode: str = "chat",
    use_search: bool = False,
    context: Optional[dict] = None,
) -> AIRouteDecision:
    route_text = strip_vi_tones(
        " ".join(
            [
                variants.original_query,
                variants.normalized_vi_query,
            ]
        )
    )
    mode_norm = strip_vi_tones(mode)

    if _contains_any(
        route_text,
        (
            "chac thang",
            "cam ket loi nhuan",
            "dam bao loi",
            "all in",
            "allin",
            "martingale",
            "gong lo",
            "go lo",
            "vao 10 lot",
            "tang lot de go",
            "nhan doi lot",
        ),
    ):
        return AIRouteDecision(
            intent="risk_warning",
            preferred_provider="ollama",
            needs_knowledge_context=True,
        )

    if _contains_any(
        route_text,
        (
            "gia cntx",
            "cntx gia",
            "phi cntx",
            "gia bao nhieu",
            "phi bao nhieu",
            "bao nhieu tien",
            "dat qua",
            "mac qua",
            "pricing",
            "goi phi",
            "phi thang",
            "co dang tien",
            "vi sao cntx",
        ),
    ):
        return AIRouteDecision(
            intent="pricing_sales",
            preferred_provider="ollama",
            needs_knowledge_context=True,
        )

    support_or_status_signal = _contains_any(
        route_text,
        (
            "bot",
            "mt5",
            "runner",
            "slot",
            "vps",
            "backend",
            "deployment",
            "command_id",
            "log",
            "error",
            "loi",
            "tai khoan",
            "account",
        ),
    )

    if bool(use_search) or (
        _contains_any(
            route_text,
            (
                "search",
                "google",
                "tim kiem",
                "tra cuu",
                "moi nhat",
                "hom nay",
                "tin moi",
                "nguon",
                "link",
            ),
        )
        and not support_or_status_signal
    ):
        return AIRouteDecision(
            intent="search_required",
            preferred_provider="gemini",
            needs_search=True,
            needs_stronger_model=True,
        )

    if _contains_any(
        route_text,
        (
            "khong vao lenh",
            "khong co lenh",
            "khong mo lenh",
            "khong khop lenh",
            "order failed",
            "order_send",
            "authorization failed",
            "invalid account",
            "invalid server",
            "autotrading",
            "allowlivetrading",
        ),
    ):
        return AIRouteDecision(
            intent="technical_debug",
            preferred_provider="gemini",
            needs_backend_context=True,
            needs_knowledge_context=True,
            needs_stronger_model=True,
        )

    if (
        _contains_any(
            route_text,
            (
                "trang thai bot",
                "bot dang chay",
                "bot con chay",
                "bot off",
                "bot on",
                "tai khoan dang",
                "account status",
                "kiem tra bot",
                "kiem tra tai khoan",
                "pnl",
                "equity",
                "balance",
            ),
        )
        or ("bot" in route_text and _contains_any(route_text, ("dang chay", "con chay", "chay khong", "off")))
        or ("tai khoan" in route_text and _contains_any(route_text, ("sao roi", "dang the nao", "status", "connected", "disconnected")))
    ):
        return AIRouteDecision(
            intent="account_or_bot_status",
            preferred_provider="ollama",
            needs_backend_context=True,
            needs_knowledge_context=True,
        )

    if _contains_any(
        route_text,
        (
            "ket noi tai khoan",
            "ket noi mt5",
            "dang nhap mt5",
            "huong dan ket noi",
            "cach dung bot",
            "quan ly bot",
            "doi bot",
            "doi bot code",
            "chuyen bot",
        ),
    ) and not _contains_any(route_text, ("khong", "loi", "error", "failed", "authorization failed")):
        return AIRouteDecision(
            intent="product_support",
            preferred_provider="ollama",
            needs_backend_context=True,
            needs_knowledge_context=True,
        )

    if _contains_any(
        route_text,
        (
            "mt5",
            "vps",
            "backend",
            "runner",
            "slot",
            "heartbeat",
            "log",
            "error",
            "loi",
            "khong vao lenh",
            "order failed",
            "autotrading",
            "auto trading",
            "allowlivetrading",
            "allow live trading",
            "order_send",
            "market closed",
        ),
    ):
        return AIRouteDecision(
            intent="technical_debug",
            preferred_provider="gemini",
            needs_backend_context=True,
            needs_knowledge_context=True,
            needs_stronger_model=True,
        )

    trading_terms = (
        "trading",
        "trade",
        "forex",
        "xauusd",
        "eurusd",
        "usdjpy",
        "lot",
        "margin",
        "spread",
        "swap",
        "qua dem",
        "sl",
        "tp",
        "drawdown",
        "funded",
        "prop firm",
        "risk",
        "stop out",
        "stopout",
    )
    if mode_norm == "market" or _contains_any(route_text, trading_terms):
        complex_markers = (
            "tai sao",
            "vi sao",
            "phan tich",
            "so sanh",
            "bao nhieu",
            "tinh",
            "chien luoc",
            "qua dem",
            "funded",
        )
        is_complex = len(route_text.split()) >= 18 or _contains_any(route_text, complex_markers)
        return AIRouteDecision(
            intent="trading_knowledge",
            preferred_provider="gemini" if is_complex else "ollama",
            needs_knowledge_context=True,
            needs_stronger_model=is_complex,
        )

    if _contains_any(
        route_text,
        (
            "cntx",
            "bot",
            "ket noi",
            "dang nhap",
            "login",
            "server",
            "mat khau",
            "huong dan",
            "start",
            "quan ly bot",
        ),
    ) or (_context_has_product_signal(context) and _looks_like_contextual_followup(route_text)):
        return AIRouteDecision(
            intent="product_support",
            preferred_provider="ollama",
            needs_backend_context=True,
            needs_knowledge_context=True,
        )

    return AIRouteDecision(intent="simple_faq", preferred_provider="ollama")
