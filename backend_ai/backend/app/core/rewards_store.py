"""
Rewards/affiliate persistence for v2 API only. Lives under app/core.
Creates referrals and bonus_events tables; does not touch Store or v1.
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Dict, List, Tuple

from app.settings import settings

log = logging.getLogger(__name__)


def _get_connection():
    import psycopg2
    return psycopg2.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        dbname=settings.POSTGRES_DB,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
    )


def _ensure_tables(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id SERIAL PRIMARY KEY,
                referrer_telegram_id TEXT NOT NULL,
                referred_telegram_id TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(referred_telegram_id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bonus_events (
                id SERIAL PRIMARY KEY,
                telegram_id TEXT NOT NULL,
                amount NUMERIC(20, 2) NOT NULL,
                reason TEXT NOT NULL,
                ref_telegram_id TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
    conn.commit()


@contextmanager
def rewards_connection():
    conn = _get_connection()
    try:
        _ensure_tables(conn)
        yield conn
    finally:
        conn.close()


def get_referral_stats(telegram_id: str) -> Tuple[int, float]:
    """Return (total_referrals, total_bonus) for the user."""
    count = 0
    total_bonus = 0.0
    with rewards_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM referrals WHERE referrer_telegram_id = %s
            """, (telegram_id,))
            row = cur.fetchone()
            count = int(row[0]) if row and row[0] is not None else 0
            cur.execute("""
                SELECT COALESCE(SUM(amount), 0) FROM bonus_events WHERE telegram_id = %s
            """, (telegram_id,))
            row = cur.fetchone()
            total_bonus = float(row[0]) if row and row[0] is not None else 0.0
    return count, total_bonus


def get_leaderboard(limit: int = 10) -> List[Dict[str, Any]]:
    """Return top N referrers: [{ rank, referral_count, masked_username }, ...]."""
    out: List[Dict[str, Any]] = []
    with rewards_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT referrer_telegram_id, COUNT(*) AS cnt
                FROM referrals
                GROUP BY referrer_telegram_id
                ORDER BY cnt DESC
                LIMIT %s
            """, (limit,))
            for idx, row in enumerate(cur.fetchall() or [], 1):
                tg_id = (row[0] or "").strip()
                cnt = int(row[1]) if row[1] is not None else 0
                masked = f"user_***{tg_id[-3:]}" if len(tg_id) >= 3 else "user_***"
                out.append({
                    "rank": idx,
                    "referral_count": cnt,
                    "masked_username": masked,
                })
    return out


def list_bonus_events(telegram_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Return recent bonus events for the user (for BonusHistory)."""
    out: List[Dict[str, Any]] = []
    with rewards_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, telegram_id, amount, reason, ref_telegram_id, created_at
                FROM bonus_events
                WHERE telegram_id = %s
                ORDER BY created_at DESC
                LIMIT %s
            """, (telegram_id, limit))
            for row in cur.fetchall() or []:
                ref_id = row[4]
                reason = row[3] or "Bonus"
                if ref_id:
                    reason = f"Referral bonus from user ***{str(ref_id)[-3:]} - ${float(row[2]):.2f}"
                else:
                    reason = f"{reason} - ${float(row[2]):.2f}"
                out.append({
                    "id": row[0],
                    "amount": float(row[2]) if row[2] is not None else 0,
                    "reason": reason,
                    "created_at": row[5].isoformat() if hasattr(row[5], "isoformat") else str(row[5]),
                })
    return out
