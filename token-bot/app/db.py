from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def make_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite:///"):
        path = database_url.removeprefix("sqlite:///")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        return create_engine(
            database_url,
            future=True,
            connect_args={"check_same_thread": False},
        )
    return create_engine(database_url, future=True)


def make_session_factory(engine: Engine):
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def ensure_schema_patches(engine: Engine) -> None:
    """Idempotent SQLite ALTER TABLE cho các cột thêm sau khi DB đã tồn tại.

    Tránh phải xoá var/token_bot.db khi schema mở rộng. Chỉ chạy cho SQLite —
    Postgres sẽ dùng Alembic sau này.
    """
    if engine.dialect.name != "sqlite":
        return
    patches = {
        "tokens": [
            ("expiry_notice_sent_at", "DATETIME"),
            ("renewed_to_jti", "VARCHAR(64)"),
            ("locked_at", "DATETIME"),
            ("account_id", "BIGINT"),
            ("force_stop_at", "DATETIME"),
            ("force_stop_attempts", "INTEGER DEFAULT 0"),
            ("force_stop_last_attempt", "DATETIME"),
            ("force_stop_last_error", "VARCHAR(255)"),
            ("issued_by_telegram_id", "BIGINT"),
            ("issued_by_username", "VARCHAR(64)"),
        ],
        "partners": [
            ("billing_anchor_at", "DATETIME"),
        ],
        "partner_billing_notices": [
            ("support_active_users", "INTEGER DEFAULT 0"),
        ],
        "partner_billing_snapshots": [
            ("support_active_users", "INTEGER DEFAULT 0"),
        ],
    }
    with engine.connect() as conn:
        for table, cols in patches.items():
            rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
            existing = {r[1] for r in rows}
            for name, sqltype in cols:
                if name not in existing:
                    conn.exec_driver_sql(
                        f"ALTER TABLE {table} ADD COLUMN {name} {sqltype}"
                    )
        conn.commit()
