from __future__ import annotations

from logging.config import fileConfig
from pathlib import Path
import sys

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import URL

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

target_metadata = None


def _database_url() -> str:
    from app.settings import settings

    user = str(settings.POSTGRES_USER or "")
    password = str(settings.POSTGRES_PASSWORD or "")
    host = str(settings.POSTGRES_HOST or "127.0.0.1")
    port = int(settings.POSTGRES_PORT or 5432)
    database = str(settings.POSTGRES_DB or "cntxlabserver")
    if host.startswith("/"):
        return URL.create(
            drivername="postgresql+psycopg2",
            username=user or None,
            password=password or None,
            database=database,
            query={"host": host, "port": str(port)},
        ).render_as_string(hide_password=False)
    return URL.create(
        drivername="postgresql+psycopg2",
        username=user or None,
        password=password or None,
        host=host,
        port=port,
        database=database,
    ).render_as_string(hide_password=False)


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
