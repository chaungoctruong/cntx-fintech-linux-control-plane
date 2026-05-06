"""
Minimal wallet persistence for v2 API only. Lives under app/core per safety constraints.
Creates withdrawal_requests and wallet_transactions tables; does not touch Store or v1.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

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
            CREATE TABLE IF NOT EXISTS withdrawal_requests (
                id SERIAL PRIMARY KEY,
                telegram_id TEXT NOT NULL,
                amount NUMERIC(20, 2) NOT NULL,
                wallet_address TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS wallet_transactions (
                id SERIAL PRIMARY KEY,
                telegram_id TEXT NOT NULL,
                type TEXT NOT NULL,
                amount NUMERIC(20, 2) NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                tx_ref TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
    conn.commit()


@contextmanager
def wallet_connection():
    conn = _get_connection()
    try:
        _ensure_tables(conn)
        yield conn
    finally:
        conn.close()


def list_transactions(telegram_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """List recent wallet_transactions and withdrawal_requests for the user."""
    out: List[Dict[str, Any]] = []
    with wallet_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, telegram_id, type, amount, status, tx_ref, created_at
                FROM wallet_transactions
                WHERE telegram_id = %s
                ORDER BY created_at DESC
                LIMIT %s
            """, (telegram_id, limit))
            for row in cur.fetchall() or []:
                out.append({
                    "id": row[0],
                    "telegram_id": row[1],
                    "type": row[2],
                    "amount": float(row[3]) if row[3] is not None else 0,
                    "status": row[4] or "pending",
                    "tx_ref": row[5],
                    "created_at": row[6].isoformat() if hasattr(row[6], "isoformat") else str(row[6]),
                })
            cur.execute("""
                SELECT id, telegram_id, amount, wallet_address, status, created_at
                FROM withdrawal_requests
                WHERE telegram_id = %s
                ORDER BY created_at DESC
                LIMIT %s
            """, (telegram_id, limit))
            for row in cur.fetchall() or []:
                out.append({
                    "id": f"w_{row[0]}",
                    "telegram_id": row[1],
                    "type": "withdrawal",
                    "amount": float(row[2]) if row[2] is not None else 0,
                    "status": row[4] or "pending",
                    "tx_ref": row[3],
                    "created_at": row[5].isoformat() if hasattr(row[5], "isoformat") else str(row[5]),
                })
    out.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return out[:limit]


def add_withdrawal_request(telegram_id: str, amount: float, wallet_address: str) -> Dict[str, Any]:
    """Insert a withdrawal request; status pending. Returns the created row."""
    with wallet_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO withdrawal_requests (telegram_id, amount, wallet_address, status)
                VALUES (%s, %s, %s, 'pending')
                RETURNING id, telegram_id, amount, wallet_address, status, created_at
            """, (telegram_id, amount, (wallet_address or "").strip()))
            row = cur.fetchone()
            conn.commit()
            if not row:
                return {}
            return {
                "id": row[0],
                "telegram_id": row[1],
                "amount": float(row[2]) if row[2] is not None else 0,
                "wallet_address": row[3],
                "status": row[4] or "pending",
                "created_at": row[5].isoformat() if hasattr(row[5], "isoformat") else str(row[5]),
            }
