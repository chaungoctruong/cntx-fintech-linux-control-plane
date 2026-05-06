from __future__ import annotations

from typing import Any, Optional

from app.repositories.control_plane.support import _norm


class ControlPlaneUserMixin:
    def ensure_user(self, *, telegram_id: str, username: Optional[str]) -> dict[str, Any]:
        tg = _norm(telegram_id)
        if not tg:
            raise ValueError("telegram_id_required")
        un = _norm(username) or None

        def _do(con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                """
                INSERT INTO users(telegram_id, username, created_at, updated_at)
                VALUES(%s, %s, NOW(), NOW())
                ON CONFLICT(telegram_id) DO UPDATE SET
                    username = COALESCE(EXCLUDED.username, users.username),
                    updated_at = NOW()
                RETURNING id, telegram_id, username, created_at, updated_at
                """,
                (tg, un),
            )
            return dict(cur.fetchone() or {})

        return self._store._with_retry_locked(_do)

    def get_user_runtime_summary(self, telegram_id: str) -> dict[str, Any]:
        tg = _norm(telegram_id)
        if not tg:
            return {
                "telegram_id": "",
                "linked_accounts": 0,
                "running_accounts": 0,
                "last_activity_ts": 0,
                "balance": 0.0,
                "equity": 0.0,
            }

        def _do(con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                """
                SELECT
                    u.id AS user_id,
                    u.telegram_id,
                    COALESCE(acc.connected_accounts, 0) AS linked_accounts,
                    COALESCE(dep.running_accounts, 0) AS running_accounts,
                    COALESCE(snap.total_balance, 0) AS balance,
                    COALESCE(snap.total_equity, 0) AS equity,
                    EXTRACT(
                        EPOCH FROM GREATEST(
                            COALESCE(acc.last_account_at, TO_TIMESTAMP(0)),
                            COALESCE(dep.last_deployment_at, TO_TIMESTAMP(0)),
                            COALESCE(snap.last_snapshot_at, TO_TIMESTAMP(0)),
                            COALESCE(u.updated_at, TO_TIMESTAMP(0))
                        )
                    )::BIGINT AS last_activity_ts
                FROM users u
                LEFT JOIN LATERAL (
                    SELECT
                        COUNT(*) FILTER (
                            WHERE a.is_active = TRUE
                              AND (
                                  a.verified_at IS NOT NULL
                                  OR COALESCE(LOWER(a.status), '') = 'connected'
                              )
                        ) AS connected_accounts,
                        MAX(a.updated_at) AS last_account_at
                    FROM broker_accounts a
                    WHERE a.user_id = u.id
                ) acc ON TRUE
                LEFT JOIN LATERAL (
                    SELECT
                        COUNT(DISTINCT d.account_id) FILTER (
                            WHERE d.desired_state = 'running'
                              AND d.status IN ('start_requested', 'starting', 'running', 'stop_requested')
                        ) AS running_accounts,
                        MAX(COALESCE(d.last_heartbeat_at, d.updated_at, d.created_at)) AS last_deployment_at
                    FROM bot_deployments d
                    WHERE d.user_id = u.id
                ) dep ON TRUE
                LEFT JOIN LATERAL (
                    SELECT
                        COALESCE(SUM(COALESCE(s.balance, 0)), 0) AS total_balance,
                        COALESCE(SUM(COALESCE(s.equity, 0)), 0) AS total_equity,
                        MAX(COALESCE(s.updated_at, s.heartbeat_at)) AS last_snapshot_at
                    FROM account_state_snapshots s
                    JOIN broker_accounts a2 ON a2.id = s.account_id
                    WHERE a2.user_id = u.id
                ) snap ON TRUE
                WHERE u.telegram_id = %s
                LIMIT 1
                """,
                (tg,),
            )
            row = cur.fetchone()
            return dict(row) if row else {}

        summary = self._store._with_retry_read(_do)
        if summary:
            return summary
        return {
            "telegram_id": tg,
            "linked_accounts": 0,
            "running_accounts": 0,
            "last_activity_ts": 0,
            "balance": 0.0,
            "equity": 0.0,
        }

    def list_user_runtime_summaries(self) -> list[dict[str, Any]]:
        def _do(con: Any, cur: Any) -> list[dict[str, Any]]:
            cur.execute(
                """
                SELECT
                    u.id AS user_id,
                    u.telegram_id,
                    COALESCE(acc.connected_accounts, 0) AS linked_accounts,
                    COALESCE(dep.running_accounts, 0) AS running_accounts,
                    EXTRACT(
                        EPOCH FROM GREATEST(
                            COALESCE(acc.last_account_at, TO_TIMESTAMP(0)),
                            COALESCE(dep.last_deployment_at, TO_TIMESTAMP(0)),
                            COALESCE(snap.last_snapshot_at, TO_TIMESTAMP(0)),
                            COALESCE(u.updated_at, TO_TIMESTAMP(0))
                        )
                    )::BIGINT AS last_activity_ts
                FROM users u
                LEFT JOIN LATERAL (
                    SELECT
                        COUNT(*) FILTER (
                            WHERE a.is_active = TRUE
                              AND (
                                  a.verified_at IS NOT NULL
                                  OR COALESCE(LOWER(a.status), '') = 'connected'
                              )
                        ) AS connected_accounts,
                        MAX(a.updated_at) AS last_account_at
                    FROM broker_accounts a
                    WHERE a.user_id = u.id
                ) acc ON TRUE
                LEFT JOIN LATERAL (
                    SELECT
                        COUNT(DISTINCT d.account_id) FILTER (
                            WHERE d.desired_state = 'running'
                              AND d.status IN ('start_requested', 'starting', 'running', 'stop_requested')
                        ) AS running_accounts,
                        MAX(COALESCE(d.last_heartbeat_at, d.updated_at, d.created_at)) AS last_deployment_at
                    FROM bot_deployments d
                    WHERE d.user_id = u.id
                ) dep ON TRUE
                LEFT JOIN LATERAL (
                    SELECT MAX(COALESCE(s.updated_at, s.heartbeat_at)) AS last_snapshot_at
                    FROM account_state_snapshots s
                    JOIN broker_accounts a2 ON a2.id = s.account_id
                    WHERE a2.user_id = u.id
                ) snap ON TRUE
                WHERE COALESCE(acc.connected_accounts, 0) > 0
                   OR COALESCE(dep.running_accounts, 0) > 0
                   OR COALESCE(snap.last_snapshot_at, TO_TIMESTAMP(0)) > TO_TIMESTAMP(0)
                ORDER BY last_activity_ts DESC, u.telegram_id ASC
                """
            )
            return [dict(row) for row in (cur.fetchall() or [])]

        return self._store._with_retry_read(_do)
