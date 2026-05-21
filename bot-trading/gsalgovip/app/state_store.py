"""PostgreSQL-backed state store for GsAlgoVIP.

Bot owns its own database; DATABASE_URL is injected by the platform runtime
context. This store does NOT touch the Linux backend's core PostgreSQL.

Tables:
- signals(id, nonce, idempotency_key, ...)
- executions(id, signal_id, ...)

Concurrency:
- claim_pending_signal() uses FOR UPDATE SKIP LOCKED so multiple workers can
  consume the queue safely without losing or double-processing rows.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from .models import ExecutionResult, TradingViewSignal, utc_now_iso


SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS signals (
    id                BIGSERIAL PRIMARY KEY,
    nonce             TEXT NOT NULL,
    idempotency_key   TEXT NOT NULL,
    strategy          TEXT NOT NULL,
    strategy_version  TEXT NOT NULL,
    symbol            TEXT NOT NULL,
    timeframe         TEXT NOT NULL,
    side              TEXT NOT NULL,
    entry             DOUBLE PRECISION NOT NULL,
    sl                DOUBLE PRECISION NOT NULL,
    tp                DOUBLE PRECISION NOT NULL,
    sl_value          DOUBLE PRECISION NOT NULL,
    tp_value          DOUBLE PRECISION NOT NULL,
    bar_time_ms       BIGINT NOT NULL,
    config_key        TEXT NOT NULL,
    status            TEXT NOT NULL,
    raw_payload       JSONB NOT NULL,
    created_at        TEXT NOT NULL,
    executed_at       TEXT,
    error             TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_signals_nonce            ON signals(nonce);
CREATE UNIQUE INDEX IF NOT EXISTS uq_signals_idempotency      ON signals(idempotency_key);
CREATE INDEX        IF NOT EXISTS ix_signals_status_id        ON signals(status, id);

CREATE TABLE IF NOT EXISTS executions (
    id                BIGSERIAL PRIMARY KEY,
    signal_id         BIGINT NOT NULL REFERENCES signals(id),
    mt5_ticket        TEXT,
    side              TEXT NOT NULL,
    volume            DOUBLE PRECISION NOT NULL,
    symbol            TEXT NOT NULL,
    requested_entry   DOUBLE PRECISION NOT NULL,
    executed_price    DOUBLE PRECISION,
    sl                DOUBLE PRECISION NOT NULL,
    tp                DOUBLE PRECISION NOT NULL,
    status            TEXT NOT NULL,
    mt5_retcode       TEXT,
    error             TEXT,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_executions_signal_id ON executions(signal_id);
"""


class StateStore:
    """Thin sync wrapper around psycopg 3.

    Public API is kept stable so worker.py and webhook.py do not need to
    change. Constructor takes a DATABASE_URL string; the rest of the surface
    (insert_signal, claim_pending_signal, mark_signal_status, has_execution,
    insert_execution) is the same.
    """

    def __init__(self, database_url: str, *, auto_init: bool = True):
        if not database_url or not database_url.strip():
            raise ValueError("database_url_missing")
        self.database_url = database_url
        if auto_init:
            self._init_db()

    @contextmanager
    def conn(self) -> Iterator[psycopg.Connection]:
        connection = psycopg.connect(self.database_url, row_factory=dict_row)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _init_db(self) -> None:
        with self.conn() as con:
            con.execute(SCHEMA_DDL)

    def insert_signal(self, signal: TradingViewSignal) -> tuple[bool, int | None]:
        now = utc_now_iso()
        try:
            with self.conn() as con:
                row = con.execute(
                    """
                    INSERT INTO signals (
                        nonce, idempotency_key, strategy, strategy_version, symbol, timeframe,
                        side, entry, sl, tp, sl_value, tp_value, bar_time_ms, config_key,
                        status, raw_payload, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        'pending', %s::jsonb, %s
                    )
                    RETURNING id
                    """,
                    (
                        signal.nonce,
                        signal.idempotency_key(),
                        signal.strategy,
                        signal.strategy_version,
                        signal.symbol,
                        signal.timeframe,
                        signal.side,
                        signal.entry,
                        signal.sl,
                        signal.tp,
                        signal.sl_value,
                        signal.tp_value,
                        signal.bar_time_ms,
                        signal.config_key,
                        signal.raw_json(),
                        now,
                    ),
                ).fetchone()
                return True, int(row["id"]) if row else None
        except psycopg.errors.UniqueViolation:
            return False, self.find_signal_id_by_nonce_or_key(
                signal.nonce, signal.idempotency_key()
            )

    def find_signal_id_by_nonce_or_key(self, nonce: str, key: str) -> int | None:
        with self.conn() as con:
            row = con.execute(
                "SELECT id FROM signals WHERE nonce = %s OR idempotency_key = %s LIMIT 1",
                (nonce, key),
            ).fetchone()
            return int(row["id"]) if row else None

    def fetch_pending_signal(self) -> dict[str, Any] | None:
        with self.conn() as con:
            row = con.execute(
                "SELECT * FROM signals WHERE status = 'pending' ORDER BY id ASC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    def claim_pending_signal(self) -> dict[str, Any] | None:
        """Atomically claim the oldest pending signal.

        Uses FOR UPDATE SKIP LOCKED so concurrent workers do not collide.
        """
        with self.conn() as con:
            row = con.execute(
                """
                WITH claimed AS (
                    SELECT id FROM signals
                    WHERE status = 'pending'
                    ORDER BY id ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE signals
                SET status = 'processing', error = ''
                WHERE id IN (SELECT id FROM claimed)
                RETURNING *
                """
            ).fetchone()
            return dict(row) if row else None

    def mark_signal_status(self, signal_id: int, status: str, error: str = "") -> None:
        terminal_status = status in {"executed", "failed", "dry_run", "duplicate_ignored"}
        with self.conn() as con:
            if terminal_status:
                con.execute(
                    "UPDATE signals SET status = %s, error = %s, executed_at = %s WHERE id = %s",
                    (status, error, utc_now_iso(), signal_id),
                )
            else:
                con.execute(
                    "UPDATE signals SET status = %s, error = %s WHERE id = %s",
                    (status, error, signal_id),
                )

    def has_execution(self, signal_id: int) -> bool:
        with self.conn() as con:
            row = con.execute(
                "SELECT id FROM executions WHERE signal_id = %s LIMIT 1",
                (signal_id,),
            ).fetchone()
            return row is not None

    def insert_execution(self, result: ExecutionResult) -> None:
        result = result.with_created_at()
        with self.conn() as con:
            con.execute(
                """
                INSERT INTO executions (
                    signal_id, mt5_ticket, side, volume, symbol, requested_entry,
                    executed_price, sl, tp, status, mt5_retcode, error, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    result.signal_id,
                    result.mt5_ticket,
                    result.side,
                    result.volume,
                    result.symbol,
                    result.requested_entry,
                    result.executed_price,
                    result.sl,
                    result.tp,
                    result.status,
                    result.mt5_retcode,
                    result.error,
                    result.created_at,
                ),
            )
