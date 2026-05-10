-- GsAlgoVIP — PostgreSQL schema for the bot's isolated state DB.
--
-- Owner of this schema is the bot package, not the Linux backend core.
-- The platform should provision a dedicated database (or schema) per bot
-- (or per tenant) and inject the URL via DATABASE_URL.
--
-- Idempotent: safe to run on every startup. The application also runs the
-- equivalent DDL on first connection, so this file is provided for platforms
-- that prefer to pre-create tables before the bot starts.

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

CREATE UNIQUE INDEX IF NOT EXISTS uq_signals_nonce       ON signals(nonce);
CREATE UNIQUE INDEX IF NOT EXISTS uq_signals_idempotency ON signals(idempotency_key);
CREATE INDEX        IF NOT EXISTS ix_signals_status_id   ON signals(status, id);

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
