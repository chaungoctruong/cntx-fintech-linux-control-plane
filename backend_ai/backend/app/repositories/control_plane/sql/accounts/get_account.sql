SELECT
    a.id,
    a.user_id,
    a.broker,
    a.server,
    a.login,
    a.status,
    a.label,
    a.is_active,
    a.risk_policy_json AS account_risk_policy,
    a.last_error,
    a.verified_at,
    a.login_requested_at,
    a.created_at,
    a.updated_at,
    r.id AS login_reservation_id,
    r.status AS login_reservation_status,
    r.payload_json AS login_reservation_payload_json,
    r.runner_id AS login_reservation_runner_id,
    r.slot_id AS login_reservation_slot_id,
    r.trace_id AS login_reservation_trace_id,
    r.requested_at AS login_reservation_requested_at,
    r.dispatched_at AS login_reservation_dispatched_at,
    r.completed_at AS login_reservation_completed_at,
    r.expires_at AS login_reservation_expires_at,
    EXISTS(
        SELECT 1 FROM account_credentials_encrypted c
        WHERE c.account_id = a.id
          AND COALESCE(NULLIF(BTRIM(c.password_encrypted), ''), '') <> ''
    ) AS has_credentials
FROM broker_accounts a
LEFT JOIN LATERAL (
    SELECT id, status, payload_json, runner_id, slot_id, trace_id,
           requested_at, dispatched_at, completed_at, expires_at
    FROM account_login_reservations
    WHERE account_id = a.id
      AND requested_at >= COALESCE(a.login_requested_at, a.created_at)
    ORDER BY requested_at DESC, id DESC
    LIMIT 1
) r ON TRUE
WHERE a.id = %s AND a.user_id = %s
