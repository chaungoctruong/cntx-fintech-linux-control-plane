INSERT INTO account_state_snapshots(
    account_id, deployment_id, runner_id, slot_id,
    connection_status, pnl, balance, equity, free_margin,
    payload_json, heartbeat_at, created_at, updated_at
)
VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW(), NOW(), NOW())
ON CONFLICT(account_id) DO UPDATE SET
    deployment_id = EXCLUDED.deployment_id,
    runner_id = EXCLUDED.runner_id,
    slot_id = EXCLUDED.slot_id,
    connection_status = EXCLUDED.connection_status,
    pnl = EXCLUDED.pnl,
    balance = EXCLUDED.balance,
    equity = EXCLUDED.equity,
    free_margin = EXCLUDED.free_margin,
    payload_json = EXCLUDED.payload_json,
    heartbeat_at = NOW(),
    updated_at = NOW()
