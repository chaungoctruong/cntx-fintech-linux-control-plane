from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from app.ai.query_normalizer import strip_vi_tones


log = logging.getLogger("ai_knowledge")
KNOWLEDGE_ROOT = Path(__file__).resolve().parents[2] / "ai_knowledge"

INTENT_FILES: dict[str, tuple[str, ...]] = {
    "simple_faq": ("cntx_labs_overview.md", "ai_answer_policy.md"),
    "product_support": (
        "cntx_labs_overview.md",
        "mt5_connect_flow.md",
        "bot_runtime.md",
        "runtime/linux_backend.md",
        "ai_answer_policy.md",
    ),
    "technical_debug": (
        "common_mt5_errors.md",
        "runtime/common_errors.md",
        "runtime/windows_runner.md",
        "runtime/sticky_slot.md",
        "mt5_connect_flow.md",
        "ai_answer_policy.md",
    ),
    "account_or_bot_status": (
        "bot_runtime.md",
        "runtime/deployment_lifecycle.md",
        "runtime/windows_runner.md",
        "runtime/sticky_slot.md",
        "ai_answer_policy.md",
    ),
    "trading_knowledge": (
        "lot_margin_spread_swap.md",
        "trading/lot_calculation.md",
        "trading/margin_explainer.md",
        "trading/swap_explainer.md",
        "risk_management.md",
        "ai_answer_policy.md",
    ),
    "pricing_sales": ("sales/sales_playbook.md", "ai_answer_policy.md"),
    "risk_warning": ("risk_management.md", "trading/funded_account_rules.md", "ai_answer_policy.md"),
    "search_required": ("ai_answer_policy.md",),
}

KEYWORD_FILES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("exness", "icmarkets", "ic market", "ic markets", "xm", "vantage"), ("broker/mt5_brokers.md",)),
    (("windows runner", "runner", "vps"), ("runtime/windows_runner.md",)),
    (("linux backend", "control plane"), ("runtime/linux_backend.md",)),
    (("sticky slot", "slot"), ("runtime/sticky_slot.md",)),
    (("deployment", "start bot", "stop bot"), ("runtime/deployment_lifecycle.md",)),
    (("common error", "authorization failed", "order_send", "autotrading"), ("runtime/common_errors.md", "common_mt5_errors.md")),
    (("xauusd", "gold", "vang"), ("trading/xau_usd.md",)),
    (("eurusd",), ("trading/eurusd.md",)),
    (("lot",), ("trading/lot_calculation.md", "lot_margin_spread_swap.md")),
    (("swap", "qua dem", "overnight"), ("trading/swap_explainer.md", "lot_margin_spread_swap.md")),
    (("margin", "ky quy", "leverage", "don bay"), ("trading/margin_explainer.md", "lot_margin_spread_swap.md")),
    (("funded", "prop firm"), ("trading/funded_account_rules.md",)),
    (("dat qua", "mac qua", "pricing", "gia cntx", "phi cntx"), ("sales/sales_playbook.md",)),
)


@lru_cache(maxsize=32)
def _load_markdown(filename: str) -> str:
    path = (KNOWLEDGE_ROOT / filename).resolve()
    try:
        if not path.is_file() or KNOWLEDGE_ROOT.resolve() not in path.parents:
            return ""
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def select_knowledge_files(intent: str, keywords: list[str] | None = None, *, max_files: int = 6) -> list[str]:
    folded_keywords = strip_vi_tones(" ".join(keywords or []))
    keyword_selected: list[str] = []
    for terms, files in KEYWORD_FILES:
        if any(term in folded_keywords for term in terms):
            keyword_selected.extend(files)
    selected = keyword_selected + list(INTENT_FILES.get(intent, ("cntx_labs_overview.md", "ai_answer_policy.md")))
    if any(term in folded_keywords for term in ("risk", "drawdown", "stop out", "all in", "martingale")):
        selected.append("risk_management.md")

    seen: set[str] = set()
    out: list[str] = []
    for filename in selected:
        if filename in seen:
            continue
        seen.add(filename)
        if _load_markdown(filename):
            out.append(filename)
        if len(out) >= max_files:
            break
    return out


def load_knowledge_for_intent(intent: str, keywords: list[str] | None = None) -> str:
    selected = select_knowledge_files(intent, keywords)
    log.info("AI knowledge inject intent=%s files=%s", intent, selected)

    blocks: list[str] = []
    for filename in selected:
        content = _load_markdown(filename)
        if not content:
            continue
        blocks.append(f"[{filename}]\n{content[:1400]}")
    return "\n\n".join(blocks) if blocks else "none"
