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
    ver.id AS verification_job_id,
    ver.status AS verification_job_status,
    ver.payload_json AS verification_payload_json,
    ver.requested_at AS verification_requested_at,
    ver.completed_at AS verification_completed_at,
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
    SELECT id, status, payload_json, requested_at, completed_at
    FROM account_verification_jobs
    WHERE account_id = a.id
    ORDER BY requested_at DESC, id DESC
    LIMIT 1
) ver ON TRUE
LEFT JOIN LATERAL (
    SELECT id, bot_code, bot_name, status, health_status, last_heartbeat_at
    FROM bot_deployments
    WHERE account_id = a.id
    ORDER BY updated_at DESC, id DESC
    LIMIT 1
) dep ON TRUE
LEFT JOIN account_state_snapshots snap ON snap.account_id = a.id
WHERE a.id = %s AND a.user_id = %s
