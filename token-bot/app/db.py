from sqlalchemy import create_engine
from sqlalchemy.exc import ArgumentError
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session, sessionmaker


_SCHEMA_ADVISORY_LOCK_ID = 510_809_202_605_210


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _normalize_database_url(database_url: str) -> str:
    raw_url = str(database_url or "").strip()
    if not raw_url:
        raise RuntimeError("token-bot requires PostgreSQL DATABASE_URL")
    if raw_url.startswith("postgres://"):
        raw_url = "postgresql+psycopg2://" + raw_url.removeprefix("postgres://")

    try:
        url = make_url(raw_url)
    except ArgumentError as exc:
        raise RuntimeError("token-bot requires PostgreSQL DATABASE_URL") from exc
    if url.get_backend_name() != "postgresql":
        raise RuntimeError("token-bot requires PostgreSQL DATABASE_URL")
    return raw_url


def make_engine(database_url: str) -> Engine:
    return create_engine(
        _normalize_database_url(database_url),
        future=True,
        pool_pre_ping=True,
    )


def make_session_factory(engine: Engine):
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def _drop_column_if_exists(conn, table: str, column: str) -> None:
    conn.exec_driver_sql(
        f"ALTER TABLE {_quote_identifier(table)} DROP COLUMN IF EXISTS {_quote_identifier(column)}"
    )


def _add_column_if_missing(conn, table: str, column: str, definition: str) -> None:
    conn.exec_driver_sql(
        f"ALTER TABLE {_quote_identifier(table)} "
        f"ADD COLUMN IF NOT EXISTS {_quote_identifier(column)} {definition}"
    )


def _create_index_if_missing(conn, name: str, table: str, columns: list[str]) -> None:
    cols_sql = ", ".join(_quote_identifier(column) for column in columns)
    conn.exec_driver_sql(
        f"CREATE INDEX IF NOT EXISTS {_quote_identifier(name)} "
        f"ON {_quote_identifier(table)} ({cols_sql})"
    )


def _apply_schema_patches(conn) -> None:
    patches = {
        "tokens": [
            ("expiry_notice_sent_at", "TIMESTAMP WITHOUT TIME ZONE"),
            ("locked_at", "TIMESTAMP WITHOUT TIME ZONE"),
            ("account_id", "BIGINT"),
            ("force_stop_at", "TIMESTAMP WITHOUT TIME ZONE"),
            ("force_stop_attempts", "INTEGER NOT NULL DEFAULT 0"),
            ("force_stop_last_attempt", "TIMESTAMP WITHOUT TIME ZONE"),
            ("force_stop_last_error", "VARCHAR(255)"),
            ("issued_by_telegram_id", "BIGINT"),
            ("issued_by_username", "VARCHAR(64)"),
        ],
        "partners": [
            ("billing_anchor_at", "TIMESTAMP WITHOUT TIME ZONE"),
        ],
        "partner_billing_notices": [
            ("support_active_users", "INTEGER NOT NULL DEFAULT 0"),
        ],
        "partner_billing_snapshots": [
            ("support_active_users", "INTEGER NOT NULL DEFAULT 0"),
        ],
    }
    indexes = [
        ("ix_partners_telegram_user_id", "partners", ["telegram_user_id"]),
        ("ix_partner_members_partner_id", "partner_members", ["partner_id"]),
        ("ix_partner_members_telegram_user_id", "partner_members", ["telegram_user_id"]),
        ("ix_partner_members_role", "partner_members", ["role"]),
        ("ix_partner_members_active", "partner_members", ["active"]),
        ("ix_partner_bot_grants_partner_id", "partner_bot_grants", ["partner_id"]),
        ("ix_partner_bot_grants_bot_id", "partner_bot_grants", ["bot_id"]),
        ("ix_tokens_partner_id", "tokens", ["partner_id"]),
        ("ix_tokens_end_user_telegram_id", "tokens", ["end_user_telegram_id"]),
        ("ix_tokens_issued_by_telegram_id", "tokens", ["issued_by_telegram_id"]),
        ("ix_tokens_locked_at", "tokens", ["locked_at"]),
        ("ix_tokens_account_id", "tokens", ["account_id"]),
        ("ix_tokens_force_stop_at", "tokens", ["force_stop_at"]),
        ("ix_partner_billing_notices_partner_id", "partner_billing_notices", ["partner_id"]),
        ("ix_partner_billing_notices_billing_month", "partner_billing_notices", ["billing_month"]),
        ("ix_partner_billing_notices_week_key", "partner_billing_notices", ["week_key"]),
        ("ix_partner_payment_proofs_partner_id", "partner_payment_proofs", ["partner_id"]),
        ("ix_partner_payment_proofs_billing_month", "partner_payment_proofs", ["billing_month"]),
        ("ix_partner_payment_proofs_week_key", "partner_payment_proofs", ["week_key"]),
        ("ix_partner_payment_proofs_status", "partner_payment_proofs", ["status"]),
        ("ix_partner_billing_snapshots_partner_id", "partner_billing_snapshots", ["partner_id"]),
        ("ix_partner_billing_snapshots_payment_proof_id", "partner_billing_snapshots", ["payment_proof_id"]),
        ("ix_partner_billing_snapshots_billing_period_key", "partner_billing_snapshots", ["billing_period_key"]),
        ("ix_partner_billing_snapshots_week_key", "partner_billing_snapshots", ["week_key"]),
    ]

    _drop_column_if_exists(conn, "tokens", "renewed_to_jti")
    for table, columns in patches.items():
        for column, definition in columns:
            _add_column_if_missing(conn, table, column, definition)
    for name, table, columns in indexes:
        _create_index_if_missing(conn, name, table, columns)


def _ensure_postgres_engine(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        raise RuntimeError("token-bot schema patches require PostgreSQL")


def ensure_schema_patches(engine: Engine) -> None:
    """PostgreSQL-only idempotent patches for already-provisioned token-bot DBs."""
    _ensure_postgres_engine(engine)
    with engine.connect() as conn:
        _apply_schema_patches(conn)
        conn.commit()


def initialize_schema(engine: Engine, metadata) -> None:
    """Create/patch schema under a PostgreSQL advisory lock.

    token-bot-api and token-bot-tg start together in Compose. Without a shared
    DB lock, concurrent ``create_all`` calls can race while PostgreSQL creates
    table metadata types.
    """
    _ensure_postgres_engine(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "SELECT pg_advisory_xact_lock(%s)",
            (_SCHEMA_ADVISORY_LOCK_ID,),
        )
        metadata.create_all(conn)
        _apply_schema_patches(conn)
