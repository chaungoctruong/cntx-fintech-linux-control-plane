from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, TypeVar
from contextlib import contextmanager

from app.settings import settings

T = TypeVar("T")
log = logging.getLogger("store")

SECURITY_CRITICAL_AUDIT_ACTIONS = (
    "account.connect",
    "account.login_slot.requested",
    "user.delete",
    "account.circuit_breaker.trigger",
    "account.risk_policy.update",
)
_AI_LOG_REDACTED = "[redacted_sensitive]"
_AI_LOG_SENSITIVE_RE = re.compile(
    r"(password|passwd|pwd|token|secret|api\s*key|private\s*key|authorization|bearer|mat\s*khau|mk|otp|2fa|redis://|postgres://|postgresql://)",
    re.IGNORECASE,
)


def _now() -> int:
    return int(time.time())

def _safe_json_dumps(payload: Optional[Dict[str, Any]]) -> Optional[str]:
    if payload is None: return None
    try:
        import json
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except Exception: return None


def _redact_ai_log_text(value: Any) -> str:
    text = str(value or "").strip()
    if _AI_LOG_SENSITIVE_RE.search(text):
        return _AI_LOG_REDACTED
    return text


def _compute_audit_retention_delete_ids(
    rows: list[Dict[str, Any]],
    *,
    retention_count: int,
    exclude_actions: list[str] | tuple[str, ...],
) -> set[int]:
    retention = max(0, int(retention_count))
    excluded = {str(action or "").strip() for action in (exclude_actions or []) if str(action or "").strip()}
    grouped: dict[str, list[Dict[str, Any]]] = {}
    for row in rows:
        action = str(row.get("action") or "").strip()
        if action in excluded:
            continue
        user_key = str(row.get("user_id") or row.get("telegram_id") or "anonymous").strip()
        grouped.setdefault(user_key, []).append(row)

    delete_ids: set[int] = set()
    for user_rows in grouped.values():
        ordered = sorted(
            user_rows,
            key=lambda item: (int(item.get("created_at") or 0), int(item.get("id") or 0)),
            reverse=True,
        )
        for row in ordered[retention:]:
            if row.get("id") is not None:
                delete_ids.add(int(row["id"]))
    return delete_ids

class Store:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.mode = settings.DB_MODE.lower()

        if self.mode != "postgres":
            raise RuntimeError(
                "\033[91m[CRITICAL ERROR] SQLite fallback has been disabled for SaaS safety. "
                "Please run PostgreSQL via Docker and set DB_MODE=postgres.\033[0m"
            )

        from psycopg2 import pool

        try:
            minconn = max(1, int(getattr(settings, "POSTGRES_POOL_MIN", 5)))
            maxconn = max(minconn, int(getattr(settings, "POSTGRES_POOL_MAX", 50)))
            self.pg_pool = pool.ThreadedConnectionPool(
                minconn=minconn,
                maxconn=maxconn,
                user=settings.POSTGRES_USER,
                password=settings.POSTGRES_PASSWORD,
                host=settings.POSTGRES_HOST,
                port=settings.POSTGRES_PORT,
                database=settings.POSTGRES_DB
            )
            conn = self.pg_pool.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
            finally:
                self.pg_pool.putconn(conn)
            self._closed = False
        except Exception as exc:
            raise RuntimeError(
                f"\033[91m[CRITICAL ERROR] Cannot connect PostgreSQL at "
                f"{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}. "
                f"Please start Docker PostgreSQL before running backend. "
                f"Root cause: {exc}\033[0m"
            ) from exc

    def close(self) -> None:
        pg_pool = getattr(self, "pg_pool", None)
        if pg_pool is None or bool(getattr(self, "_closed", False)):
            return
        try:
            pg_pool.closeall()
        finally:
            self._closed = True
            self.pg_pool = None

    def closeall(self) -> None:
        self.close()

    @contextmanager
    def _get_connection(self):
        pg_pool = getattr(self, "pg_pool", None)
        if pg_pool is None or bool(getattr(self, "_closed", False)):
            raise RuntimeError("postgres_pool_closed")
        conn = pg_pool.getconn()
        try:
            yield conn
        finally:
            pg_pool.putconn(conn)

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s")

    def _with_retry_locked(self, fn: Callable[[Any, Any], T], *, tries: int = 5) -> T:
        last_err: Optional[Exception] = None
        for i in range(tries):
            with self._get_connection() as con:
                try:
                    from psycopg2.extras import RealDictCursor
                    cur = con.cursor(cursor_factory=RealDictCursor)
                    cur.execute("SET TRANSACTION READ WRITE")
                    res = fn(con, cur)
                    con.commit()
                    cur.close()
                    return res
                except Exception as e:
                    last_err = e
                    con.rollback()
                    if i < tries - 1:
                        time.sleep(0.1 * (i + 1))
                        continue
                    raise e
        raise last_err or RuntimeError("db retry failed")

    def _with_retry_read(self, fn: Callable[[Any, Any], T], *, tries: int = 3) -> T:
        last_err: Optional[Exception] = None
        for i in range(tries):
            with self._get_connection() as con:
                try:
                    from psycopg2.extras import RealDictCursor
                    cur = con.cursor(cursor_factory=RealDictCursor)
                    res = fn(con, cur)
                    cur.close()
                    con.rollback()
                    return res
                except Exception as e:
                    last_err = e
                    con.rollback()
                    if i < tries - 1:
                        time.sleep(0.05 * (i + 1))
                        continue
                    raise
        raise last_err or RuntimeError("db read retry failed")

    def init(self) -> None:
        return

    def add_audit(self, telegram_id: str, action: str, payload: Optional[Dict[str, Any]], result: str) -> None:
        now = _now()
        payload_json = _safe_json_dumps(payload)
        def _do(con: Any, cur: Any) -> None:
            cur.execute(self._sql("INSERT INTO audit_logs(telegram_id, action, payload_json, result, created_at) VALUES(?,?,?,?,?)"), (telegram_id, action, payload_json, result, now))
        self._with_retry_locked(_do)

    def list_audit(self, telegram_id: str, limit: int = 50) -> list[Dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        def _do(con: Any, cur: Any) -> list[Dict[str, Any]]:
            cur.execute(self._sql("SELECT id, telegram_id, action, payload_json, result, created_at FROM audit_logs WHERE telegram_id=? ORDER BY id DESC LIMIT ?"), (telegram_id, limit))
            return [dict(r) for r in cur.fetchall()]
        return self._with_retry_read(_do)

    def delete_audit_logs_batch_older_than(self, cutoff_ts: int, batch_size: int = 1000) -> int:
        """Zero-lock cleanup: xóa tối đa batch_size dòng audit_logs có created_at < cutoff_ts. Trả về số dòng đã xóa."""
        batch_size = max(1, min(int(batch_size), 5000))
        def _do(con: Any, cur: Any) -> int:
            cur.execute(self._sql("SELECT id FROM audit_logs WHERE created_at < ? ORDER BY id ASC LIMIT ?"), (cutoff_ts, batch_size))
            rows = cur.fetchall()
            ids = [r["id"] for r in rows if r and r.get("id") is not None]
            if not ids:
                return 0
            cur.execute(self._sql("DELETE FROM audit_logs WHERE id = ANY(?)"), (ids,))
            return int(cur.rowcount or 0)
        return self._with_retry_locked(_do)

    def prune_audit_logs_keep_last_n_per_user(
        self,
        retention_count: int,
        exclude_actions: list[str] | tuple[str, ...],
        *,
        dry_run: bool = False,
    ) -> dict[str, int | bool]:
        retention = max(0, int(retention_count))
        excluded = [str(action or "").strip() for action in (exclude_actions or []) if str(action or "").strip()]

        if dry_run:
            def _do_read(con: Any, cur: Any) -> dict[str, int | bool]:
                cur.execute(
                    """
                    WITH ranked AS (
                        SELECT
                            id,
                            COALESCE(user_id::TEXT, NULLIF(telegram_id, ''), 'anonymous') AS user_key,
                            ROW_NUMBER() OVER (
                                PARTITION BY COALESCE(user_id::TEXT, NULLIF(telegram_id, ''), 'anonymous')
                                ORDER BY created_at DESC, id DESC
                            ) AS rn
                        FROM audit_logs
                        WHERE action <> ALL(%s::TEXT[])
                    ),
                    victims AS (
                        SELECT id, user_key
                        FROM ranked
                        WHERE rn > %s
                    )
                    SELECT
                        (SELECT COUNT(*)::INT FROM victims) AS deleted_count,
                        (SELECT COUNT(DISTINCT user_key)::INT FROM ranked) AS scanned_users
                    """,
                    (excluded, retention),
                )
                row = dict(cur.fetchone() or {})
                return {
                    "deleted_count": int(row.get("deleted_count") or 0),
                    "scanned_users": int(row.get("scanned_users") or 0),
                    "dry_run": True,
                }

            return self._with_retry_read(_do_read)

        def _do(con: Any, cur: Any) -> dict[str, int | bool]:
            cur.execute(
                """
                WITH ranked AS (
                    SELECT
                        id,
                        COALESCE(user_id::TEXT, NULLIF(telegram_id, ''), 'anonymous') AS user_key,
                        ROW_NUMBER() OVER (
                            PARTITION BY COALESCE(user_id::TEXT, NULLIF(telegram_id, ''), 'anonymous')
                            ORDER BY created_at DESC, id DESC
                        ) AS rn
                    FROM audit_logs
                    WHERE action <> ALL(%s::TEXT[])
                ),
                victims AS (
                    SELECT id, user_key
                    FROM ranked
                    WHERE rn > %s
                ),
                deleted AS (
                    DELETE FROM audit_logs a
                    USING victims v
                    WHERE a.id = v.id
                    RETURNING a.id
                )
                SELECT
                    (SELECT COUNT(*)::INT FROM deleted) AS deleted_count,
                    (SELECT COUNT(DISTINCT user_key)::INT FROM ranked) AS scanned_users
                """,
                (excluded, retention),
            )
            row = dict(cur.fetchone() or {})
            return {
                "deleted_count": int(row.get("deleted_count") or 0),
                "scanned_users": int(row.get("scanned_users") or 0),
                "dry_run": False,
            }

        return self._with_retry_locked(_do)

    def save_ai_log(self, user_id: str, question: str, answer: str, status: str = "PENDING_REVIEW") -> None:
        now = _now()
        safe_question = _redact_ai_log_text(question)
        safe_answer = _redact_ai_log_text(answer)
        def _do(con: Any, cur: Any) -> None:
            cur.execute("""
                INSERT INTO ai_logs(user_id, question, answer, status, created_at)
                VALUES(%s,%s,%s,%s,%s)
            """, (str(user_id), safe_question, safe_answer, str(status), now))
        try:
            self._with_retry_locked(_do)
        except Exception as exc:
            log.warning("Graceful Degradation: skip ai_logs insert due to DB error: %s", exc)

    def upsert_bot_catalog(
        self,
        *,
        bot_code: str,
        bot_name: str,
        strategy: Optional[str] = None,
        tags: Optional[list[str]] = None,
        enabled: bool = True,
        status: Optional[str] = None,
        superseded_by: Optional[str] = None,
    ) -> None:
        now = _now()
        code = str(bot_code or "").strip()
        name = str(bot_name or "").strip()
        if not code or not name:
            raise ValueError("invalid_bot_catalog_payload")
        strategy_s = str(strategy or "").strip() or None
        tags_json = _safe_json_dumps({"tags": [str(t).strip() for t in (tags or []) if str(t).strip()]})
        status_s = (str(status or "").strip().upper() or "ACTIVE") if status is not None else ("ACTIVE" if enabled else "RETIRED")
        if status_s not in ("ACTIVE", "DEPRECATED", "RETIRED"):
            status_s = "ACTIVE"
        superseded_s = str(superseded_by or "").strip() or None if superseded_by is not None else None

        def _do(con: Any, cur: Any) -> None:
            cur.execute(
                """
                INSERT INTO bot_catalog(bot_code, bot_name, strategy, tags, enabled, status, superseded_by, created_at, updated_at)
                VALUES(%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s)
                ON CONFLICT(bot_code) DO UPDATE SET
                    bot_name=EXCLUDED.bot_name,
                    strategy=EXCLUDED.strategy,
                    tags=EXCLUDED.tags,
                    enabled=EXCLUDED.enabled,
                    status=EXCLUDED.status,
                    superseded_by=COALESCE(EXCLUDED.superseded_by, bot_catalog.superseded_by),
                    updated_at=EXCLUDED.updated_at
                """,
                (code, name, strategy_s, tags_json or "{\"tags\":[]}", bool(enabled), status_s, superseded_s, now, now),
            )

        self._with_retry_locked(_do)

    def set_bot_catalog_retired_except(self, active_bot_codes: list[str]) -> None:
        """Lifecycle: set status=RETIRED cho mọi bot_code trong DB không nằm trong active_bot_codes hiện còn được hỗ trợ."""
        if not isinstance(active_bot_codes, (list, tuple)):
            return
        codes_set = {str(c).strip() for c in active_bot_codes if str(c).strip()}
        now = _now()
        placeholders = ",".join(["%s"] * len(codes_set)) if codes_set else "NULL"

        def _do(con: Any, cur: Any) -> None:
            if codes_set:
                cur.execute(
                    f"""
                    UPDATE bot_catalog
                    SET enabled=FALSE, status='RETIRED', updated_at=%s
                    WHERE bot_code IS NOT NULL AND bot_code != ''
                      AND bot_code NOT IN ({placeholders})
                    """,
                    (now, *codes_set),
                )
            else:
                cur.execute(
                    """
                    UPDATE bot_catalog
                    SET enabled=FALSE, status='RETIRED', updated_at=%s
                    WHERE bot_code IS NOT NULL AND bot_code != ''
                    """,
                    (now,),
                )

        self._with_retry_locked(_do)

    def set_bot_catalog_inactive_except(self, active_bot_codes: list[str]) -> None:
        """Soft-delete (V2 compat): set enabled=FALSE, status=RETIRED cho mọi bot_code không trong list."""
        self.set_bot_catalog_retired_except(active_bot_codes)

    def get_bot_catalog_row(self, bot_code: str) -> Optional[Dict[str, Any]]:
        """Lấy 1 dòng bot_catalog (để kiểm tra status, superseded_by cho Graceful UX)."""
        code_s = str(bot_code or "").strip()
        if not code_s:
            return None
        def _do(con: Any, cur: Any) -> Optional[Dict[str, Any]]:
            cur.execute(
                "SELECT bot_code, bot_name, strategy, tags, enabled, status, superseded_by FROM bot_catalog WHERE bot_code=%s",
                (code_s,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
        return self._with_retry_read(_do)

    def list_bot_catalog(self, *, only_enabled: bool = True, status_filter: Optional[str] = None) -> list[Dict[str, Any]]:
        """status_filter: 'ACTIVE' = chỉ catalog (user mới); None = tất cả."""
        def _do(con: Any, cur: Any) -> list[Dict[str, Any]]:
            if status_filter == "ACTIVE":
                cur.execute("SELECT bot_code, bot_name, strategy, tags, enabled, status, superseded_by FROM bot_catalog WHERE status=%s ORDER BY bot_code ASC", ("ACTIVE",))
            elif only_enabled:
                cur.execute("SELECT bot_code, bot_name, strategy, tags, enabled, status, superseded_by FROM bot_catalog WHERE enabled=TRUE ORDER BY bot_code ASC")
            else:
                cur.execute("SELECT bot_code, bot_name, strategy, tags, enabled, status, superseded_by FROM bot_catalog ORDER BY bot_code ASC")
            rows = [dict(r) for r in cur.fetchall()]
            out: list[Dict[str, Any]] = []
            for row in rows:
                tags_raw = row.get("tags")
                tags: list[str] = []
                if isinstance(tags_raw, dict):
                    vals = tags_raw.get("tags")
                    if isinstance(vals, list):
                        tags = [str(v) for v in vals]
                elif isinstance(tags_raw, str):
                    try:
                        parsed = json.loads(tags_raw)
                        vals = parsed.get("tags") if isinstance(parsed, dict) else []
                        if isinstance(vals, list):
                            tags = [str(v) for v in vals]
                    except Exception:
                        tags = []
                out.append(
                    {
                        "code": str(row.get("bot_code") or "").strip(),
                        "name": str(row.get("bot_name") or "").strip(),
                        "strategy": str(row.get("strategy") or "").strip(),
                        "tags": tags,
                        "enabled": bool(row.get("enabled")),
                        "status": str(row.get("status") or "ACTIVE").strip().upper(),
                        "superseded_by": str(row.get("superseded_by") or "").strip() or None,
                    }
                )
            return out

        try:
            return self._with_retry_read(_do)
        except Exception:
            raise

    # --- External WEB B connect (durable handshake; Redis remains short-lived cache) ---

    def insert_external_connect_handshake(
        self,
        *,
        session_id: str,
        telegram_id: str,
        state_secret: str,
        created_at: int,
    ) -> None:
        """Persist new pending handshake (paired with Redis session)."""
        sid = str(session_id or "").strip()
        tg = str(telegram_id or "").strip()
        st = str(state_secret or "").strip()
        if not sid or not tg or not st:
            raise ValueError("session_id, telegram_id, state_secret required")

        def _do(con: Any, cur: Any) -> None:
            cur.execute(
                """
                INSERT INTO external_connect_handshakes(
                    session_id, telegram_id, state_secret, status,
                    broker_account_id, broker_metadata, created_at, updated_at
                )
                VALUES (%s, %s, %s, 'pending', NULL, NULL, %s, %s)
                """,
                (sid, tg, st, int(created_at), int(created_at)),
            )

        self._with_retry_locked(_do)

    def get_external_connect_handshake(self, session_id: str) -> Optional[Dict[str, Any]]:
        sid = str(session_id or "").strip()
        if not sid:
            return None

        def _do(con: Any, cur: Any) -> Optional[Dict[str, Any]]:
            cur.execute(
                """
                SELECT session_id, telegram_id, state_secret, status,
                       broker_account_id, broker_metadata, created_at, updated_at
                FROM external_connect_handshakes WHERE session_id=%s
                """,
                (sid,),
            )
            row = cur.fetchone()
            return dict(row) if row else None

        return self._with_retry_read(_do)

    def update_external_connect_handshake(
        self,
        *,
        session_id: str,
        status: str,
        broker_account_id: Optional[str] = None,
        broker_metadata: Optional[Dict[str, Any]] = None,
        updated_at: int,
    ) -> int:
        """Update handshake row. Returns rowcount."""
        sid = str(session_id or "").strip()
        st = str(status or "").strip().lower()
        if st not in ("pending", "connected", "failed"):
            st = "pending"
        now = int(updated_at)
        ba = (str(broker_account_id).strip() or None) if broker_account_id is not None else None
        meta_json = broker_metadata if isinstance(broker_metadata, dict) else None

        def _do(con: Any, cur: Any) -> int:
            cur.execute(
                """
                UPDATE external_connect_handshakes
                SET status=%s,
                    broker_account_id=%s,
                    broker_metadata=%s,
                    updated_at=%s
                WHERE session_id=%s
                """,
                (st, ba, json.dumps(meta_json) if meta_json is not None else None, now, sid),
            )
            return int(cur.rowcount or 0)

        return self._with_retry_locked(_do)

    def list_external_connect_handshakes_for_user(
        self, telegram_id: str, *, limit: int = 10
    ) -> list[Dict[str, Any]]:
        tg = str(telegram_id or "").strip()
        if not tg:
            return []
        lim = max(1, min(50, int(limit)))

        def _do(con: Any, cur: Any) -> list[Dict[str, Any]]:
            cur.execute(
                """
                SELECT session_id, telegram_id, state_secret, status,
                       broker_account_id, broker_metadata, created_at, updated_at
                FROM external_connect_handshakes
                WHERE telegram_id=%s
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (tg, lim),
            )
            rows = cur.fetchall() or []
            return [dict(r) for r in rows]

        return self._with_retry_read(_do)
