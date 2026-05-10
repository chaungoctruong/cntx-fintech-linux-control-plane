from __future__ import annotations

from typing import Any, Optional

from app.repositories.control_plane.query_loader import load_sql
from app.repositories.control_plane.support import (
    _COMMAND_DELIVERY_REPLAY_ADVISORY_LOCK_ID,
    _TERMINAL_DEPLOYMENT_STATUSES,
    _json_payload,
    _norm,
    _norm_slot_id,
    _safe_int,
)


class ControlPlaneCommandsMixin:
    _SQL_CLAIM_NEXT_EXECUTION_COMMAND_FOR_RUNNER = load_sql("commands/claim_next_execution_command_for_runner.sql")
    _SQL_COUNT_COMMAND_DELIVERY_REPLAY_BACKLOG_BASE = load_sql("commands/count_command_delivery_replay_backlog_base.sql")
    _SQL_CREATE_EXECUTION_COMMAND = load_sql("commands/create_execution_command.sql")
    _SQL_FAIL_PENDING_START_COMMANDS_FOR_DEPLOYMENT = load_sql("commands/fail_pending_start_commands_for_deployment.sql")
    _SQL_GET_EXECUTION_COMMAND = load_sql("commands/get_execution_command.sql")
    _SQL_GET_EXECUTION_COMMAND_BY_TRACE_IDENTITY = load_sql("commands/get_execution_command_by_trace_identity.sql")
    _SQL_GET_PENDING_ACCOUNT_START_STOP_COMMAND = load_sql("commands/get_pending_account_start_stop_command.sql")
    _SQL_GET_RECENT_BOT_CONTROL_COMMAND_FOR_USER = load_sql("commands/get_recent_bot_control_command_for_user.sql")
    _SQL_INSERT_EXECUTION_EVENT = load_sql("commands/insert_execution_event.sql")
    _SQL_INSERT_RUNTIME_LOG = load_sql("commands/insert_runtime_log.sql")
    _SQL_LIST_EXECUTION_AUDIT = load_sql("commands/list_execution_audit.sql")
    _SQL_LIST_EXECUTION_COMMANDS = load_sql("commands/list_execution_commands.sql")
    _SQL_LIST_EXECUTION_EVENTS = load_sql("commands/list_execution_events.sql")
    _SQL_LIST_REPLAYABLE_EXECUTION_COMMANDS_BASE = load_sql("commands/list_replayable_execution_commands_base.sql")
    _SQL_LIST_RUNTIME_LOGS = load_sql("commands/list_runtime_logs.sql")
    _SQL_LIST_STALE_PROCESSING_EXECUTION_COMMANDS = load_sql("commands/list_stale_processing_execution_commands.sql")
    _SQL_LIST_STALE_QUEUED_START_COMMANDS = load_sql("commands/list_stale_queued_start_commands.sql")
    _SQL_MARK_COMMAND_DELIVERY = load_sql("commands/mark_command_delivery.sql")
    _SQL_MARK_COMMAND_PROCESSING_REQUEUED = load_sql("commands/mark_command_processing_requeued.sql")
    _SQL_MARK_COMMAND_REPLAY_FAILURE = load_sql("commands/mark_command_replay_failure.sql")
    _SQL_RECONCILE_TERMINAL_BOT_CONTROL_COMMANDS = load_sql("commands/reconcile_terminal_bot_control_commands.sql")
    _SQL_REQUEUE_STALE_HTTP_CLAIMED_EXECUTION_COMMANDS = load_sql(
        "commands/requeue_stale_http_claimed_execution_commands.sql"
    )
    _SQL_UPDATE_EXECUTION_COMMAND_DELIVERY = load_sql("commands/update_execution_command_delivery.sql")
    _SQL_UPSERT_EXECUTION_AUDIT = load_sql("commands/upsert_execution_audit.sql")

    def get_pending_account_start_stop_command(self, *, account_id: int) -> Optional[dict[str, Any]]:
        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                self._SQL_GET_PENDING_ACCOUNT_START_STOP_COMMAND,
                (int(account_id), list(_TERMINAL_DEPLOYMENT_STATUSES)),
            )
            row = cur.fetchone()
            return dict(row) if row else None

        return self._store._with_retry_read(_do)

    def get_recent_bot_control_command_for_user(self, *, user_id: int, cooldown_sec: int) -> Optional[dict[str, Any]]:
        cooldown_i = max(0, int(cooldown_sec or 0))
        if cooldown_i <= 0:
            return None

        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                self._SQL_GET_RECENT_BOT_CONTROL_COMMAND_FOR_USER,
                (cooldown_i, int(user_id), cooldown_i),
            )
            row = cur.fetchone()
            return dict(row) if row else None

        return self._store._with_retry_read(_do)

    def list_execution_commands(self, *, deployment_id: int, user_id: int, limit: int = 100) -> list[dict[str, Any]]:
        limit_i = max(1, min(int(limit), 500))

        def _do(con: Any, cur: Any) -> list[dict[str, Any]]:
            cur.execute(
                self._SQL_LIST_EXECUTION_COMMANDS,
                (int(deployment_id), int(user_id), limit_i),
            )
            return [dict(row) for row in (cur.fetchall() or [])]

        return self._store._with_retry_read(_do)

    def get_execution_command(self, *, command_id: str) -> Optional[dict[str, Any]]:
        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                self._SQL_GET_EXECUTION_COMMAND,
                (_norm(command_id),),
            )
            row = cur.fetchone()
            return dict(row) if row else None

        return self._store._with_retry_read(_do)

    def get_execution_command_by_trace_identity(
        self,
        *,
        account_id: int,
        deployment_id: int,
        command_type: str,
        trace_id: str,
    ) -> Optional[dict[str, Any]]:
        trace_s = _norm(trace_id)
        if not trace_s:
            return None

        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                self._SQL_GET_EXECUTION_COMMAND_BY_TRACE_IDENTITY,
                (int(account_id), int(deployment_id), _norm(command_type), trace_s),
            )
            row = cur.fetchone()
            return dict(row) if row else None

        return self._store._with_retry_read(_do)

    def list_execution_events(self, *, deployment_id: int, user_id: int, limit: int = 200) -> list[dict[str, Any]]:
        limit_i = max(1, min(int(limit), 1000))

        def _do(con: Any, cur: Any) -> list[dict[str, Any]]:
            cur.execute(
                self._SQL_LIST_EXECUTION_EVENTS,
                (int(deployment_id), int(user_id), limit_i),
            )
            return [dict(row) for row in (cur.fetchall() or [])]

        return self._store._with_retry_read(_do)

    def list_runtime_logs(self, *, deployment_id: int, user_id: int, limit: int = 200) -> list[dict[str, Any]]:
        limit_i = max(1, min(int(limit), 1000))

        def _do(con: Any, cur: Any) -> list[dict[str, Any]]:
            cur.execute(
                self._SQL_LIST_RUNTIME_LOGS,
                (int(deployment_id), int(user_id), limit_i),
            )
            return [dict(row) for row in (cur.fetchall() or [])]

        return self._store._with_retry_read(_do)

    def create_execution_command(
        self,
        *,
        command_id: str,
        command_type: str,
        account_id: int,
        deployment_id: int,
        bot_id: str,
        runner_id: str,
        slot_id: str,
        priority: int,
        payload: dict[str, Any],
        trace_id: str,
        queue_name: str,
    ) -> dict[str, Any]:
        existing = self.get_execution_command_by_trace_identity(
            account_id=account_id,
            deployment_id=deployment_id,
            command_type=command_type,
            trace_id=trace_id,
        )
        if existing:
            return existing

        def _do(con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                self._SQL_CREATE_EXECUTION_COMMAND,
                (
                    _norm(command_id),
                    _norm(command_type),
                    int(account_id),
                    int(deployment_id),
                    _norm(bot_id),
                    _norm(runner_id),
                    _norm_slot_id(slot_id),
                    int(priority),
                    _json_payload(payload),
                    _norm(queue_name),
                    _norm(trace_id),
                ),
            )
            return dict(cur.fetchone() or {})

        return self._store._with_retry_locked(_do)

    def mark_command_delivery(self, *, command_id: str, status: str, redis_stream_id: Optional[str] = None) -> None:
        def _do(con: Any, cur: Any) -> None:
            cur.execute(
                self._SQL_MARK_COMMAND_DELIVERY,
                (_norm(status), _norm(redis_stream_id) or None, _norm(command_id)),
            )

        self._store._with_retry_locked(_do)

    def mark_command_replay_failure(self, *, command_id: str, error_text: str) -> None:
        error_s = (_norm(error_text) or "command_replay_failed")[:200]

        def _do(con: Any, cur: Any) -> None:
            cur.execute(
                self._SQL_MARK_COMMAND_REPLAY_FAILURE,
                (error_s, _norm(command_id)),
            )

        self._store._with_retry_locked(_do)

    def try_acquire_command_delivery_replay_lock(self) -> Any | None:
        pool = getattr(self._store, "pg_pool", None)
        if pool is None:
            return None
        con = pool.getconn()
        try:
            cur = con.cursor()
            try:
                cur.execute("SELECT pg_try_advisory_lock(%s)", (_COMMAND_DELIVERY_REPLAY_ADVISORY_LOCK_ID,))
                row = cur.fetchone()
                acquired = bool(row[0] if row else False)
                con.commit()
            finally:
                cur.close()
            if acquired:
                return con
        except Exception:
            try:
                con.rollback()
            finally:
                pool.putconn(con)
            raise
        pool.putconn(con)
        return None

    def release_command_delivery_replay_lock(self, lock_handle: Any) -> None:
        if lock_handle is None:
            return
        pool = getattr(self._store, "pg_pool", None)
        if pool is None:
            return
        try:
            cur = lock_handle.cursor()
            try:
                cur.execute("SELECT pg_advisory_unlock(%s)", (_COMMAND_DELIVERY_REPLAY_ADVISORY_LOCK_ID,))
                lock_handle.commit()
            finally:
                cur.close()
        except Exception:
            try:
                lock_handle.rollback()
            except Exception:
                pass
            raise
        finally:
            pool.putconn(lock_handle)

    def update_execution_command_delivery(
        self,
        *,
        command_id: str,
        status: str,
        error_text: Optional[str],
        payload: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        status_s = _norm(status).lower()
        if status_s not in {"queued", "dispatched", "acknowledged", "failed"}:
            raise ValueError("invalid_command_delivery_status")

        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                self._SQL_UPDATE_EXECUTION_COMMAND_DELIVERY,
                (
                    status_s,
                    _norm(error_text) or None,
                    _json_payload(payload),
                    _json_payload(payload),
                    status_s,
                    status_s,
                    _norm(command_id),
                ),
            )
            row = cur.fetchone()
            return dict(row) if row else None

        return self._store._with_retry_locked(_do)

    def claim_next_execution_command_for_runner(
        self,
        *,
        runner_id: str,
        slot_id: Optional[str] = None,
        command_types: Optional[list[str]] = None,
    ) -> Optional[dict[str, Any]]:
        runner_id_s = _norm(runner_id)
        if not runner_id_s:
            raise ValueError("runner_id_required")
        slot_id_s = _norm_slot_id(slot_id) or None
        default_types = ["STOP_BOT", "START_BOT", "UPDATE_BOT_CONFIG", "PLACE_ORDER", "CLOSE_ORDER", "SYNC_STATE"]
        command_types_s = [
            str(item or "").strip().upper()
            for item in (command_types or default_types)
            if str(item or "").strip()
        ] or list(default_types)

        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                self._SQL_CLAIM_NEXT_EXECUTION_COMMAND_FOR_RUNNER,
                (
                    runner_id_s,
                    runner_id_s,
                    command_types_s,
                    slot_id_s,
                    slot_id_s,
                    slot_id_s,
                    slot_id_s,
                    runner_id_s,
                    slot_id_s,
                    slot_id_s,
                    slot_id_s,
                ),
            )
            row = cur.fetchone()
            return dict(row) if row else None

        return self._store._with_retry_locked(_do)

    def requeue_stale_http_claimed_execution_commands(
        self,
        *,
        runner_id: Optional[str] = None,
        limit: int = 100,
        older_than_sec: int = 180,
        command_types: Optional[list[str]] = None,
    ) -> int:
        runner_id_s = _norm(runner_id) or None
        limit_i = max(1, min(int(limit or 100), 1000))
        older_than_i = max(10, int(older_than_sec or 180))
        default_types = ["STOP_BOT", "START_BOT", "UPDATE_BOT_CONFIG", "PLACE_ORDER", "CLOSE_ORDER", "SYNC_STATE"]
        command_types_s = [
            str(item or "").strip().upper()
            for item in (command_types or default_types)
            if str(item or "").strip()
        ] or list(default_types)

        def _do(con: Any, cur: Any) -> int:
            cur.execute(
                self._SQL_REQUEUE_STALE_HTTP_CLAIMED_EXECUTION_COMMANDS,
                (command_types_s, runner_id_s, runner_id_s, older_than_i, limit_i, older_than_i),
            )
            return int(cur.rowcount or 0)

        return self._store._with_retry_locked(_do)

    def fail_pending_start_commands_for_deployment(self, *, deployment_id: int, reason: str) -> int:
        reason_s = _norm(reason) or "stale_start_command_reconciled"

        def _do(con: Any, cur: Any) -> int:
            cur.execute(
                self._SQL_FAIL_PENDING_START_COMMANDS_FOR_DEPLOYMENT,
                (reason_s, int(deployment_id)),
            )
            return int(cur.rowcount or 0)

        return self._store._with_retry_locked(_do)

    def reconcile_terminal_bot_control_commands(
        self,
        *,
        account_id: Optional[int] = None,
        deployment_id: Optional[int] = None,
        older_than_sec: int = 0,
    ) -> dict[str, int]:
        """Close START/STOP commands that can no longer affect a terminal deployment.

        Windows callbacks are useful for precise timing, but product flows should
        not remain locked when the deployment is already stopped/failed and
        inactive in the control plane.  START commands become superseded by the
        terminal deployment state; STOP commands are treated as acknowledged
        because the desired end state has already been reached.
        """

        account_id_i = int(account_id) if account_id is not None else None
        deployment_id_i = int(deployment_id) if deployment_id is not None else None
        older_than_i = max(0, int(older_than_sec or 0))

        def _do(con: Any, cur: Any) -> dict[str, int]:
            cur.execute(
                self._SQL_RECONCILE_TERMINAL_BOT_CONTROL_COMMANDS,
                (
                    list(_TERMINAL_DEPLOYMENT_STATUSES),
                    account_id_i,
                    account_id_i,
                    deployment_id_i,
                    deployment_id_i,
                    older_than_i,
                    older_than_i,
                    older_than_i,
                    older_than_i,
                ),
            )
            row = dict(cur.fetchone() or {})
            return {
                "failed_start_commands": _safe_int(row.get("failed_start_commands"), 0),
                "acknowledged_stop_commands": _safe_int(row.get("acknowledged_stop_commands"), 0),
            }

        return self._store._with_retry_locked(_do)

    def list_replayable_execution_commands(
        self,
        *,
        limit: int = 100,
        runner_id: Optional[str] = None,
        statuses: Optional[list[str]] = None,
        command_types: Optional[list[str]] = None,
        require_missing_stream: bool = True,
        older_than_sec: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        limit_i = max(1, min(int(limit), 1000))
        statuses_s = [
            str(item or "").strip().lower()
            for item in (statuses or ["pending"])
            if str(item or "").strip()
        ] or ["pending"]
        command_types_s = [
            str(item or "").strip().upper()
            for item in (command_types or [])
            if str(item or "").strip()
        ]
        runner_id_s = _norm(runner_id) or None
        older_than_i = max(0, int(older_than_sec or 0))

        def _do(con: Any, cur: Any) -> list[dict[str, Any]]:
            sql = [self._SQL_LIST_REPLAYABLE_EXECUTION_COMMANDS_BASE.rstrip()]
            params: list[Any] = [statuses_s]
            if require_missing_stream:
                sql.append("AND COALESCE(redis_stream_id, '') = ''")
            if command_types_s:
                sql.append("AND command_type = ANY(%s)")
                params.append(command_types_s)
            if runner_id_s:
                sql.append("AND runner_id = %s")
                params.append(runner_id_s)
            if older_than_i > 0:
                sql.append("AND updated_at < (NOW() - (%s * INTERVAL '1 second'))")
                params.append(older_than_i)
            sql.append("ORDER BY created_at ASC, command_id ASC LIMIT %s")
            params.append(limit_i)
            cur.execute("\n".join(sql), tuple(params))
            return [dict(row) for row in (cur.fetchall() or [])]

        return self._store._with_retry_read(_do)

    def count_command_delivery_replay_backlog(
        self,
        *,
        command_types: Optional[list[str]] = None,
        statuses: Optional[list[str]] = None,
        require_missing_stream: bool = True,
        older_than_sec: Optional[int] = None,
    ) -> int:
        statuses_s = [
            str(item or "").strip().lower()
            for item in (statuses or ["pending", "queued"])
            if str(item or "").strip()
        ] or ["pending", "queued"]
        command_types_s = [
            str(item or "").strip().upper()
            for item in (command_types or ["START_BOT", "STOP_BOT", "UPDATE_BOT_CONFIG"])
            if str(item or "").strip()
        ] or ["START_BOT", "STOP_BOT", "UPDATE_BOT_CONFIG"]
        older_than_i = max(0, int(older_than_sec or 0))

        def _do(con: Any, cur: Any) -> int:
            sql = [self._SQL_COUNT_COMMAND_DELIVERY_REPLAY_BACKLOG_BASE.rstrip()]
            params: list[Any] = [statuses_s, command_types_s]
            if require_missing_stream:
                sql.append("AND COALESCE(redis_stream_id, '') = ''")
            if older_than_i > 0:
                sql.append("AND updated_at < (NOW() - (%s * INTERVAL '1 second'))")
                params.append(older_than_i)
            cur.execute("\n".join(sql), tuple(params))
            row = dict(cur.fetchone() or {})
            return _safe_int(row.get("count"), 0)

        return self._store._with_retry_read(_do)

    def list_stale_processing_execution_commands(
        self,
        *,
        limit: int = 100,
        statuses: Optional[list[str]] = None,
        command_types: Optional[list[str]] = None,
        older_than_sec: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        limit_i = max(1, min(int(limit), 1000))
        statuses_s = [
            str(item or "").strip().lower()
            for item in (statuses or ["queued", "dispatched"])
            if str(item or "").strip()
        ] or ["queued", "dispatched"]
        command_types_s = [
            str(item or "").strip().upper()
            for item in (command_types or ["START_BOT", "STOP_BOT", "UPDATE_BOT_CONFIG"])
            if str(item or "").strip()
        ] or ["START_BOT", "STOP_BOT", "UPDATE_BOT_CONFIG"]
        older_than_i = max(1, int(older_than_sec or 1))

        def _do(con: Any, cur: Any) -> list[dict[str, Any]]:
            cur.execute(
                self._SQL_LIST_STALE_PROCESSING_EXECUTION_COMMANDS,
                (statuses_s, command_types_s, older_than_i, limit_i),
            )
            return [dict(row) for row in (cur.fetchall() or [])]

        return self._store._with_retry_read(_do)

    def list_stale_queued_start_commands(
        self,
        *,
        limit: int = 100,
        older_than_sec: int = 60,
    ) -> list[dict[str, Any]]:
        limit_i = max(1, min(int(limit), 1000))
        older_than_i = max(10, int(older_than_sec or 60))

        def _do(con: Any, cur: Any) -> list[dict[str, Any]]:
            cur.execute(
                self._SQL_LIST_STALE_QUEUED_START_COMMANDS,
                (older_than_i, limit_i),
            )
            return [dict(row) for row in (cur.fetchall() or [])]

        return self._store._with_retry_read(_do)

    def mark_command_processing_requeued(self, *, command_id: str, reason: str) -> None:
        reason_s = (_norm(reason) or "runner_processing_requeued")[:200]

        def _do(con: Any, cur: Any) -> None:
            cur.execute(
                self._SQL_MARK_COMMAND_PROCESSING_REQUEUED,
                (reason_s, _norm(command_id)),
            )

        self._store._with_retry_locked(_do)

    def insert_execution_event(
        self,
        *,
        event_id: str,
        event_type: str,
        account_id: Optional[int],
        deployment_id: Optional[int],
        bot_id: Optional[str],
        runner_id: str,
        slot_id: Optional[str],
        command_id: Optional[str],
        severity: str,
        payload: dict[str, Any],
        trace_id: Optional[str],
    ) -> dict[str, Any]:
        def _do(con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                self._SQL_INSERT_EXECUTION_EVENT,
                (
                    _norm(event_id),
                    _norm(event_type),
                    int(account_id) if account_id is not None else None,
                    int(deployment_id) if deployment_id is not None else None,
                    _norm(bot_id) or None,
                    _norm(runner_id),
                    _norm_slot_id(slot_id) or None,
                    _norm(command_id) or None,
                    _norm(severity) or "info",
                    _json_payload(payload),
                    _norm(trace_id) or None,
                ),
            )
            return dict(cur.fetchone() or {})

        return self._store._with_retry_locked(_do)

    def upsert_execution_audit(
        self,
        *,
        event_id: str,
        command_id: Optional[str],
        trace_id: Optional[str],
        account_id: Optional[int],
        deployment_id: Optional[int],
        runner_id: Optional[str],
        slot_id: Optional[str],
        event_type: str,
        severity: str,
        audit_status: str,
        payload: dict[str, Any],
        source_stream_id: Optional[str],
    ) -> dict[str, Any]:
        def _do(con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                self._SQL_UPSERT_EXECUTION_AUDIT,
                (
                    _norm(event_id),
                    _norm(command_id) or None,
                    _norm(trace_id) or None,
                    int(account_id) if account_id is not None else None,
                    int(deployment_id) if deployment_id is not None else None,
                    _norm(runner_id) or None,
                    _norm_slot_id(slot_id) or None,
                    _norm(event_type),
                    _norm(severity) or "info",
                    _norm(audit_status) or "recorded",
                    _json_payload(payload),
                    _norm(source_stream_id) or None,
                ),
            )
            return dict(cur.fetchone() or {})

        return self._store._with_retry_locked(_do)

    def list_execution_audit(self, *, deployment_id: int, user_id: int, limit: int = 200) -> list[dict[str, Any]]:
        limit_i = max(1, min(int(limit), 1000))

        def _do(con: Any, cur: Any) -> list[dict[str, Any]]:
            cur.execute(
                self._SQL_LIST_EXECUTION_AUDIT,
                (int(deployment_id), int(user_id), limit_i),
            )
            return [dict(row) for row in (cur.fetchall() or [])]

        return self._store._with_retry_read(_do)

    def insert_runtime_log(
        self,
        *,
        account_id: Optional[int],
        deployment_id: Optional[int],
        runner_id: Optional[str],
        slot_id: Optional[str],
        level: str,
        message: str,
        payload: dict[str, Any],
        trace_id: Optional[str],
    ) -> None:
        def _do(con: Any, cur: Any) -> None:
            cur.execute(
                self._SQL_INSERT_RUNTIME_LOG,
                (
                    int(account_id) if account_id is not None else None,
                    int(deployment_id) if deployment_id is not None else None,
                    _norm(runner_id) or None,
                    _norm_slot_id(slot_id) or None,
                    _norm(level) or "info",
                    _norm(message),
                    _json_payload(payload),
                    _norm(trace_id) or None,
                ),
            )

        self._store._with_retry_locked(_do)

