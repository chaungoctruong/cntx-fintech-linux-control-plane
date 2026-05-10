from __future__ import annotations

import uuid
from typing import Any, Optional

from app.models.control_plane import ACTIVE_DEPLOYMENT_STATUSES
from app.repositories.control_plane.query_loader import load_sql
from app.repositories.control_plane.support import (
    _json_payload,
    _norm,
    _norm_slot_id,
    _safe_int,
)


class ControlPlaneDeploymentsMixin:
    _SQL_GET_ACTIVE_DEPLOYMENT_FOR_ACCOUNT = load_sql("deployments/get_active_deployment_for_account.sql")
    _SQL_GET_ACCOUNT_RUNTIME_START_BLOCKER_ACTIVE = load_sql("deployments/get_account_runtime_start_blocker_active.sql")
    _SQL_GET_ACCOUNT_RUNTIME_START_BLOCKER_SNAPSHOT = load_sql("deployments/get_account_runtime_start_blocker_snapshot.sql")
    _SQL_LIST_DEPLOYMENTS = load_sql("deployments/list_deployments.sql")

    def get_active_deployment_for_account(self, *, account_id: int) -> Optional[dict[str, Any]]:
        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                self._SQL_GET_ACTIVE_DEPLOYMENT_FOR_ACCOUNT,
                (int(account_id), list(ACTIVE_DEPLOYMENT_STATUSES)),
            )
            row = cur.fetchone()
            return dict(row) if row else None

        return self._store._with_retry_read(_do)

    def get_account_runtime_start_blocker(self, *, account_id: int, fresh_sec: int) -> Optional[dict[str, Any]]:
        fresh_i = max(30, int(fresh_sec or 0))

        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                self._SQL_GET_ACCOUNT_RUNTIME_START_BLOCKER_ACTIVE,
                (int(account_id), list(ACTIVE_DEPLOYMENT_STATUSES)),
            )
            active_row = cur.fetchone()
            active = dict(active_row) if active_row else None

            cur.execute(
                self._SQL_GET_ACCOUNT_RUNTIME_START_BLOCKER_SNAPSHOT,
                (int(account_id), fresh_i),
            )
            row = cur.fetchone()
            if not row:
                return active
            data = dict(row)
            data["account_id"] = int(account_id)
            if not data.get("id") and data.get("blocker_deployment_id"):
                data["id"] = data.get("blocker_deployment_id")
            if not data.get("runner_id") and data.get("blocker_runner_id"):
                data["runner_id"] = data.get("blocker_runner_id")
            if not data.get("slot_id") and data.get("blocker_slot_id"):
                data["slot_id"] = data.get("blocker_slot_id")
            if active:
                active_id = _safe_int(active.get("id"), 0)
                runtime_deployment_id = _safe_int(data.get("blocker_deployment_id") or data.get("id"), 0)
                if runtime_deployment_id > 0 and active_id > 0 and runtime_deployment_id != active_id:
                    data["blocker_source"] = "runtime_duplicate"
                    data["active_deployment_id"] = active_id
                    data["active_runner_id"] = active.get("runner_id")
                    data["active_slot_id"] = active.get("slot_id")
                    return data
                return active
            return data

        return self._store._with_retry_read(_do)

    def create_deployment_draft(
        self,
        *,
        user_id: int,
        account_id: int,
        bot: dict[str, Any],
        bot_config: dict[str, Any],
        trace_id: str,
    ) -> dict[str, Any]:
        def _do(con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                """
                INSERT INTO bot_deployments(
                    user_id, account_id, bot_code, bot_name, profile_class,
                    status, desired_state, is_active, config_json, trace_id,
                    health_status, created_at, updated_at
                )
                VALUES(%s, %s, %s, %s, %s, 'draft', 'stopped', FALSE, %s::jsonb, %s, 'draft', NOW(), NOW())
                RETURNING *
                """,
                (
                    int(user_id),
                    int(account_id),
                    _norm(bot.get("bot_code") or bot.get("bot_id")),
                    _norm(bot.get("display_name") or bot.get("bot_name") or bot.get("bot_code")),
                    _norm(bot.get("profile_class") or "normal"),
                    _json_payload(bot_config),
                    _norm(trace_id) or uuid.uuid4().hex,
                ),
            )
            return dict(cur.fetchone() or {})

        return self._store._with_retry_locked(_do)

    def create_started_deployment(
        self,
        *,
        user_id: int,
        account_id: int,
        bot: dict[str, Any],
        bot_config: dict[str, Any],
        runner_id: str,
        slot_id: str,
        binding_id: int,
        trace_id: str,
        mode: str = "live",
    ) -> dict[str, Any]:
        mode_s = _norm(mode).lower() or "live"
        if mode_s not in {"live", "paper"}:
            raise ValueError("invalid_request")

        def _do(con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                """
                INSERT INTO bot_deployments(
                    user_id, account_id, bot_code, bot_name, profile_class,
                    status, desired_state, is_active, runner_id, slot_id, binding_id,
                    config_json, trace_id, health_status, mode, created_at, updated_at
                )
                VALUES(
                    %s, %s, %s, %s, %s,
                    'start_requested', 'running', TRUE, %s, %s, %s,
                    %s::jsonb, %s, 'starting', %s, NOW(), NOW()
                )
                RETURNING *
                """,
                (
                    int(user_id),
                    int(account_id),
                    _norm(bot.get("bot_code") or bot.get("bot_id")),
                    _norm(bot.get("display_name") or bot.get("bot_name") or bot.get("bot_code")),
                    _norm(bot.get("profile_class") or "normal"),
                    _norm(runner_id),
                    _norm_slot_id(slot_id),
                    int(binding_id),
                    _json_payload(bot_config),
                    _norm(trace_id) or uuid.uuid4().hex,
                    mode_s,
                ),
            )
            return dict(cur.fetchone() or {})

        return self._store._with_retry_locked(_do)

    def get_queued_replacement_deployment(self, *, account_id: int) -> Optional[dict[str, Any]]:
        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                """
                SELECT *
                FROM bot_deployments
                WHERE account_id = %s
                  AND status = 'queued'
                  AND desired_state = 'running'
                  AND health_status = 'waiting_previous_runtime_stop'
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (int(account_id),),
            )
            row = cur.fetchone()
            return dict(row) if row else None

        return self._store._with_retry_read(_do)

    def create_queued_replacement_deployment(
        self,
        *,
        user_id: int,
        account_id: int,
        bot: dict[str, Any],
        bot_config: dict[str, Any],
        trace_id: str,
        mode: str = "live",
        previous_deployment_id: Optional[int] = None,
    ) -> dict[str, Any]:
        mode_s = _norm(mode).lower() or "live"
        if mode_s not in {"live", "paper"}:
            raise ValueError("invalid_request")

        def _do(con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                """
                INSERT INTO bot_deployments(
                    user_id, account_id, bot_code, bot_name, profile_class,
                    status, desired_state, is_active, config_json, trace_id,
                    health_status, last_error, mode, created_at, updated_at
                )
                VALUES(
                    %s, %s, %s, %s, %s,
                    'queued', 'running', FALSE, %s::jsonb, %s,
                    'waiting_previous_runtime_stop', %s, %s, NOW(), NOW()
                )
                RETURNING *
                """,
                (
                    int(user_id),
                    int(account_id),
                    _norm(bot.get("bot_code") or bot.get("bot_id")),
                    _norm(bot.get("display_name") or bot.get("bot_name") or bot.get("bot_code")),
                    _norm(bot.get("profile_class") or "normal"),
                    _json_payload(bot_config),
                    _norm(trace_id) or uuid.uuid4().hex,
                    f"waiting_previous_deployment_stop:{int(previous_deployment_id)}"
                    if previous_deployment_id
                    else "waiting_previous_runtime_stop",
                    mode_s,
                ),
            )
            return dict(cur.fetchone() or {})

        return self._store._with_retry_locked(_do)

    def activate_queued_deployment_start(
        self,
        *,
        deployment_id: int,
        runner_id: str,
        slot_id: str,
        binding_id: int,
        trace_id: str,
    ) -> Optional[dict[str, Any]]:
        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                """
                UPDATE bot_deployments
                SET status = 'start_requested',
                    desired_state = 'running',
                    is_active = TRUE,
                    runner_id = %s,
                    slot_id = %s,
                    binding_id = %s,
                    trace_id = COALESCE(NULLIF(%s, ''), trace_id),
                    health_status = 'starting',
                    last_error = NULL,
                    updated_at = NOW()
                WHERE id = %s
                  AND status = 'queued'
                  AND desired_state = 'running'
                RETURNING *
                """,
                (
                    _norm(runner_id),
                    _norm_slot_id(slot_id),
                    int(binding_id),
                    _norm(trace_id),
                    int(deployment_id),
                ),
            )
            row = cur.fetchone()
            return dict(row) if row else None

        return self._store._with_retry_locked(_do)

    def update_deployment_config(
        self,
        *,
        deployment_id: int,
        user_id: int,
        bot_config: dict[str, Any],
        allow_active: bool = False,
    ) -> Optional[dict[str, Any]]:
        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            active_guard = "" if allow_active else """
                  AND status IN ('draft', 'stopped', 'failed', 'blocked')
                  AND is_active = FALSE
            """
            cur.execute(
                f"""
                UPDATE bot_deployments
                SET config_json = %s::jsonb,
                    updated_at = NOW()
                WHERE id = %s
                  AND user_id = %s
                  {active_guard}
                RETURNING *
                """,
                (
                    _json_payload(bot_config),
                    int(deployment_id),
                    int(user_id),
                ),
            )
            row = cur.fetchone()
            return dict(row) if row else None

        return self._store._with_retry_locked(_do)

    def get_open_config_restart_command(self, *, deployment_id: int) -> Optional[dict[str, Any]]:
        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                """
                SELECT stop_cmd.*
                FROM execution_commands stop_cmd
                WHERE stop_cmd.deployment_id = %s
                  AND stop_cmd.command_type = 'STOP_BOT'
                  AND stop_cmd.payload_json->>'control_flow' = 'deployment_config_restart'
                  AND stop_cmd.delivery_status IN ('pending', 'queued', 'dispatched', 'acknowledged')
                  AND stop_cmd.created_at >= NOW() - INTERVAL '30 minutes'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM execution_commands start_cmd
                      WHERE start_cmd.deployment_id = stop_cmd.deployment_id
                        AND start_cmd.command_type = 'START_BOT'
                        AND start_cmd.payload_json->>'control_flow' = 'deployment_config_restart'
                        AND start_cmd.payload_json->>'config_restart_stop_command_id' = stop_cmd.command_id
                  )
                ORDER BY stop_cmd.created_at DESC, stop_cmd.id DESC
                LIMIT 1
                """,
                (int(deployment_id),),
            )
            row = cur.fetchone()
            return dict(row) if row else None

        return self._store._with_retry_read(_do)

    def get_config_restart_stop_command_for_start(
        self,
        *,
        deployment_id: int,
        command_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        command_id_s = _norm(command_id) or None

        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            params: list[Any] = [int(deployment_id)]
            command_filter = ""
            if command_id_s:
                command_filter = "AND stop_cmd.command_id = %s"
                params.append(command_id_s)
            cur.execute(
                f"""
                SELECT stop_cmd.*
                FROM execution_commands stop_cmd
                WHERE stop_cmd.deployment_id = %s
                  {command_filter}
                  AND stop_cmd.command_type = 'STOP_BOT'
                  AND stop_cmd.payload_json->>'control_flow' = 'deployment_config_restart'
                  AND stop_cmd.delivery_status IN ('queued', 'dispatched', 'acknowledged')
                  AND NOT EXISTS (
                      SELECT 1
                      FROM execution_commands start_cmd
                      WHERE start_cmd.deployment_id = stop_cmd.deployment_id
                        AND start_cmd.command_type = 'START_BOT'
                        AND start_cmd.payload_json->>'control_flow' = 'deployment_config_restart'
                        AND start_cmd.payload_json->>'config_restart_stop_command_id' = stop_cmd.command_id
                  )
                ORDER BY stop_cmd.created_at DESC, stop_cmd.id DESC
                LIMIT 1
                """,
                tuple(params),
            )
            row = cur.fetchone()
            return dict(row) if row else None

        return self._store._with_retry_read(_do)

    def get_open_replacement_stop_command(
        self,
        *,
        previous_deployment_id: int,
        replacement_deployment_id: int,
    ) -> Optional[dict[str, Any]]:
        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                """
                SELECT stop_cmd.*
                FROM execution_commands stop_cmd
                WHERE stop_cmd.deployment_id = %s
                  AND stop_cmd.command_type = 'STOP_BOT'
                  AND stop_cmd.payload_json->>'control_flow' = 'deployment_replacement_start'
                  AND stop_cmd.payload_json->>'replacement_deployment_id' = %s
                  AND stop_cmd.delivery_status IN ('pending', 'queued', 'dispatched', 'acknowledged')
                  AND NOT EXISTS (
                      SELECT 1
                      FROM execution_commands start_cmd
                      WHERE start_cmd.deployment_id = %s
                        AND start_cmd.command_type = 'START_BOT'
                        AND start_cmd.payload_json->>'control_flow' = 'deployment_replacement_start'
                        AND start_cmd.payload_json->>'replacement_stop_command_id' = stop_cmd.command_id
                  )
                ORDER BY stop_cmd.created_at DESC, stop_cmd.id DESC
                LIMIT 1
                """,
                (
                    int(previous_deployment_id),
                    str(int(replacement_deployment_id)),
                    int(replacement_deployment_id),
                ),
            )
            row = cur.fetchone()
            return dict(row) if row else None

        return self._store._with_retry_read(_do)

    def get_replacement_stop_command_for_start(
        self,
        *,
        previous_deployment_id: int,
        command_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        command_id_s = _norm(command_id) or None

        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            params: list[Any] = [int(previous_deployment_id)]
            command_filter = ""
            if command_id_s:
                command_filter = "AND stop_cmd.command_id = %s"
                params.append(command_id_s)
            cur.execute(
                f"""
                SELECT stop_cmd.*
                FROM execution_commands stop_cmd
                WHERE stop_cmd.deployment_id = %s
                  {command_filter}
                  AND stop_cmd.command_type = 'STOP_BOT'
                  AND stop_cmd.payload_json->>'control_flow' = 'deployment_replacement_start'
                  AND stop_cmd.delivery_status IN ('queued', 'dispatched', 'acknowledged')
                  AND NOT EXISTS (
                      SELECT 1
                      FROM execution_commands start_cmd
                      WHERE start_cmd.deployment_id = NULLIF(stop_cmd.payload_json->>'replacement_deployment_id', '')::BIGINT
                        AND start_cmd.command_type = 'START_BOT'
                        AND start_cmd.payload_json->>'control_flow' = 'deployment_replacement_start'
                        AND start_cmd.payload_json->>'replacement_stop_command_id' = stop_cmd.command_id
                  )
                ORDER BY stop_cmd.created_at DESC, stop_cmd.id DESC
                LIMIT 1
                """,
                tuple(params),
            )
            row = cur.fetchone()
            return dict(row) if row else None

        return self._store._with_retry_read(_do)

    def insert_deployment_audit(
        self,
        *,
        deployment_id: int,
        action: str,
        payload: dict[str, Any],
        result: str,
        trace_id: Optional[str] = None,
    ) -> None:
        def _do(con: Any, cur: Any) -> None:
            cur.execute(
                """
                INSERT INTO audit_logs(
                    telegram_id, user_id, account_id, deployment_id, trace_id,
                    action, payload_json, result, created_at
                )
                SELECT
                    u.telegram_id,
                    d.user_id,
                    d.account_id,
                    d.id,
                    %s,
                    %s,
                    %s::jsonb,
                    %s,
                    EXTRACT(EPOCH FROM NOW())::BIGINT
                FROM bot_deployments d
                JOIN users u ON u.id = d.user_id
                WHERE d.id = %s
                """,
                (
                    _norm(trace_id) or None,
                    _norm(action),
                    _json_payload(payload),
                    _norm(result) or "recorded",
                    int(deployment_id),
                ),
            )

        self._store._with_retry_locked(_do)

    def fail_stale_config_restart_commands(self, *, timeout_sec: int) -> int:
        timeout_i = max(30, int(timeout_sec or 0))

        def _do(con: Any, cur: Any) -> int:
            cur.execute(
                """
                WITH stale AS (
                    UPDATE execution_commands c
                    SET delivery_status = 'failed',
                        last_error = COALESCE(NULLIF(c.last_error, ''), 'config_restart_command_timeout'),
                        updated_at = NOW()
                    WHERE c.command_type IN ('START_BOT', 'STOP_BOT')
                      AND c.payload_json->>'control_flow' = 'deployment_config_restart'
                      AND (
                          c.delivery_status IN ('pending', 'queued', 'dispatched')
                          OR (
                              c.delivery_status = 'acknowledged'
                              AND (
                                  (
                                      c.command_type = 'STOP_BOT'
                                      AND NOT EXISTS (
                                          SELECT 1
                                          FROM execution_commands start_cmd
                                          WHERE start_cmd.deployment_id = c.deployment_id
                                            AND start_cmd.command_type = 'START_BOT'
                                            AND start_cmd.payload_json->>'control_flow' = 'deployment_config_restart'
                                            AND start_cmd.payload_json->>'config_restart_stop_command_id' = c.command_id
                                      )
                                  )
                                  OR (
                                      c.command_type = 'START_BOT'
                                      AND EXISTS (
                                          SELECT 1
                                          FROM bot_deployments d_running
                                          WHERE d_running.id = c.deployment_id
                                            AND COALESCE(d_running.status, '') <> 'running'
                                      )
                                  )
                              )
                          )
                      )
                      AND NOT EXISTS (
                          SELECT 1
                          FROM audit_logs existing_audit
                          WHERE existing_audit.deployment_id = c.deployment_id
                            AND existing_audit.action = 'deployment.config.restart_failed'
                            AND existing_audit.payload_json->>'command_id' = c.command_id
                      )
                      AND c.created_at < NOW() - (%s * INTERVAL '1 second')
                    RETURNING c.*
                ),
                timeout_deployments AS (
                    UPDATE bot_deployments d
                    SET health_status = 'config_restart_timeout',
                        last_error = COALESCE(NULLIF(d.last_error, ''), 'config_restart_command_timeout'),
                        updated_at = NOW()
                    FROM stale s
                    WHERE d.id = s.deployment_id
                    RETURNING d.id
                ),
                audited AS (
                    INSERT INTO audit_logs(
                        telegram_id, user_id, account_id, deployment_id, trace_id,
                        action, payload_json, result, created_at
                    )
                    SELECT
                        u.telegram_id,
                        d.user_id,
                        d.account_id,
                        d.id,
                        s.trace_id,
                        'deployment.config.restart_failed',
                        jsonb_build_object(
                            'deployment_id', d.id,
                            'account_id', d.account_id,
                            'command_id', s.command_id,
                            'command_type', s.command_type,
                            'reason', 'config_restart_command_timeout'
                        ),
                        'timeout',
                        EXTRACT(EPOCH FROM NOW())::BIGINT
                    FROM stale s
                    JOIN bot_deployments d ON d.id = s.deployment_id
                    JOIN users u ON u.id = d.user_id
                    RETURNING 1
                )
                SELECT COUNT(*) AS failed_count
                FROM stale
                """,
                (timeout_i,),
            )
            row = cur.fetchone() or {}
            return _safe_int(row.get("failed_count"), 0)

        return self._store._with_retry_locked(_do)

    def fail_stale_config_hot_update_commands(self, *, timeout_sec: int) -> int:
        timeout_i = max(30, int(timeout_sec or 0))

        def _do(con: Any, cur: Any) -> int:
            cur.execute(
                """
                WITH stale AS (
                    UPDATE execution_commands c
                    SET delivery_status = 'failed',
                        last_error = COALESCE(NULLIF(c.last_error, ''), 'config_hot_update_command_timeout'),
                        updated_at = NOW()
                    WHERE c.command_type = 'UPDATE_BOT_CONFIG'
                      AND c.delivery_status IN ('pending', 'queued', 'dispatched')
                      AND NOT EXISTS (
                          SELECT 1
                          FROM audit_logs existing_audit
                          WHERE existing_audit.deployment_id = c.deployment_id
                            AND existing_audit.action = 'deployment.config.hot_update_failed'
                            AND existing_audit.payload_json->>'command_id' = c.command_id
                      )
                      AND COALESCE(c.dispatched_at, c.updated_at, c.created_at)
                          < NOW() - (%s * INTERVAL '1 second')
                    RETURNING c.*
                ),
                timeout_deployments AS (
                    UPDATE bot_deployments d
                    SET health_status = 'config_hot_update_restart_required',
                        last_error = COALESCE(NULLIF(d.last_error, ''), 'config_hot_update_command_timeout'),
                        updated_at = NOW()
                    FROM stale s
                    WHERE d.id = s.deployment_id
                    RETURNING d.id
                ),
                audited AS (
                    INSERT INTO audit_logs(
                        telegram_id, user_id, account_id, deployment_id, trace_id,
                        action, payload_json, result, created_at
                    )
                    SELECT
                        u.telegram_id,
                        d.user_id,
                        d.account_id,
                        d.id,
                        s.trace_id,
                        'deployment.config.hot_update_failed',
                        jsonb_build_object(
                            'deployment_id', d.id,
                            'account_id', d.account_id,
                            'command_id', s.command_id,
                            'command_type', s.command_type,
                            'reason', 'config_hot_update_command_timeout',
                            'requires_restart', TRUE
                        ),
                        'restart_required_timeout',
                        EXTRACT(EPOCH FROM NOW())::BIGINT
                    FROM stale s
                    JOIN bot_deployments d ON d.id = s.deployment_id
                    JOIN users u ON u.id = d.user_id
                    RETURNING 1
                )
                SELECT COUNT(*) AS failed_count
                FROM stale
                """,
                (timeout_i,),
            )
            row = cur.fetchone() or {}
            return _safe_int(row.get("failed_count"), 0)

        return self._store._with_retry_locked(_do)

    def update_deployment_status(
        self,
        *,
        deployment_id: int,
        status: str,
        desired_state: Optional[str] = None,
        is_active: Optional[bool] = None,
        health_status: Optional[str] = None,
        last_error: Optional[str] = None,
        started: bool = False,
        stopped: bool = False,
        runner_id: Optional[str] = None,
        slot_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                """
                UPDATE bot_deployments
                SET status = %s,
                    desired_state = COALESCE(%s, desired_state),
                    is_active = COALESCE(%s, is_active),
                    health_status = COALESCE(%s, health_status),
                    last_error = %s,
                    runner_id = COALESCE(%s, runner_id),
                    slot_id = COALESCE(%s, slot_id),
                    started_at = CASE WHEN %s THEN COALESCE(started_at, NOW()) ELSE started_at END,
                    stopped_at = CASE WHEN %s THEN NOW() ELSE stopped_at END,
                    updated_at = NOW()
                WHERE id = %s
                RETURNING *
                """,
                (
                    _norm(status),
                    _norm(desired_state) or None,
                    is_active,
                    _norm(health_status) or None,
                    _norm(last_error) or None,
                    _norm(runner_id) or None,
                    _norm_slot_id(slot_id) or None,
                    bool(started),
                    bool(stopped),
                    int(deployment_id),
                ),
            )
            row = cur.fetchone()
            return dict(row) if row else None

        return self._store._with_retry_locked(_do)

    def reconcile_deployment_runtime_slot(
        self,
        *,
        deployment_id: int,
        account_id: Optional[int],
        runner_id: str,
        slot_id: str,
    ) -> dict[str, Any]:
        runner_id_s = _norm(runner_id)
        slot_id_s = _norm_slot_id(slot_id)
        if not runner_id_s or not slot_id_s:
            return {"reconciled": False, "reason": "missing_runtime_slot"}
        account_id_i = int(account_id) if account_id is not None else None

        def _do(con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                """
                SELECT
                    d.id,
                    d.account_id,
                    d.runner_id,
                    d.slot_id,
                    d.binding_id,
                    d.status,
                    d.desired_state,
                    d.is_active,
                    b.runner_id AS binding_runner_id,
                    b.slot_id AS binding_slot_id
                FROM bot_deployments d
                LEFT JOIN account_slot_bindings b ON b.id = d.binding_id
                WHERE d.id = %s
                FOR UPDATE OF d
                """,
                (int(deployment_id),),
            )
            row = cur.fetchone()
            if not row:
                return {"reconciled": False, "reason": "deployment_not_found"}

            deployment_account_id = int(row["account_id"])
            if account_id_i is not None and account_id_i != deployment_account_id:
                return {"reconciled": False, "reason": "account_mismatch"}
            deployment_status = _norm(row.get("status")).lower()
            deployment_desired_state = _norm(row.get("desired_state")).lower()
            deployment_is_active = bool(row.get("is_active"))
            if deployment_status not in ACTIVE_DEPLOYMENT_STATUSES or (
                not deployment_is_active and deployment_desired_state != "running"
            ):
                return {
                    "reconciled": False,
                    "reason": "deployment_not_active",
                    "deployment_status": deployment_status,
                }

            old_runner_id = _norm(row.get("runner_id"))
            old_slot_id = _norm_slot_id(row.get("slot_id"))
            binding_runner_id = _norm(row.get("binding_runner_id"))
            binding_slot_id = _norm_slot_id(row.get("binding_slot_id"))
            binding_id = row.get("binding_id")
            binding_id_i = int(binding_id) if binding_id is not None else None

            deployment_matches = old_runner_id == runner_id_s and old_slot_id == slot_id_s
            binding_matches = binding_runner_id == runner_id_s and binding_slot_id == slot_id_s
            if deployment_matches and binding_matches:
                return {"reconciled": False, "reason": "already_aligned"}

            cur.execute(
                """
                UPDATE account_slot_bindings b
                SET is_current = FALSE,
                    binding_state = CASE WHEN binding_state = 'broken' THEN binding_state ELSE 'released' END,
                    updated_at = NOW()
                WHERE b.runner_id = %s
                  AND b.slot_id = %s
                  AND b.is_current = TRUE
                  AND b.account_id <> %s
                  AND NOT EXISTS (
                      SELECT 1
                      FROM bot_deployments d
                      WHERE d.account_id = b.account_id
                        AND d.runner_id = b.runner_id
                        AND d.slot_id = b.slot_id
                        AND d.status = ANY(%s)
                  )
                """,
                (runner_id_s, slot_id_s, deployment_account_id, list(ACTIVE_DEPLOYMENT_STATUSES)),
            )

            cur.execute(
                """
                SELECT id, account_id
                FROM bot_deployments
                WHERE runner_id = %s
                  AND slot_id = %s
                  AND id <> %s
                  AND status = ANY(%s)
                LIMIT 1
                """,
                (runner_id_s, slot_id_s, int(deployment_id), list(ACTIVE_DEPLOYMENT_STATUSES)),
            )
            conflict = cur.fetchone()
            if conflict:
                return {
                    "reconciled": False,
                    "reason": "runtime_slot_active_conflict",
                    "conflict_deployment_id": conflict.get("id"),
                    "conflict_account_id": conflict.get("account_id"),
                }

            cur.execute(
                """
                UPDATE account_slot_bindings
                SET is_current = FALSE,
                    binding_state = CASE WHEN binding_state = 'broken' THEN binding_state ELSE 'released' END,
                    updated_at = NOW()
                WHERE account_id = %s
                  AND is_current = TRUE
                  AND (%s IS NULL OR id <> %s)
                """,
                (deployment_account_id, binding_id_i, binding_id_i),
            )

            if binding_id_i is not None:
                cur.execute(
                    """
                    UPDATE account_slot_bindings
                    SET runner_id = %s,
                        slot_id = %s,
                        binding_state = 'active',
                        is_sticky = TRUE,
                        is_current = TRUE,
                        last_used_at = NOW(),
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (runner_id_s, slot_id_s, binding_id_i),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO account_slot_bindings(
                        account_id, runner_id, slot_id, binding_state,
                        is_sticky, is_current, last_used_at, created_at, updated_at
                    )
                    VALUES(%s, %s, %s, 'active', TRUE, TRUE, NOW(), NOW(), NOW())
                    RETURNING id
                    """,
                    (deployment_account_id, runner_id_s, slot_id_s),
                )
                inserted = cur.fetchone() or {}
                binding_id_i = int(inserted["id"]) if inserted.get("id") is not None else None

            cur.execute(
                """
                UPDATE bot_deployments
                SET runner_id = %s,
                    slot_id = %s,
                    binding_id = COALESCE(%s, binding_id),
                    updated_at = NOW()
                WHERE id = %s
                """,
                (runner_id_s, slot_id_s, binding_id_i, int(deployment_id)),
            )

            cur.execute(
                """
                UPDATE runner_slots
                SET current_account_id = %s,
                    status = CASE WHEN status = 'broken' THEN status ELSE 'allocated' END,
                    metadata_json = jsonb_strip_nulls(
                        COALESCE(metadata_json, '{}'::jsonb) || jsonb_build_object(
                            'account_id', %s,
                            'active_account_id', %s,
                            'deployment_id', %s,
                            'sticky_account_id', %s,
                            'available_for_new_account', FALSE,
                            'control_plane_state', 'allocated',
                            'current_control_plane_state', 'allocated',
                            'runner_state', 'active',
                            'current_runner_state', 'active',
                            'last_reason', 'deployment_runtime_slot_reconciled',
                            'last_error', ''
                        )
                    ),
                    updated_at = NOW()
                WHERE runner_id = %s
                  AND slot_id = %s
                """,
                (
                    deployment_account_id,
                    str(deployment_account_id),
                    str(deployment_account_id),
                    str(int(deployment_id)),
                    str(deployment_account_id),
                    runner_id_s,
                    slot_id_s,
                ),
            )

            stale_slot_candidates: set[tuple[str, str]] = set()
            if old_runner_id and old_slot_id and (old_runner_id != runner_id_s or old_slot_id != slot_id_s):
                stale_slot_candidates.add((old_runner_id, old_slot_id))
            if binding_runner_id and binding_slot_id and (binding_runner_id != runner_id_s or binding_slot_id != slot_id_s):
                stale_slot_candidates.add((binding_runner_id, binding_slot_id))

            for stale_runner_id, stale_slot_id in stale_slot_candidates:
                cur.execute(
                    """
                    UPDATE runner_slots
                    SET current_account_id = NULL,
                        status = CASE WHEN status = 'broken' THEN status ELSE 'ready' END,
                        metadata_json = jsonb_strip_nulls(
                            (
                                COALESCE(metadata_json, '{}'::jsonb)
                                    - 'account_id'
                                    - 'active_account_id'
                                    - 'deployment_id'
                                    - 'verification_job_id'
                                    - 'verification_status'
                                    - 'verification_account_id'
                                    - 'verification_attempt'
                                    - 'sticky_account_id'
                                    - 'reserved_account_id'
                                    - 'current_control_plane_state'
                                    - 'previous_control_plane_state'
                                    - 'runner_state'
                                    - 'current_runner_state'
                                    - 'previous_runner_state'
                                    - 'current_state'
                                    - 'previous_state'
                                    - 'reason'
                                    - 'last_error'
                            ) || jsonb_build_object(
                                'available_for_new_account', TRUE,
                                'control_plane_state', 'ready',
                                'current_control_plane_state', 'ready',
                                'runner_state', 'ready',
                                'current_runner_state', 'ready',
                                'last_reason', 'deployment_runtime_slot_moved',
                                'last_error', ''
                            )
                        ),
                        updated_at = NOW()
                    WHERE runner_id = %s
                      AND slot_id = %s
                      AND NOT EXISTS (
                          SELECT 1
                          FROM bot_deployments d
                          WHERE d.runner_id = %s
                            AND d.slot_id = %s
                            AND d.id <> %s
                            AND d.status = ANY(%s)
                      )
                      AND (current_account_id IS NULL OR current_account_id = %s)
                      AND (
                          NULLIF(BTRIM(COALESCE(metadata_json->>'account_id', '')), '') IS NULL
                          OR NULLIF(BTRIM(COALESCE(metadata_json->>'account_id', '')), '') = %s
                      )
                    """,
                    (
                        stale_runner_id,
                        stale_slot_id,
                        stale_runner_id,
                        stale_slot_id,
                        int(deployment_id),
                        list(ACTIVE_DEPLOYMENT_STATUSES),
                        deployment_account_id,
                        str(deployment_account_id),
                    ),
                )

            return {
                "reconciled": True,
                "deployment_id": int(deployment_id),
                "account_id": deployment_account_id,
                "runner_id": runner_id_s,
                "slot_id": slot_id_s,
                "previous_runner_id": old_runner_id,
                "previous_slot_id": old_slot_id,
                "previous_binding_runner_id": binding_runner_id,
                "previous_binding_slot_id": binding_slot_id,
            }

        return self._store._with_retry_locked(_do)

    def get_deployment(self, *, deployment_id: int, user_id: Optional[int] = None) -> Optional[dict[str, Any]]:
        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            sql = [
                """
                SELECT
                    d.*,
                    a.broker,
                    a.server,
                    a.login,
                    s.status AS slot_status,
                    n.status AS runner_status,
                    snap.connection_status,
                    snap.pnl,
                    snap.balance,
                    snap.equity,
                    snap.free_margin,
                    snap.heartbeat_at AS snapshot_heartbeat_at
                FROM bot_deployments d
                JOIN broker_accounts a ON a.id = d.account_id
                LEFT JOIN runner_slots s
                  ON s.runner_id = d.runner_id
                 AND s.slot_id = d.slot_id
                LEFT JOIN runner_nodes n ON n.runner_id = d.runner_id
                LEFT JOIN account_state_snapshots snap ON snap.account_id = d.account_id
                WHERE d.id = %s
                """
            ]
            params: list[Any] = [int(deployment_id)]
            if user_id is not None:
                sql.append("AND d.user_id = %s")
                params.append(int(user_id))
            cur.execute("\n".join(sql), tuple(params))
            row = cur.fetchone()
            return dict(row) if row else None

        return self._store._with_retry_read(_do)

    def list_deployments(self, *, user_id: int) -> list[dict[str, Any]]:
        def _do(con: Any, cur: Any) -> list[dict[str, Any]]:
            cur.execute(
                self._SQL_LIST_DEPLOYMENTS,
                (int(user_id), list(ACTIVE_DEPLOYMENT_STATUSES)),
            )
            return [dict(row) for row in (cur.fetchall() or [])]

        return self._store._with_retry_read(_do)

    def touch_deployment_heartbeat(
        self,
        *,
        deployment_id: Optional[int],
        account_id: Optional[int],
        runner_id: str,
        slot_id: Optional[str],
        payload: dict[str, Any],
    ) -> None:
        deployment_id_i = int(deployment_id) if deployment_id is not None else None
        account_id_i = int(account_id) if account_id is not None else None
        runner_id_s = _norm(runner_id)
        slot_id_s = _norm_slot_id(slot_id) or None

        def _do(con: Any, cur: Any) -> None:
            cur.execute(
                """
                UPDATE runner_nodes
                SET last_heartbeat_at = NOW(),
                    status = CASE WHEN status = 'offline' THEN 'online' ELSE status END,
                    updated_at = NOW()
                WHERE runner_id = %s
                """,
                (runner_id_s,),
            )
            if slot_id_s:
                cur.execute(
                    """
                    UPDATE runner_slots
                    SET last_heartbeat_at = NOW(),
                        updated_at = NOW()
                    WHERE runner_id = %s AND slot_id = %s
                    """,
                    (runner_id_s, slot_id_s),
                )
            if deployment_id_i is not None:
                cur.execute(
                    """
                    UPDATE bot_deployments
                    SET last_heartbeat_at = NOW(),
                        health_status = CASE
                            WHEN health_status IS NULL OR health_status = '' THEN 'running'
                            WHEN LOWER(health_status) IN ('stale', 'offline', 'degraded', 'starting') THEN 'running'
                            ELSE health_status
                        END,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (deployment_id_i,),
                )
            else:
                cur.execute(
                    """
                    UPDATE bot_deployments AS d
                    SET last_heartbeat_at = NOW(),
                        health_status = CASE
                            WHEN d.health_status IS NULL OR d.health_status = '' THEN 'running'
                            WHEN LOWER(d.health_status) IN ('stale', 'offline', 'degraded', 'starting') THEN 'running'
                            ELSE d.health_status
                        END,
                        updated_at = NOW()
                    FROM bot_catalog AS c
                    WHERE c.bot_code = d.bot_code
                      AND d.runner_id = %s
                      AND (%s IS NULL OR d.slot_id = %s)
                      AND d.status = 'running'
                      AND d.desired_state = 'running'
                      AND COALESCE(d.is_active, FALSE) = TRUE
                      AND LOWER(COALESCE(
                          NULLIF(BTRIM(c.runtime_env->>'bot_type'), ''),
                          NULLIF(BTRIM(c.resource_hints->>'bot_type'), ''),
                          ''
                      )) = 'backend_webhook_signal'
                      AND LOWER(COALESCE(
                          NULLIF(BTRIM(c.runtime_env->>'windows_role'), ''),
                          NULLIF(BTRIM(c.resource_hints->>'windows_role'), ''),
                          ''
                      )) = 'mt5_executor_only'
                    """,
                    (runner_id_s, slot_id_s, slot_id_s),
                )
            if account_id_i is not None:
                cur.execute(
                    """
                    INSERT INTO account_state_snapshots(
                        account_id, deployment_id, runner_id, slot_id,
                        connection_status, pnl, balance, equity, free_margin,
                        payload_json, heartbeat_at, created_at, updated_at
                    )
                    VALUES(%s, %s, %s, %s, 'connected', NULL, NULL, NULL, NULL, %s::jsonb, NOW(), NOW(), NOW())
                    ON CONFLICT(account_id) DO UPDATE SET
                        deployment_id = COALESCE(EXCLUDED.deployment_id, account_state_snapshots.deployment_id),
                        runner_id = COALESCE(EXCLUDED.runner_id, account_state_snapshots.runner_id),
                        slot_id = COALESCE(EXCLUDED.slot_id, account_state_snapshots.slot_id),
                        connection_status = EXCLUDED.connection_status,
                        payload_json = EXCLUDED.payload_json,
                        heartbeat_at = NOW(),
                        updated_at = NOW()
                    """,
                    (account_id_i, deployment_id_i, runner_id_s, slot_id_s, _json_payload(payload)),
                )

        self._store._with_retry_locked(_do)

    def list_running_deployments_for_account(self, *, account_id: int) -> list[dict[str, Any]]:
        """Liet ke deployment dang running cua 1 account (de circuit-breaker auto-stop)."""
        def _do(con: Any, cur: Any) -> list[dict[str, Any]]:
            cur.execute(
                """
                SELECT id, account_id, bot_code, runner_id, slot_id, trace_id, status
                FROM bot_deployments
                WHERE account_id = %s
                  AND status IN ('start_requested', 'starting', 'running')
                ORDER BY id ASC
                """,
                (int(account_id),),
            )
            return [dict(row) for row in (cur.fetchall() or [])]

        return self._store._with_retry_read(_do)
