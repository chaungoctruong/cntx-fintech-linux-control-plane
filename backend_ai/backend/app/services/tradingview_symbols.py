from __future__ import annotations

import re
from typing import Any


_CANONICAL_PREFIX_SYMBOLS = ("XAUUSD",)


def compact_trading_symbol(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").strip().upper())


def canonical_tradingview_symbol(value: Any) -> str:
    """Return the product-level symbol sent by Linux control-plane.

    Broker-specific symbol variants are resolved in the Windows runner, where
    broker/server context is closest to MT5.
    """
    raw = str(value or "").strip()
    compact = compact_trading_symbol(raw)
    if not compact:
        return ""
    for canonical in _CANONICAL_PREFIX_SYMBOLS:
        if compact == canonical or compact.startswith(canonical):
            return canonical
    return raw


def trading_symbols_match(left: Any, right: Any) -> bool:
    left_canonical = canonical_tradingview_symbol(left)
    right_canonical = canonical_tradingview_symbol(right)
    if not left_canonical or not right_canonical:
        return False
    return compact_trading_symbol(left_canonical) == compact_trading_symbol(right_canonical)
