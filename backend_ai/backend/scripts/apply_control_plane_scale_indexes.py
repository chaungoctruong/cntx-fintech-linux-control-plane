from __future__ import annotations

import sys
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.settings import settings  # noqa: E402
from init_pg_schema import _create_control_plane_scale_indexes  # noqa: E402


ADVISORY_LOCK_KEY = 2_026_042_501


def main() -> int:
    conn = psycopg2.connect(
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        database=settings.POSTGRES_DB,
        application_name="spider-control-plane-scale-indexes",
    )
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
            locked = bool((cur.fetchone() or [False])[0])
            if not locked:
                print("control_plane_scale_indexes_skipped: another index build is running")
                return 2
            try:
                cur.execute("SET lock_timeout = '3s'")
                cur.execute("SET statement_timeout = '5min'")
                _create_control_plane_scale_indexes(cur, concurrently=True)
            finally:
                cur.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))
        print("control_plane_scale_indexes_ok")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
