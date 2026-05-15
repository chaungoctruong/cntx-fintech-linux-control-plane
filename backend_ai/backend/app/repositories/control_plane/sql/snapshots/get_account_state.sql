SELECT
    a.id AS account_id,
    a.broker,
    a.server,
    a.login,
    a.status AS connection_status,
    a.last_error,
    bind.runner_id,
    bind.slot_id,
    bind.binding_state,
    login_hold.id AS login_reservation_id,
    login_hold.status AS login_reservation_status,
    login_hold.payload_json AS login_reservation_payload_json,
    login_hold.runner_id AS login_reservation_runner_id,
    login_hold.slot_id AS login_reservation_slot_id,
    login_hold.trace_id AS login_reservation_trace_id,
    login_hold.requested_at AS login_reservation_requested_at,
    login_hold.dispatched_at AS login_reservation_dispatched_at,
    login_hold.completed_at AS login_reservation_completed_at,
    login_hold.expires_at AS login_reservation_expires_at,
    dep.id AS deployment_id,
    dep.bot_code,
    dep.bot_name,
    dep.status AS deployment_status,
    dep.health_status,
    dep.last_heartbeat_at,
    snap.pnl,
    snap.balance,
    snap.equity,
    snap.free_margin,
    snap.payload_json AS snapshot_payload,
    snap.heartbeat_at AS snapshot_heartbeat_at
FROM broker_accounts a
LEFT JOIN LATERAL (
    SELECT runner_id, slot_id, binding_state
    FROM account_slot_bindings
    WHERE account_id = a.id AND is_current = TRUE
    ORDER BY updated_at DESC, id DESC
    LIMIT 1
) bind ON TRUE
LEFT JOIN LATERAL (
    SELECT id, status, payload_json, runner_id, slot_id, trace_id,
           requested_at, dispatched_at, completed_at, expires_at
    FROM account_login_reservations
    WHERE account_id = a.id
    ORDER BY requested_at DESC, id DESC
    LIMIT 1
) login_hold ON TRUE
LEFT JOIN LATERAL (
    SELECT id, bot_code, bot_name, status, health_status, last_heartbeat_at
    FROM bot_deployments
    WHERE account_id = a.id
    ORDER BY updated_at DESC, id DESC
    LIMIT 1
) dep ON TRUE
LEFT JOIN account_state_snapshots snap ON snap.account_id = a.id
WHERE a.id = %s AND a.user_id = %s
