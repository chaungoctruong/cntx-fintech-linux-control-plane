from __future__ import annotations

import uuid
from typing import Any, Optional

from app.models.control_plane import ACTIVE_DEPLOYMENT_STATUSES
from app.repositories.control_plane.query_loader import load_sql
from app.repositories.control_plane.support import (
    _VERIFICATION_CREDENTIAL_ERROR_CODES,
    _decorate_verification_job_row,
    _derive_verification_state,
    _derive_verification_ui_state,
    _json_payload,
    _norm,
    _norm_slot_id,
    _norm_verification_error_code,
    _verification_failure_metadata,
)


class ControlPlaneVerificationMixin:
    _SQL_GET_ACTIVE_ACCOUNT_VERIFICATION_JOB = load_sql("verification/get_active_account_verification_job.sql")
    _SQL_GET_ACCOUNT_VERIFICATION_JOB_BY_ID = load_sql("verification/get_account_verification_job_by_id.sql")
    _SQL_GET_ACCOUNT_VERIFICATION_JOB_FOR_USER = load_sql("verification/get_account_verification_job_for_user.sql")

    def _build_verification_result_row_locked(self, cur: Any, *, job_row: dict[str, Any]) -> dict[str, Any]:
        cur.execute(
            """
            SELECT id, status, is_active, last_error, verified_at, updated_at
            FROM broker_accounts
            WHERE id = %s
            LIMIT 1
            """,
            (int(job_row["account_id"]),),
        )
        account = dict(cur.fetchone() or {})
        result = _decorate_verification_job_row(dict(job_row)) or dict(job_row)
        result["account"] = account
        result["account_status"] = account.get("status")
        result["verification_state"] = _derive_verification_state(
            account_status=account.get("status"),
            job_status=result.get("status"),
        )
        result["verification_ui_state"] = _derive_verification_ui_state(result)
        return result

    def get_active_account_verification_job(self, *, account_id: int) -> Optional[dict[str, Any]]:
        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                self._SQL_GET_ACTIVE_ACCOUNT_VERIFICATION_JOB,
                (int(account_id),),
            )
            row = cur.fetchone()
            return _decorate_verification_job_row(dict(row)) if row else None

        return self._store._with_retry_read(_do)

    def get_account_verification_job_by_id(self, *, job_id: int) -> Optional[dict[str, Any]]:
        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                self._SQL_GET_ACCOUNT_VERIFICATION_JOB_BY_ID,
                (int(job_id),),
            )
            row = cur.fetchone()
            return _decorate_verification_job_row(dict(row)) if row else None

        return self._store._with_retry_read(_do)

    def get_account_verification_job_for_user(self, *, job_id: int, user_id: int) -> Optional[dict[str, Any]]:
        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                self._SQL_GET_ACCOUNT_VERIFICATION_JOB_FOR_USER,
                (int(job_id), int(user_id)),
            )
            row = cur.fetchone()
            return _decorate_verification_job_row(dict(row)) if row else None

        return self._store._with_retry_read(_do)

    def create_account_verification_job(
        self,
        *,
        user_id: int,
        account_id: int,
        runner_id: Optional[str] = None,
        slot_id: Optional[str] = None,
        payload: dict[str, Any],
        trace_id: str,
        slot_candidates: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        trace_id_s = _norm(trace_id) or uuid.uuid4().hex
        payload_map = dict(payload or {})
        normalized_candidates: list[dict[str, Any]] = []
        seen_candidates: set[tuple[str, str]] = set()
        for item in (slot_candidates or []):
            runner_id_item = _norm((item or {}).get("runner_id"))
            slot_id_item = _norm_slot_id((item or {}).get("slot_id"))
            if not runner_id_item or not slot_id_item:
                continue
            key = (runner_id_item, slot_id_item)
            if key in seen_candidates:
                continue
            normalized_candidates.append(
                {
                    "runner_id": runner_id_item,
                    "slot_id": slot_id_item,
                    "reason": _norm((item or {}).get("reason")) or "selected_best_available_slot",
                    "sticky_reused": bool((item or {}).get("sticky_reused")),
                }
            )
            seen_candidates.add(key)

        def _do(con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                """
                UPDATE broker_accounts
                SET status = 'pending_verification',
                    last_error = NULL,
                    verified_at = NULL,
                    is_active = TRUE,
                    verification_requested_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s AND user_id = %s
                RETURNING id
                """,
                (int(account_id), int(user_id)),
            )
            if not cur.fetchone():
                raise ValueError("account_not_found")
            cur.execute(
                """
                INSERT INTO account_verification_jobs(
                    user_id, account_id, runner_id, slot_id, status, last_error,
                    trace_id, redis_stream_id, payload_json,
                    requested_at, dispatched_at, completed_at, created_at, updated_at
                )
                VALUES(
                    %s, %s, %s, %s, 'pending', NULL,
                    %s, NULL, %s::jsonb,
                    NOW(), NULL, NULL, NOW(), NOW()
                )
                ON CONFLICT(account_id)
                WHERE status IN ('pending', 'dispatched')
                DO UPDATE SET
                    updated_at = account_verification_jobs.updated_at
                RETURNING *
                """,
                (
                    int(user_id),
                    int(account_id),
                    _norm(runner_id) or None,
                    _norm_slot_id(slot_id) or None,
                    trace_id_s,
                    _json_payload(payload_map),
                ),
            )
            job = dict(cur.fetchone() or {})
            if not job:
                raise ValueError("verification_job_not_found")
            if _norm(job.get("trace_id")) != trace_id_s or not normalized_candidates:
                return _decorate_verification_job_row(job) or {}

            candidate = normalized_candidates[0]
            assigned_payload = {
                **payload_map,
                "account_id": int(account_id),
                "runner_id": candidate["runner_id"],
                "slot_id": candidate["slot_id"],
                "scheduler_reason": candidate["reason"],
                "sticky_reused": bool(candidate["sticky_reused"]),
                "verification_lane_contract": "session0_lane",
            }
            cur.execute(
                """
                UPDATE account_verification_jobs
                SET runner_id = %s,
                    slot_id = %s,
                    payload_json = %s::jsonb,
                    updated_at = NOW()
                WHERE id = %s
                RETURNING *
                """,
                (
                    candidate["runner_id"],
                    candidate["slot_id"],
                    _json_payload(assigned_payload),
                    int(job["id"]),
                ),
            )
            return _decorate_verification_job_row(dict(cur.fetchone() or {})) or {}

        return self._store._with_retry_locked(_do)

    def mark_account_verification_dispatched(
        self,
        *,
        job_id: int,
        runner_id: str,
        slot_id: str,
        redis_stream_id: Optional[str],
    ) -> Optional[dict[str, Any]]:
        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                """
                SELECT *
                FROM account_verification_jobs
                WHERE id = %s
                FOR UPDATE
                """,
                (int(job_id),),
            )
            existing = cur.fetchone()
            if not existing:
                return None
            existing_row = dict(existing)
            if _norm(existing_row.get("status")).lower() in {"verified", "failed", "cancelled"}:
                return _decorate_verification_job_row(existing_row)
            cur.execute(
                """
                UPDATE account_verification_jobs
                SET status = CASE
                        WHEN status = 'pending' THEN 'dispatched'
                        ELSE status
                    END,
                    runner_id = COALESCE(%s, runner_id),
                    slot_id = COALESCE(%s, slot_id),
                    redis_stream_id = COALESCE(%s, redis_stream_id),
                    dispatched_at = COALESCE(dispatched_at, NOW()),
                    updated_at = NOW()
                WHERE id = %s
                RETURNING *
                """,
                (_norm(runner_id) or None, _norm_slot_id(slot_id) or None, _norm(redis_stream_id) or None, int(job_id)),
            )
            row = cur.fetchone()
            return _decorate_verification_job_row(dict(row)) if row else None

        return self._store._with_retry_locked(_do)

    def complete_account_verification_job(
        self,
        *,
        job_id: int,
        ok: bool,
        error_text: Optional[str],
        runner_id: Optional[str],
        slot_id: Optional[str],
        payload: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        error_s = _norm(error_text) or None
        payload_map = payload if isinstance(payload, dict) else {}
        failure = _verification_failure_metadata(error_text=error_text, payload=payload_map)
        failure_code = _norm_verification_error_code((payload_map or {}).get("error_code")) or _norm(failure.get("error_code"))
        status = "verified" if ok else "failed"
        if ok:
            account_status = "connected"
            account_is_active = True
            account_last_error = None
        elif failure_code in _VERIFICATION_CREDENTIAL_ERROR_CODES:
            account_status = "disconnected"
            account_is_active = False
            account_last_error = failure_code
        else:
            account_status = "verification_failed"
            account_is_active = False
            account_last_error = failure_code or error_s
        payload_json = _json_payload(payload)

        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                """
                SELECT *
                FROM account_verification_jobs
                WHERE id = %s
                FOR UPDATE
                """,
                (int(job_id),),
            )
            existing = cur.fetchone()
            if not existing:
                return None
            existing_row = dict(existing)
            if _norm(existing_row.get("status")).lower() in {"verified", "failed", "cancelled"}:
                return self._build_verification_result_row_locked(cur, job_row=existing_row)

            cur.execute(
                """
                UPDATE account_verification_jobs
                SET status = %s,
                    last_error = %s,
                    runner_id = COALESCE(%s, runner_id),
                    slot_id = COALESCE(%s, slot_id),
                    payload_json = CASE
                        WHEN %s::jsonb = '{}'::jsonb THEN payload_json
                        ELSE payload_json || %s::jsonb
                    END,
                    completed_at = COALESCE(completed_at, NOW()),
                    updated_at = NOW()
                WHERE id = %s
                RETURNING *
                """,
                (
                    status,
                    error_s,
                    _norm(runner_id) or None,
                    _norm_slot_id(slot_id) or None,
                    payload_json,
                    payload_json,
                    int(job_id),
                ),
            )
            job = cur.fetchone()
            if not job:
                return None
            job_row = dict(job)
            cur.execute(
                """
                UPDATE broker_accounts
                SET status = %s,
                    last_error = %s,
                    is_active = %s,
                    verified_at = CASE
                        WHEN %s = 'connected' THEN COALESCE(verified_at, NOW())
                        ELSE NULL
                    END,
                    updated_at = NOW()
                WHERE id = %s
                RETURNING id, status, is_active, last_error, verified_at, updated_at
                """,
                (
                    account_status,
                    account_last_error,
                    bool(account_is_active),
                    account_status,
                    int(job_row["account_id"]),
                ),
            )
            actual_runner_id = _norm(runner_id) or _norm(job_row.get("runner_id"))
            actual_slot_id = _norm_slot_id(slot_id) or _norm_slot_id(job_row.get("slot_id"))
            if actual_runner_id and actual_slot_id:
                keep_sticky = account_status == "connected"
                cur.execute(
                    """
                    UPDATE runner_slots
                    SET current_account_id = NULL,
                        status = CASE WHEN status = 'broken' THEN status ELSE 'ready' END,
                        updated_at = NOW()
                    WHERE runner_id = %s AND slot_id = %s
                      AND (current_account_id IS NULL OR current_account_id = %s)
                    """,
                    (actual_runner_id, actual_slot_id, int(job_row["account_id"])),
                )
                cur.execute(
                    """
                    UPDATE account_slot_bindings
                    SET binding_state = CASE
                            WHEN binding_state = 'broken' THEN binding_state
                            WHEN %s THEN 'sticky'
                            ELSE 'released'
                        END,
                        is_sticky = CASE
                            WHEN binding_state = 'broken' THEN is_sticky
                            ELSE %s
                        END,
                        is_current = CASE
                            WHEN binding_state = 'broken' THEN is_current
                            ELSE %s
                        END,
                        last_used_at = NOW(),
                        updated_at = NOW()
                    WHERE account_id = %s
                      AND runner_id = %s
                      AND slot_id = %s
                      AND is_current = TRUE
                    """,
                    (
                        keep_sticky,
                        keep_sticky,
                        keep_sticky,
                        int(job_row["account_id"]),
                        actual_runner_id,
                        actual_slot_id,
                    ),
                )
            return self._build_verification_result_row_locked(cur, job_row=job_row)

        return self._store._with_retry_locked(_do)

    def cancel_account_verification_job(
        self,
        *,
        job_id: int,
        user_id: int,
        reason: Optional[str] = None,
    ) -> dict[str, Any]:
        """Huy 1 verification job dang active (pending/dispatched).

        Tra ve dict voi key:
          - "status": "cancelled" / "already_completed" / "not_found"
          - "job": row sau khi update (neu cancelled hoac already_completed)
          - "previous_status": status truoc cancel (de caller phat skip signal hop ly)

        Free luon slot binding tam thoi neu da gan runner/slot.
        """
        cancel_reason = _norm(reason) or "cancelled_by_user"

        def _do(con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                """
                SELECT *
                FROM account_verification_jobs
                WHERE id = %s
                  AND user_id = %s
                FOR UPDATE
                """,
                (int(job_id), int(user_id)),
            )
            existing = cur.fetchone()
            if not existing:
                return {"status": "not_found", "job": None, "previous_status": None}

            existing_row = dict(existing)
            previous_status = _norm(existing_row.get("status")).lower()

            if previous_status in {"verified", "failed", "cancelled"}:
                decorated = _decorate_verification_job_row(existing_row)
                return {
                    "status": "already_completed",
                    "job": decorated,
                    "previous_status": previous_status,
                }

            cur.execute(
                """
                UPDATE account_verification_jobs
                SET status = 'cancelled',
                    last_error = %s,
                    completed_at = COALESCE(completed_at, NOW()),
                    updated_at = NOW()
                WHERE id = %s
                RETURNING *
                """,
                (cancel_reason, int(job_id)),
            )
            row = cur.fetchone()
            if not row:
                return {"status": "not_found", "job": None, "previous_status": previous_status}

            job_row = dict(row)
            actual_runner_id = _norm(job_row.get("runner_id"))
            actual_slot_id = _norm_slot_id(job_row.get("slot_id"))
            if actual_runner_id and actual_slot_id:
                cur.execute(
                    """
                    UPDATE runner_slots
                    SET current_account_id = NULL,
                        status = CASE WHEN status = 'broken' THEN status ELSE 'ready' END,
                        updated_at = NOW()
                    WHERE runner_id = %s
                      AND slot_id = %s
                      AND (current_account_id IS NULL OR current_account_id = %s)
                    """,
                    (actual_runner_id, actual_slot_id, int(job_row["account_id"])),
                )
                cur.execute(
                    """
                    UPDATE account_slot_bindings
                    SET binding_state = CASE
                            WHEN binding_state = 'broken' THEN binding_state
                            ELSE 'released'
                        END,
                        is_sticky = FALSE,
                        is_current = FALSE,
                        last_used_at = NOW(),
                        updated_at = NOW()
                    WHERE account_id = %s
                      AND runner_id = %s
                      AND slot_id = %s
                      AND is_current = TRUE
                    """,
                    (int(job_row["account_id"]), actual_runner_id, actual_slot_id),
                )

            decorated = _decorate_verification_job_row(job_row)
            return {
                "status": "cancelled",
                "job": decorated,
                "previous_status": previous_status,
            }

        return self._store._with_retry_locked(_do)

    def list_account_verification_jobs(self, *, account_id: int, user_id: int, limit: int = 50) -> list[dict[str, Any]]:
        limit_i = max(1, min(int(limit), 500))

        def _do(con: Any, cur: Any) -> list[dict[str, Any]]:
            cur.execute(
                """
                SELECT
                    id,
                    user_id,
                    account_id,
                    runner_id,
                    slot_id,
                    status,
                    last_error,
                    trace_id,
                    redis_stream_id,
                    payload_json,
                    requested_at,
                    dispatched_at,
                    completed_at,
                    created_at,
                    updated_at
                FROM account_verification_jobs
                WHERE account_id = %s
                  AND user_id = %s
                ORDER BY requested_at DESC, id DESC
                LIMIT %s
                """,
                (int(account_id), int(user_id), limit_i),
            )
            return [
                _decorate_verification_job_row(dict(row))
                for row in (cur.fetchall() or [])
            ]

        return self._store._with_retry_read(_do)

    def list_active_verification_job_ids_for_account(
        self,
        *,
        account_id: int,
        user_id: int,
        limit: int = 200,
    ) -> list[int]:
        """Liet ke job_id active (pending|dispatched) cua mot account thuoc 1 user.

        Dung de bulk-cancel: caller loop qua list nay va goi
        `cancel_account_verification_job` cho tung id (re-use logic free slot binding).
        """
        limit_i = max(1, min(int(limit), 1000))

        def _do(con: Any, cur: Any) -> list[int]:
            cur.execute(
                """
                SELECT id
                FROM account_verification_jobs
                WHERE account_id = %s
                  AND user_id = %s
                  AND status IN ('pending', 'dispatched')
                ORDER BY requested_at ASC, id ASC
                LIMIT %s
                """,
                (int(account_id), int(user_id), limit_i),
            )
            return [int(row[0]) for row in (cur.fetchall() or [])]

        return self._store._with_retry_read(_do)

    def list_replayable_account_verification_jobs(
        self,
        *,
        limit: int = 100,
        runner_id: Optional[str] = None,
        statuses: Optional[list[str]] = None,
        require_missing_stream: bool = True,
        older_than_sec: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        limit_i = max(1, min(int(limit), 1000))
        statuses_s = [
            str(item or "").strip().lower()
            for item in (statuses or ["pending"])
            if str(item or "").strip()
        ] or ["pending"]
        runner_id_s = _norm(runner_id) or None
        older_than_i = max(0, int(older_than_sec or 0))

        def _do(con: Any, cur: Any) -> list[dict[str, Any]]:
            sql = [
                """
                SELECT
                    id,
                    user_id,
                    account_id,
                    runner_id,
                    slot_id,
                    status,
                    last_error,
                    trace_id,
                    redis_stream_id,
                    payload_json,
                    requested_at,
                    dispatched_at,
                    completed_at,
                    created_at,
                    updated_at
                FROM account_verification_jobs
                WHERE LOWER(status) = ANY(%s)
                """
            ]
            params: list[Any] = [statuses_s]
            if require_missing_stream:
                sql.append("AND COALESCE(redis_stream_id, '') = ''")
            sql.append("AND completed_at IS NULL")
            if runner_id_s:
                sql.append("AND runner_id = %s")
                params.append(runner_id_s)
            if older_than_i > 0:
                sql.append("AND updated_at < (NOW() - (%s * INTERVAL '1 second'))")
                params.append(older_than_i)
            sql.append("ORDER BY requested_at ASC, id ASC LIMIT %s")
            params.append(limit_i)
            cur.execute("\n".join(sql), tuple(params))
            return [dict(row) for row in (cur.fetchall() or [])]

        return self._store._with_retry_read(_do)

