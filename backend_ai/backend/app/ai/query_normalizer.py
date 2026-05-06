from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class QueryVariants:
    original_query: str
    normalized_vi_query: str
    expanded_trading_keywords: list[str]


def strip_vi_tones(text: str) -> str:
    raw = str(text or "").lower().strip().replace("đ", "d")
    raw = unicodedata.normalize("NFD", raw)
    raw = "".join(ch for ch in raw if unicodedata.category(ch) != "Mn")
    raw = re.sub(r"[^a-z0-9\s]", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


_PHRASE_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("khong vao lenh", "không vào lệnh"),
    ("di", "đi"),
    ("khong mo lenh", "không mở lệnh"),
    ("khong khop lenh", "không khớp lệnh"),
    ("vao lenh", "vào lệnh"),
    ("mo lenh", "mở lệnh"),
    ("khop lenh", "khớp lệnh"),
    ("roi lenh", "rơi lệnh"),
    ("bo lenh", "bỏ lệnh"),
    ("qua dem", "qua đêm"),
    ("ton bao nhieu", "tốn bao nhiêu"),
    ("bao nhieu", "bao nhiêu"),
    ("mat khau", "mật khẩu"),
    ("tai khoan", "tài khoản"),
    ("ket noi", "kết nối"),
    ("ky quy", "ký quỹ"),
    ("giao dich", "giao dịch"),
    ("loi ky thuat", "lỗi kỹ thuật"),
    ("khong du margin", "không đủ margin"),
    ("spread gian", "spread giãn"),
    ("vps mat ket noi", "VPS mất kết nối"),
    ("mt5", "MT5"),
    ("eurusd", "EURUSD"),
    ("xauusd", "XAUUSD"),
    ("usdjpy", "USDJPY"),
    ("gbpusd", "GBPUSD"),
)


def normalize_vi_query(text: str) -> str:
    raw = str(text or "").strip()
    folded = strip_vi_tones(raw)
    if not folded:
        return raw
    normalized = folded
    for src, dst in _PHRASE_REPLACEMENTS:
        normalized = re.sub(rf"(?<![a-z0-9]){re.escape(src)}(?![a-z0-9])", dst, normalized)
    return normalized.strip()


def _append_unique(items: list[str], values: tuple[str, ...]) -> None:
    for value in values:
        if value and value not in items:
            items.append(value)


def expand_trading_keywords(text: str) -> list[str]:
    norm = strip_vi_tones(text)
    keywords: list[str] = []

    if "bot" in norm and ("khong vao lenh" in norm or "khong mo lenh" in norm or "roi lenh" in norm):
        _append_unique(
            keywords,
            (
                "bot không vào lệnh",
                "MT5 order failed",
                "AutoTrading disabled",
                "AllowLiveTrading",
                "order_send",
                "spread giãn",
                "market closed",
                "free margin",
            ),
        )

    if "mt5" in norm or "metatrader" in norm:
        _append_unique(
            keywords,
            (
                "MT5",
                "Expert Advisors",
                "AutoTrading",
                "AllowLiveTrading",
                "server login",
                "trade context busy",
            ),
        )

    if "lot" in norm:
        _append_unique(
            keywords,
            (
                "lot size",
                "contract size",
                "pip value",
                "margin requirement",
                "free margin",
            ),
        )

    if "qua dem" in norm or "overnight" in norm or "swap" in norm:
        _append_unique(
            keywords,
            (
                "swap",
                "overnight financing",
                "triple swap",
                "broker contract specification",
                "long swap",
                "short swap",
            ),
        )

    if any(symbol in norm for symbol in ("eurusd", "xauusd", "usdjpy", "gbpusd")):
        _append_unique(keywords, ("symbol contract specification", "spread", "swap long", "swap short"))

    if any(term in norm for term in ("margin", "ky quy", "call margin", "stopout", "stop out")):
        _append_unique(keywords, ("margin level", "stop out", "leverage", "equity", "free margin"))

    if any(term in norm for term in ("loi", "error", "log", "vps", "backend", "runner", "slot")):
        _append_unique(
            keywords,
            (
                "technical debug checklist",
                "runner heartbeat",
                "deployment_id",
                "command_id",
                "runtime log",
            ),
        )

    return keywords[:16]


def build_query_variants(text: str) -> QueryVariants:
    original = str(text or "").strip()
    return QueryVariants(
        original_query=original,
        normalized_vi_query=normalize_vi_query(original),
        expanded_trading_keywords=expand_trading_keywords(original),
    )
