from __future__ import annotations

from typing import Any, Optional

from app.models.control_plane import ACTIVE_DEPLOYMENT_STATUSES
from app.repositories.control_plane.query_loader import load_sql
from app.repositories.control_plane.support import (
    _TERMINAL_DEPLOYMENT_STATUSES,
    _decorate_account_verification_projection,
    _json_payload,
    _norm,
)


class ControlPlaneAccountsMixin:
    _SQL_FIND_MT5_ACCOUNT_IDENTITY_CONFLICT = load_sql("accounts/find_mt5_account_identity_conflict.sql")
    _SQL_GET_ACCOUNT = load_sql("accounts/get_account.sql")
    _SQL_LIST_ACCOUNTS_FOR_USER = load_sql("accounts/list_accounts_for_user.sql")

    def connect_account(
        self,
        *,
        user_id: int,
        broker: str,
        server: str,
        login: str,
        password_encrypted: str,
        label: Optional[str] = None,
    ) -> dict[str, Any]:
        broker_s = _norm(broker)
        server_s = _norm(server)
        login_s = _norm(login)
        if not broker_s or not server_s or not login_s or not password_encrypted:
            raise ValueError("invalid_account_connect_payload")
        label_s = _norm(label) or None

        def _do(con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                """
                INSERT INTO broker_accounts(
                    user_id, broker, server, login, status, label, is_active,
                    verification_requested_at, verified_at, created_at, updated_at
                )
                VALUES(%s, %s, %s, %s, 'pending_verification', %s, TRUE, NOW(), NULL, NOW(), NOW())
                ON CONFLICT(user_id, broker, server, login) DO UPDATE SET
                    status = 'pending_verification',
                    label = COALESCE(EXCLUDED.label, broker_accounts.label),
                    last_error = NULL,
                    verification_requested_at = NOW(),
                    verified_at = NULL,
                    is_active = TRUE,
                    updated_at = NOW()
                RETURNING id, user_id, broker, server, login, status, label, is_active,
                          last_error, verification_requested_at, verified_at, created_at, updated_at
                """,
                (int(user_id), broker_s, server_s, login_s, label_s),
            )
            account = dict(cur.fetchone() or {})
            account_id = int(account.get("id"))
            cur.execute(
                """
                INSERT INTO account_credentials_encrypted(
                    account_id, password_encrypted, metadata_json, created_at, updated_at
                )
                VALUES(%s, %s, %s::jsonb, NOW(), NOW())
                ON CONFLICT(account_id) DO UPDATE SET
                    password_encrypted = EXCLUDED.password_encrypted,
                    updated_at = NOW()
                """,
                (account_id, password_encrypted, _json_payload({})),
            )
            return account

        return self._store._with_retry_locked(_do)

    def find_mt5_account_identity_conflict(
        self,
        *,
        user_id: int,
        broker: str,
        server: str,
        login: str,
        exclude_account_id: Optional[int] = None,
    ) -> Optional[dict[str, Any]]:
        broker_s = _norm(broker)
        server_s = _norm(server)
        login_s = _norm(login)
        if not broker_s or not server_s or not login_s:
            return None
        exclude_id = int(exclude_account_id) if exclude_account_id is not None else None

        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                self._SQL_FIND_MT5_ACCOUNT_IDENTITY_CONFLICT,
                (broker_s, server_s, login_s, exclude_id, exclude_id, int(user_id)),
            )
            row = cur.fetchone()
            return dict(row) if row else None

        return self._store._with_retry_read(_do)

    def update_account_label(
        self,
        *,
        account_id: int,
        user_id: int,
        label: Optional[str] = None,
        sort_order: Optional[int] = None,
    ) -> Optional[dict[str, Any]]:
        """PATCH label + sort_order. Truyen None de KHONG update field do.

        Tra account row sau update, hoac None neu account khong thuoc user.
        """
        if label is None and sort_order is None:
            # No-op: just fetch
            return self.get_account(account_id=account_id, user_id=user_id)

        normalized_label = None if label is None else str(label).strip()[:120]
        normalized_sort = None if sort_order is None else int(sort_order)

        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                """
                UPDATE broker_accounts
                SET label = COALESCE(%s, label),
                    sort_order = COALESCE(%s, sort_order),
                    updated_at = NOW()
                WHERE id = %s AND user_id = %s
                RETURNING id, user_id, broker, server, login, status, label,
                          sort_order, is_active, last_error, verified_at,
                          created_at, updated_at
                """,
                (normalized_label, normalized_sort, int(account_id), int(user_id)),
            )
            row = cur.fetchone()
            return dict(row) if row else None

        return self._store._with_retry_locked(_do)

    def update_account_credentials(
        self,
        *,
        account_id: int,
        user_id: int,
        password_encrypted: str,
        force: bool = False,
    ) -> dict[str, Any]:
        """Re-key broker password mà không cần xóa + tạo lại account.

        Behavior:
          - Verify account thuộc user. Raise account_not_found nếu sai.
          - Nếu account có active deployment (running/start_requested/starting/stop_requested):
            raise cannot_update_credentials_while_active (trừ khi force=True dùng cho admin).
          - Update password_encrypted trong account_credentials_encrypted (UPSERT).
          - The next START_BOT performs the broker login inside the Windows
            runner before the bot is allowed to run.
          - Tra ve account row sau khi update.
        """
        if not isinstance(password_encrypted, str) or not password_encrypted.strip():
            raise ValueError("invalid_credentials_payload")

        def _do(con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                "SELECT id, status FROM broker_accounts WHERE id = %s AND user_id = %s FOR UPDATE",
                (int(account_id), int(user_id)),
            )
            existing = cur.fetchone()
            if not existing:
                raise ValueError("account_not_found")
            if not force:
                cur.execute(
                    """
                    SELECT 1 FROM bot_deployments
                    WHERE account_id = %s
                      AND status IN ('start_requested','starting','running','stop_requested')
                    LIMIT 1
                    """,
                    (int(account_id),),
                )
                if cur.fetchone():
                    raise ValueError("cannot_update_credentials_while_active")
            cur.execute(
                """
                INSERT INTO account_credentials_encrypted(
                    account_id, password_encrypted, metadata_json, created_at, updated_at
                )
                VALUES(%s, %s, '{}'::jsonb, NOW(), NOW())
                ON CONFLICT(account_id) DO UPDATE SET
                    password_encrypted = EXCLUDED.password_encrypted,
                    metadata_json = jsonb_set(
                        COALESCE(account_credentials_encrypted.metadata_json, '{}'::jsonb),
                        '{rotated_at}', to_jsonb(extract(epoch from NOW())::bigint), true
                    ),
                    updated_at = NOW()
                """,
                (int(account_id), password_encrypted),
            )
            cur.execute(
                """
                UPDATE broker_accounts
                SET status = 'pending_verification',
                    last_error = NULL,
                    verification_requested_at = NOW(),
                    verified_at = NULL,
                    is_active = TRUE,
                    updated_at = NOW()
                WHERE id = %s AND user_id = %s
                RETURNING id, user_id, broker, server, login, status, label, is_active,
                         last_error, verification_requested_at, verified_at, created_at, updated_at
                """,
                (int(account_id), int(user_id)),
            )
            row = cur.fetchone()
            return dict(row or {})

        return self._store._with_retry_locked(_do)

    def soft_delete_account(
        self,
        *,
        account_id: int,
        user_id: int,
        reason: str = "",
    ) -> Optional[dict[str, Any]]:
        """Soft-delete one broker account scoped to a user.

        Keeps execution/audit history, scrubs the encrypted credential blob, and
        blocks deletion while a bot is active or a START/STOP command is in
        flight. Slot release is handled by service layer after verification
        cancellation so this write stays tightly scoped to account data.
        """
        clean_reason = (reason or "account_deleted_by_user")[:200]

        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                """
                SELECT id, user_id, broker, server, login, status, label, is_active,
                       last_error, verified_at, created_at, updated_at
                FROM broker_accounts
                WHERE id = %s AND user_id = %s
                FOR UPDATE
                """,
                (int(account_id), int(user_id)),
            )
            existing = cur.fetchone()
            if not existing:
                return None

            cur.execute(
                """
                SELECT 1
                FROM bot_deployments
                WHERE account_id = %s
                  AND status = ANY(%s)
                LIMIT 1
                """,
                (int(account_id), list(ACTIVE_DEPLOYMENT_STATUSES)),
            )
            if cur.fetchone():
                raise ValueError("account_has_active_deployment")

            cur.execute(
                """
                SELECT 1
                FROM execution_commands c
                LEFT JOIN bot_deployments d ON d.id = c.deployment_id
                WHERE c.account_id = %s
                  AND c.command_type IN ('START_BOT', 'STOP_BOT')
                  AND c.delivery_status IN ('pending', 'queued', 'dispatched')
                  AND NOT (
                      d.id IS NOT NULL
                      AND d.desired_state = 'stopped'
                      AND d.status = ANY(%s)
                      AND COALESCE(d.is_active, FALSE) = FALSE
                  )
                LIMIT 1
                """,
                (int(account_id), list(_TERMINAL_DEPLOYMENT_STATUSES)),
            )
            if cur.fetchone():
                raise ValueError("start_transition_in_progress")

            cur.execute(
                """
                UPDATE broker_accounts
                SET status = 'disconnected',
                    is_active = FALSE,
                    last_error = %s,
                    verified_at = NULL,
                    updated_at = NOW()
                WHERE id = %s AND user_id = %s
                RETURNING id, user_id, broker, server, login, status, label,
                          is_active, last_error, verified_at, created_at, updated_at
                """,
                (clean_reason, int(account_id), int(user_id)),
            )
            row = dict(cur.fetchone() or {})
            cur.execute(
                """
                UPDATE account_credentials_encrypted
                SET password_encrypted = '',
                    metadata_json = jsonb_set(
                        jsonb_set(
                            COALESCE(metadata_json, '{}'::jsonb),
                            '{scrubbed}', 'true', true
                        ),
                        '{account_deleted}', 'true', true
                    ),
                    updated_at = NOW()
                WHERE account_id = %s
                """,
                (int(account_id),),
            )
            return row

        return self._store._with_retry_locked(_do)

    def verify_account(self, *, account_id: int, user_id: int, ok: bool = True, error_text: Optional[str] = None) -> dict[str, Any]:
        status = "connected" if ok else "verification_failed"
        is_active = bool(ok)
        error_s = _norm(error_text) or None

        def _do(con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                """
                UPDATE broker_accounts
                SET status = %s,
                    last_error = %s,
                    is_active = %s,
                    verified_at = CASE WHEN %s = 'connected' THEN NOW() ELSE NULL END,
                    updated_at = NOW()
                WHERE id = %s AND user_id = %s
                RETURNING id, user_id, broker, server, login, status, label, is_active, last_error, verified_at, created_at, updated_at
                """,
                (status, error_s, is_active, status, int(account_id), int(user_id)),
            )
            row = cur.fetchone()
            return dict(row or {})

        return self._store._with_retry_locked(_do)

    def mark_account_runtime_login_result(
        self,
        *,
        account_id: int,
        ok: bool,
        error_text: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        status = "connected" if ok else "verification_failed"
        error_s = _norm(error_text) or None

        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                """
                UPDATE broker_accounts
                SET status = %s,
                    last_error = %s,
                    is_active = TRUE,
                    verified_at = CASE WHEN %s = 'connected' THEN NOW() ELSE NULL END,
                    verification_requested_at = CASE
                        WHEN %s = 'connected' THEN NULL
                        ELSE COALESCE(verification_requested_at, NOW())
                    END,
                    updated_at = NOW()
                WHERE id = %s
                  AND status <> 'disconnected'
                RETURNING id, user_id, broker, server, login, status, label, is_active,
                          last_error, verified_at, verification_requested_at, created_at, updated_at
                """,
                (status, error_s, status, status, int(account_id)),
            )
            row = cur.fetchone()
            return dict(row) if row else None

        return self._store._with_retry_locked(_do)

    def get_account(self, *, account_id: int, user_id: int) -> Optional[dict[str, Any]]:
        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                self._SQL_GET_ACCOUNT,
                (int(account_id), int(user_id)),
            )
            row = cur.fetchone()
            return _decorate_account_verification_projection(dict(row), account_status_key="status") if row else None

        return self._store._with_retry_read(_do)

    def get_runner_account_bundle(self, *, account_id: int) -> Optional[dict[str, Any]]:
        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                """
                SELECT
                    a.id AS account_id,
                    a.user_id,
                    a.broker,
                    a.server,
                    a.login,
                    a.status AS account_status,
                    a.label,
                    a.last_error,
                    a.verification_requested_at AS account_verification_requested_at,
                    c.password_encrypted,
                    bind.runner_id AS sticky_runner_id,
                    bind.slot_id AS sticky_slot_id,
                    bind.binding_state,
                    ver.id AS verification_job_id,
                    ver.status AS verification_job_status,
                    ver.payload_json AS verification_payload_json,
                    ver.runner_id AS verification_runner_id,
                    ver.slot_id AS verification_slot_id,
                    ver.trace_id AS verification_trace_id,
                    ver.requested_at AS verification_requested_at,
                    ver.dispatched_at AS verification_dispatched_at,
                    ver.completed_at AS verification_completed_at,
                    dep.id AS deployment_id,
                    dep.bot_code,
                    dep.bot_name,
                    dep.profile_class,
                    dep.status AS deployment_status,
                    dep.desired_state,
                    dep.runner_id AS deployment_runner_id,
                    dep.slot_id AS deployment_slot_id,
                    dep.config_json,
                    dep.trace_id,
                    dep.health_status,
                    dep.last_heartbeat_at
                FROM broker_accounts a
                LEFT JOIN account_credentials_encrypted c ON c.account_id = a.id
                LEFT JOIN LATERAL (
                    SELECT runner_id, slot_id, binding_state
                    FROM account_slot_bindings
                    WHERE account_id = a.id
                      AND is_current = TRUE
                    ORDER BY updated_at DESC, id DESC
                    LIMIT 1
                ) bind ON TRUE
                LEFT JOIN LATERAL (
                    SELECT
                        id, status, payload_json, runner_id, slot_id, trace_id,
                        requested_at, dispatched_at, completed_at
                    FROM account_verification_jobs
                    WHERE account_id = a.id
                      AND requested_at >= COALESCE(a.verification_requested_at, a.created_at)
                    ORDER BY requested_at DESC, id DESC
                    LIMIT 1
                ) ver ON TRUE
                LEFT JOIN LATERAL (
                    SELECT
                        id, bot_code, bot_name, profile_class, status, desired_state,
                        runner_id, slot_id, config_json, trace_id, health_status, last_heartbeat_at
                    FROM bot_deployments
                    WHERE account_id = a.id
                    ORDER BY updated_at DESC, id DESC
                    LIMIT 1
                ) dep ON TRUE
                WHERE a.id = %s
                """,
                (int(account_id),),
            )
            row = cur.fetchone()
            return _decorate_account_verification_projection(
                dict(row),
                account_status_key="account_status",
                job_status_key="verification_job_status",
            ) if row else None

        return self._store._with_retry_read(_do)

    def get_runner_deployment_package(self, *, deployment_id: int) -> Optional[dict[str, Any]]:
        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                """
                SELECT
                    d.id AS deployment_id,
                    d.user_id,
                    d.account_id,
                    d.bot_code,
                    d.bot_name AS deployment_bot_name,
                    d.profile_class AS deployment_profile_class,
                    d.status AS deployment_status,
                    d.desired_state,
                    d.is_active,
                    d.runner_id AS deployment_runner_id,
                    d.slot_id AS deployment_slot_id,
                    d.binding_id,
                    d.config_json,
                    d.trace_id,
                    d.health_status,
                    d.last_error AS deployment_last_error,
                    d.last_heartbeat_at,
                    d.started_at,
                    d.stopped_at,
                    a.broker,
                    a.server,
                    a.login,
                    a.status AS account_status,
                    a.label,
                    a.last_error AS account_last_error,
                    a.risk_policy_json AS account_risk_policy,
                    c.password_encrypted,
                    bind.runner_id AS binding_runner_id,
                    bind.slot_id AS binding_slot_id,
                    bind.binding_state,
                    bind.is_sticky,
                    bind.is_current,
                    bind.last_used_at,
                    bc.bot_code AS catalog_bot_code,
                    bc.bot_name AS catalog_bot_name,
                    bc.display_name,
                    bc.language,
                    bc.version,
                    bc.profile_class AS catalog_profile_class,
                    bc.runtime_entry,
                    bc.required_params,
                    bc.risk_profile,
                    bc.indicator_requirements,
                    bc.strategy_tags,
                    bc.resource_hints,
                    bc.supports_demo,
                    bc.supports_live,
                    bc.default_config_path,
                    bc.runtime_env,
                    bc.checksum,
                    bc.source_path,
                    bc.metadata_json AS catalog_metadata
                FROM bot_deployments d
                JOIN broker_accounts a ON a.id = d.account_id
                LEFT JOIN account_credentials_encrypted c ON c.account_id = a.id
                LEFT JOIN account_slot_bindings bind ON bind.id = d.binding_id
                LEFT JOIN bot_catalog bc ON bc.bot_code = d.bot_code
                WHERE d.id = %s
                LIMIT 1
                """,
                (int(deployment_id),),
            )
            row = cur.fetchone()
            return dict(row) if row else None

        return self._store._with_retry_read(_do)

    def list_accounts_for_user(self, *, user_id: int) -> list[dict[str, Any]]:
        def _do(con: Any, cur: Any) -> list[dict[str, Any]]:
            cur.execute(
                self._SQL_LIST_ACCOUNTS_FOR_USER,
                (list(ACTIVE_DEPLOYMENT_STATUSES), int(user_id)),
            )
            return [
                _decorate_account_verification_projection(dict(row), account_status_key="status")
                for row in (cur.fetchall() or [])
            ]

        return self._store._with_retry_read(_do)
