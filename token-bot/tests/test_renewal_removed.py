from pathlib import Path

from app.backend_client import BackendClient
from app.db import ensure_schema_patches, initialize_schema, make_engine
from app.models import Token


class _Dialect:
    name = "postgresql"


class _Connection:
    def __init__(self):
        self.statements = []
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def exec_driver_sql(self, sql, parameters=None):
        self.statements.append((sql, parameters))

    def commit(self):
        self.committed = True


class _Engine:
    dialect = _Dialect()

    def __init__(self):
        self.conn = _Connection()

    def connect(self):
        return self.conn

    def begin(self):
        return self.conn


class _Metadata:
    def __init__(self):
        self.calls = []

    def create_all(self, conn):
        conn.statements.append(("metadata.create_all", None))
        self.calls.append(conn)


def test_token_bot_db_rejects_non_postgres_urls():
    try:
        make_engine("duckdb:///tmp/non_product.db")
    except RuntimeError as exc:
        assert "PostgreSQL" in str(exc)
    else:
        raise AssertionError("token-bot accepted a non-PostgreSQL DATABASE_URL")


def test_token_bot_db_accepts_postgres_urls_without_connecting():
    engine = make_engine("postgres://user:pass@localhost:5432/appdb")
    try:
        assert engine.dialect.name == "postgresql"
        assert engine.url.drivername == "postgresql+psycopg2"
    finally:
        engine.dispose()


def test_postgres_schema_patch_removes_legacy_renewal_column_and_keeps_runtime_columns():
    engine = _Engine()

    ensure_schema_patches(engine)

    statements = "\n".join(sql for sql, _ in engine.conn.statements)
    assert 'DROP COLUMN IF EXISTS "renewed_to_jti"' in statements
    assert 'ADD COLUMN IF NOT EXISTS "locked_at"' in statements
    assert 'ADD COLUMN IF NOT EXISTS "force_stop_attempts" INTEGER NOT NULL DEFAULT 0' in statements
    assert 'CREATE INDEX IF NOT EXISTS "ix_tokens_force_stop_at"' in statements
    assert "PRAGMA" not in statements
    assert engine.conn.committed


def test_schema_initialize_serializes_create_all_with_postgres_advisory_lock():
    engine = _Engine()
    metadata = _Metadata()

    initialize_schema(engine, metadata)

    statements = [sql for sql, _ in engine.conn.statements]
    assert statements[0] == "SELECT pg_advisory_xact_lock(%s)"
    assert statements[1] == "metadata.create_all"
    assert any('DROP COLUMN IF EXISTS "renewed_to_jti"' in sql for sql in statements)
    assert metadata.calls == [engine.conn]


def test_renewal_runtime_api_is_removed():
    assert "renewed_to_jti" not in Token.__table__.columns
    assert not hasattr(BackendClient, "transfer_link")


def test_no_renewal_callbacks_or_routes_remain():
    repo = Path(__file__).resolve().parents[2]
    paths = [
        repo / "token-bot" / "app" / "tg_bot.py",
        repo / "token-bot" / "app" / "backend_client.py",
        repo / "backend_ai" / "backend" / "app" / "partner_users" / "routes.py",
        repo / "backend_ai" / "backend" / "app" / "partner_users" / "service.py",
    ]
    forbidden = [
        "pmenu:renew",
        "renew_t:",
        "renew_d:",
        "cb_renew",
        "_show_renew",
        "transfer-link",
        "transfer_link",
        "backend-product:partner:{partner.id}:renew",
    ]

    for path in paths:
        source = path.read_text(encoding="utf-8")
        for marker in forbidden:
            assert marker not in source, f"{marker} still present in {path}"
