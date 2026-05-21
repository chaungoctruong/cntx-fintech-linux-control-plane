from __future__ import annotations

from typing import Any, Optional

from app.repositories.control_plane.support import _json_payload, _norm, _norm_slot_id


_ACTIVE_LOGIN_RESERVATION_STATUSES = ("pending", "dispatched", "verified")


class ControlPlaneLoginReservationsMixin:
    def delete_old_login_reservations(self, *, retention_days: int = 30, batch_size: int = 5000) -> int:
        """Hard-delete terminal login-slot rows after the operational window.

        Login reservations are transient runner coordination records. Active
        rows stay protected by status filters; completed rows are safe to drop
        after retention because durable account/deployment state lives in the
        broker/deployment tables and final runner events.
        """

        retention_days_i = max(1, int(retention_days or 30))
        batch_size_i = max(1, min(int(batch_size or 5000), 50000))

        def _do(con: Any, cur: Any) -> int:
            cur.execute(
                """
                WITH old_rows AS (
                    SELECT id
                    FROM account_login_reservations
                    WHERE status IN ('failed', 'expired', 'released', 'claimed', 'cancelled')
                      AND COALESCE(completed_at, updated_at, requested_at) < NOW() - (%s::int * INTERVAL '1 day')
                    ORDER BY id
                    LIMIT %s
                )
                DELETE FROM account_login_reservations r
                USING old_rows
                WHERE r.id = old_rows.id
                """,
                (retention_days_i, batch_size_i),
            )
            return int(cur.rowcount or 0)

        return int(self._store._with_retry_locked(_do) or 0)

    def release_expired_login_reservations(self) -> int:
        """Expire stale login-slot holds and free their runner slots."""

        def _do(con: Any, cur: Any) -> int:
            cur.execute(
                """
                SELECT id, account_id, runner_id, slot_id, status
                FROM account_login_reservations
                WHERE status IN ('pending', 'dispatched', 'verified')
                  AND expires_at IS NOT NULL
                  AND expires_at <= NOW()
                FOR UPDATE
                """
            )
            rows = [dict(row) for row in (cur.fetchall() or [])]
            for row in rows:
                cur.execute(
                    """
                    UPDATE account_login_reservations
                    SET status = 'expired',
                        completed_at = COALESCE(completed_at, NOW()),
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (int(row["id"]),),
                )
                if str(row.get("status") or "").strip().lower() in {"pending", "dispatched"}:
                    cur.execute(
                        """
                        UPDATE broker_accounts
                        SET status = 'login_failed',
                            last_error = 'login_slot_timeout',
                            login_requested_at = NULL,
                            updated_at = NOW()
                        WHERE id = %s
                          AND status = 'pending_login'
                          AND NOT EXISTS (
                              SELECT 1
                              FROM account_login_reservations newer
                              WHERE newer.account_id = broker_accounts.id
                                AND newer.id <> %s
                                AND newer.status IN ('pending', 'dispatched', 'verified')
                                AND (newer.expires_at IS NULL OR newer.expires_at > NOW())
                          )
                        """,
                        (int(row["account_id"]), int(row["id"])),
                    )
                self._release_login_reservation_slot_locked(
                    cur,
                    account_id=int(row["account_id"]),
                    runner_id=str(row.get("runner_id") or ""),
                    slot_id=str(row.get("slot_id") or ""),
                    reason="login_reservation_expired",
                )
            return len(rows)

        return int(self._store._with_retry_locked(_do) or 0)

    def get_active_login_reservation(
        self,
        *,
        account_id: int,
        user_id: int | None = None,
    ) -> Optional[dict[str, Any]]:
        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            params: list[Any] = [int(account_id), list(_ACTIVE_LOGIN_RESERVATION_STATUSES)]
            user_sql = ""
            if user_id is not None:
                user_sql = "AND user_id = %s"
                params.append(int(user_id))
            cur.execute(
                f"""
                SELECT *
                FROM account_login_reservations
                WHERE account_id = %s
                  AND status = ANY(%s)
                  {user_sql}
                  AND (expires_at IS NULL OR expires_at > NOW())
                ORDER BY requested_at DESC, id DESC
                LIMIT 1
                """,
                tuple(params),
            )
            row = cur.fetchone()
            return dict(row) if row else None

        return self._store._with_retry_read(_do)

    def get_login_reservation_for_user(
        self,
        *,
        reservation_id: int,
        user_id: int,
    ) -> Optional[dict[str, Any]]:
        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                """
                SELECT r.*, a.status AS account_status, a.last_error AS account_last_error
                FROM account_login_reservations r
                JOIN broker_accounts a ON a.id = r.account_id
                WHERE r.id = %s
                  AND r.user_id = %s
                LIMIT 1
                """,
                (int(reservation_id), int(user_id)),
            )
            row = cur.fetchone()
            return dict(row) if row else None

        return self._store._with_retry_read(_do)

    def create_login_reservation(
        self,
        *,
        user_id: int,
        account_id: int,
        runner_id: str,
        slot_id: str,
        trace_id: str,
        ttl_sec: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        runner_id_s = _norm(runner_id)
        slot_id_s = _norm_slot_id(slot_id)
        if not runner_id_s or not slot_id_s:
            raise ValueError("login_slot_required")
        ttl_i = max(15, min(int(ttl_sec or 300), 900))

        def _do(con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                """
                SELECT *
                FROM account_login_reservations
                WHERE account_id = %s
                  AND status IN ('pending', 'dispatched', 'verified')
                  AND (expires_at IS NULL OR expires_at > NOW())
                ORDER BY requested_at DESC, id DESC
                LIMIT 1
                FOR UPDATE
                """,
                (int(account_id),),
            )
            existing = cur.fetchone()
            if existing:
                return dict(existing)
            cur.execute(
                """
                INSERT INTO account_login_reservations(
                    user_id, account_id, runner_id, slot_id, status,
                    trace_id, payload_json, requested_at, expires_at,
                    created_at, updated_at
                )
                VALUES(%s, %s, %s, %s, 'pending',
                       %s, %s::jsonb, NOW(), NOW() + (%s || ' seconds')::interval,
                       NOW(), NOW())
                RETURNING *
                """,
                (
                    int(user_id),
                    int(account_id),
                    runner_id_s,
                    slot_id_s,
                    _norm(trace_id),
                    _json_payload(payload),
                    ttl_i,
                ),
            )
            return dict(cur.fetchone() or {})

        return self._store._with_retry_locked(_do)

    def mark_login_reservation_dispatched(
        self,
        *,
        reservation_id: int,
        command_id: str,
        redis_stream_id: str | None,
        ttl_sec: int,
    ) -> Optional[dict[str, Any]]:
        ttl_i = max(15, min(int(ttl_sec or 300), 900))

        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                """
                UPDATE account_login_reservations
                SET status = 'dispatched',
                    command_id = %s,
                    redis_stream_id = COALESCE(%s, redis_stream_id),
                    dispatched_at = COALESCE(dispatched_at, NOW()),
                    expires_at = GREATEST(
                        COALESCE(expires_at, NOW()),
                        NOW() + (%s || ' seconds')::interval
                    ),
                    updated_at = NOW()
                WHERE id = %s
                  AND status IN ('pending', 'dispatched')
                RETURNING *
                """,
                (_norm(command_id), _norm(redis_stream_id) or None, ttl_i, int(reservation_id)),
            )
            row = cur.fetchone()
            return dict(row) if row else None

        return self._store._with_retry_locked(_do)

    def claim_verified_login_reservation(self, *, account_id: int) -> Optional[dict[str, Any]]:
        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                """
                SELECT *
                FROM account_login_reservations
                WHERE account_id = %s
                  AND status = 'verified'
                  AND expires_at > NOW()
                ORDER BY completed_at DESC NULLS LAST, id DESC
                LIMIT 1
                FOR UPDATE
                """,
                (int(account_id),),
            )
            row = cur.fetchone()
            if not row:
                return None
            reservation = dict(row)
            cur.execute(
                """
                UPDATE account_login_reservations
                SET status = 'claimed',
                    updated_at = NOW()
                WHERE id = %s
                RETURNING *
                """,
                (int(reservation["id"]),),
            )
            return dict(cur.fetchone() or reservation)

        return self._store._with_retry_locked(_do)

    def complete_login_reservation(
        self,
        *,
        reservation_id: int | None = None,
        command_id: str | None = None,
        ok: bool,
        runner_id: str | None = None,
        slot_id: str | None = None,
        error_text: str | None = None,
        payload: dict[str, Any] | None = None,
        ttl_sec: int = 300,
    ) -> Optional[dict[str, Any]]:
        payload_map = dict(payload or {})
        ttl_i = max(15, min(int(ttl_sec or 300), 900))
        error_s = (_norm(error_text) or _norm(payload_map.get("error")) or _norm(payload_map.get("reason")))[:500] or None

        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            if reservation_id is not None:
                cur.execute(
                    """
                    SELECT *
                    FROM account_login_reservations
                    WHERE id = %s
                    FOR UPDATE
                    """,
                    (int(reservation_id),),
                )
            else:
                cur.execute(
                    """
                    SELECT *
                    FROM account_login_reservations
                    WHERE command_id = %s
                    ORDER BY requested_at DESC, id DESC
                    LIMIT 1
                    FOR UPDATE
                    """,
                    (_norm(command_id),),
                )
            row = cur.fetchone()
            if not row:
                return None
            reservation = dict(row)
            current_status = str(reservation.get("status") or "").strip().lower()
            if current_status in {"failed", "expired", "released", "claimed", "cancelled"}:
                return reservation
            account_id = int(reservation["account_id"])
            runner_id_s = _norm(runner_id) or _norm(reservation.get("runner_id"))
            slot_id_s = _norm_slot_id(slot_id) or _norm_slot_id(reservation.get("slot_id"))
            status = "verified" if ok else "failed"
            cur.execute(
                """
                UPDATE account_login_reservations
                SET status = %s,
                    runner_id = %s,
                    slot_id = %s,
                    last_error = %s,
                    payload_json = COALESCE(payload_json, '{}'::jsonb) || %s::jsonb,
                    completed_at = COALESCE(completed_at, NOW()),
                    expires_at = CASE WHEN %s THEN NOW() + (%s || ' seconds')::interval ELSE NULL END,
                    updated_at = NOW()
                WHERE id = %s
                RETURNING *
                """,
                (
                    status,
                    runner_id_s,
                    slot_id_s,
                    error_s,
                    _json_payload(payload_map),
                    bool(ok),
                    ttl_i,
                    int(reservation["id"]),
                ),
            )
            updated = dict(cur.fetchone() or reservation)
            if ok:
                cur.execute(
                    """
                    UPDATE broker_accounts
                    SET status = 'connected',
                        is_active = TRUE,
                        last_error = NULL,
                        verified_at = NOW(),
                        login_requested_at = NULL,
                        updated_at = NOW()
                    WHERE id = %s
                      AND status <> 'disconnected'
                    """,
                    (account_id,),
                )
                cur.execute(
                    """
                    UPDATE runner_slots
                    SET current_account_id = %s,
                        status = CASE WHEN status = 'broken' THEN status ELSE 'allocated' END,
                        metadata_json = jsonb_strip_nulls(
                            COALESCE(metadata_json, '{}'::jsonb)
                            || jsonb_build_object(
                                'account_id', %s,
                                'active_account_id', %s,
                                'login_slot_account_id', %s,
                                'login_reservation_id', %s,
                                'login_slot_status', 'verified',
                                'available_for_new_account', FALSE,
                                'control_plane_state', 'allocated',
                                'current_control_plane_state', 'allocated',
                                'last_reason', 'login_slot_verified',
                                'last_error', ''
                            )
                        ),
                        updated_at = NOW()
                    WHERE runner_id = %s
                      AND slot_id = %s
                    """,
                    (
                        account_id,
                        str(account_id),
                        str(account_id),
                        str(account_id),
                        str(updated.get("id") or reservation["id"]),
                        runner_id_s,
                        slot_id_s,
                    ),
                )
            else:
                cur.execute(
                    """
                    UPDATE broker_accounts
                    SET status = 'login_failed',
                        is_active = TRUE,
                        last_error = %s,
                        verified_at = NULL,
                        login_requested_at = NULL,
                        updated_at = NOW()
                    WHERE id = %s
                      AND status <> 'disconnected'
                    """,
                    (error_s or "login_slot_failed", account_id),
                )
                self._release_login_reservation_slot_locked(
                    cur,
                    account_id=account_id,
                    runner_id=runner_id_s,
                    slot_id=slot_id_s,
                    reason=error_s or "login_slot_failed",
                )
            return updated

        return self._store._with_retry_locked(_do)

    def release_login_reservation(
        self,
        *,
        account_id: int,
        reason: str,
    ) -> int:
        clean_reason = (_norm(reason) or "login_reservation_released")[:200]

        def _do(con: Any, cur: Any) -> int:
            cur.execute(
                """
                SELECT id, runner_id, slot_id
                FROM account_login_reservations
                WHERE account_id = %s
                  AND status IN ('pending', 'dispatched', 'verified')
                FOR UPDATE
                """,
                (int(account_id),),
            )
            rows = [dict(row) for row in (cur.fetchall() or [])]
            for row in rows:
                cur.execute(
                    """
                    UPDATE account_login_reservations
                    SET status = 'released',
                        last_error = %s,
                        completed_at = COALESCE(completed_at, NOW()),
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (clean_reason, int(row["id"])),
                )
                self._release_login_reservation_slot_locked(
                    cur,
                    account_id=int(account_id),
                    runner_id=str(row.get("runner_id") or ""),
                    slot_id=str(row.get("slot_id") or ""),
                    reason=clean_reason,
                )
            return len(rows)

        return int(self._store._with_retry_locked(_do) or 0)

    def release_login_reservation_by_id(
        self,
        *,
        reservation_id: int,
        account_id: int,
        reason: str,
    ) -> int:
        clean_reason = (_norm(reason) or "login_reservation_released")[:200]

        def _do(con: Any, cur: Any) -> int:
            cur.execute(
                """
                SELECT id, runner_id, slot_id
                FROM account_login_reservations
                WHERE id = %s
                  AND account_id = %s
                  AND status IN ('pending', 'dispatched', 'verified')
                FOR UPDATE
                """,
                (int(reservation_id), int(account_id)),
            )
            row = dict(cur.fetchone() or {})
            if not row:
                return 0
            cur.execute(
                """
                UPDATE account_login_reservations
                SET status = 'released',
                    last_error = %s,
                    completed_at = COALESCE(completed_at, NOW()),
                    updated_at = NOW()
                WHERE id = %s
                """,
                (clean_reason, int(row["id"])),
            )
            self._release_login_reservation_slot_locked(
                cur,
                account_id=int(account_id),
                runner_id=str(row.get("runner_id") or ""),
                slot_id=str(row.get("slot_id") or ""),
                reason=clean_reason,
            )
            return 1

        return int(self._store._with_retry_locked(_do) or 0)

    def release_claimed_login_reservation(
        self,
        *,
        reservation_id: int,
        account_id: int,
        reason: str,
    ) -> int:
        clean_reason = (_norm(reason) or "claimed_login_reservation_released")[:200]

        def _do(con: Any, cur: Any) -> int:
            cur.execute(
                """
                SELECT id, runner_id, slot_id
                FROM account_login_reservations
                WHERE id = %s
                  AND account_id = %s
                  AND status = 'claimed'
                FOR UPDATE
                """,
                (int(reservation_id), int(account_id)),
            )
            row = dict(cur.fetchone() or {})
            if not row:
                return 0
            cur.execute(
                """
                UPDATE account_login_reservations
                SET status = 'released',
                    last_error = %s,
                    completed_at = COALESCE(completed_at, NOW()),
                    updated_at = NOW()
                WHERE id = %s
                """,
                (clean_reason, int(row["id"])),
            )
            self._release_login_reservation_slot_locked(
                cur,
                account_id=int(account_id),
                runner_id=str(row.get("runner_id") or ""),
                slot_id=str(row.get("slot_id") or ""),
                reason=clean_reason,
            )
            return 1

        return int(self._store._with_retry_locked(_do) or 0)

    def _release_login_reservation_slot_locked(
        self,
        cur: Any,
        *,
        account_id: int,
        runner_id: str,
        slot_id: str,
        reason: str,
    ) -> None:
        runner_id_s = _norm(runner_id)
        slot_id_s = _norm_slot_id(slot_id)
        if not runner_id_s or not slot_id_s:
            return
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
                        - 'login_slot_account_id'
                        - 'login_reservation_id'
                        - 'login_slot_status'
                        - 'sticky_account_id'
                        - 'reserved_account_id'
                        - 'current_control_plane_state'
                        - 'control_plane_state'
                        - 'runner_state'
                        - 'current_runner_state'
                        - 'last_error'
                    ) || jsonb_build_object(
                        'available_for_new_account', TRUE,
                        'control_plane_state', 'ready',
                        'current_control_plane_state', 'ready',
                        'runner_state', 'ready',
                        'current_runner_state', 'ready',
                        'last_reason', %s,
                        'last_error', ''
                    )
                ),
                updated_at = NOW()
            WHERE runner_id = %s
              AND slot_id = %s
              AND (current_account_id IS NULL OR current_account_id = %s)
            """,
            (_norm(reason)[:200], runner_id_s, slot_id_s, int(account_id)),
        )
        cur.execute(
            """
            UPDATE account_slot_bindings
            SET binding_state = 'released',
                is_current = FALSE,
                is_sticky = FALSE,
                updated_at = NOW()
            WHERE account_id = %s
              AND runner_id = %s
              AND slot_id = %s
              AND is_current = TRUE
            """,
            (int(account_id), runner_id_s, slot_id_s),
        )
