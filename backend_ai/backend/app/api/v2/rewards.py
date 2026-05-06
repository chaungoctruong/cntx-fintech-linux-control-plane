"""
Rewards/Affiliate API v2: referral link, stats, leaderboard, bonus history.
User-scoped routes require Telegram Mini App auth and upsert the current user.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Depends

from app.api.v2.control_plane_deps import user_dep
from app.core.rewards_store import (
    get_leaderboard as store_get_leaderboard,
    get_referral_stats,
    list_bonus_events,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/rewards", tags=["rewards-v2"])


def _bot_username() -> str:
    return os.environ.get("TELEGRAM_BOT_USERNAME", "").strip() or "CNTxLabsBot"


def _referral_link(telegram_id: str) -> str:
    bot = _bot_username()
    return f"https://t.me/{bot}?start=ref_{telegram_id}"


@router.get("/info")
def get_rewards_info(
    tg_user: dict[str, Any] = Depends(user_dep),
):
    """GET /info: referral_link, total_referrals, total_bonus."""
    telegram_id = tg_user["telegram_id"]
    total_referrals, total_bonus = get_referral_stats(telegram_id)
    return {
        "referral_link": _referral_link(telegram_id),
        "total_referrals": total_referrals,
        "total_bonus": round(total_bonus, 2),
    }


@router.get("/leaderboard")
def get_rewards_leaderboard():
    """GET /leaderboard: top 10 referrers (masked usernames). No auth required for public board."""
    items = store_get_leaderboard(limit=10)
    return {"leaderboard": items}


@router.get("/bonus-history")
def get_bonus_history(
    tg_user: dict[str, Any] = Depends(user_dep),
    limit: int = 50,
):
    """GET /bonus-history: list of bonus events for the current user."""
    if limit < 1 or limit > 100:
        limit = 50
    events = list_bonus_events(tg_user["telegram_id"], limit=limit)
    return {"events": events}
