INSERT INTO runner_bot_state_records(
    bot_id, schema_name, operation, record_type, record_key,
    account_id, deployment_id, runner_id, slot_id,
    status, symbol, side, realized_pnl, occurred_at,
    payload_json, context_json, created_at, updated_at
)
VALUES(
    %s, %s, %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s, %s, %s, %s::timestamptz,
    %s::jsonb, %s::jsonb, NOW(), NOW()
)
ON CONFLICT(bot_id, record_type, account_id, deployment_id, record_key)
DO UPDATE SET
    schema_name = EXCLUDED.schema_name,
    operation = EXCLUDED.operation,
    runner_id = EXCLUDED.runner_id,
    slot_id = EXCLUDED.slot_id,
    status = EXCLUDED.status,
    symbol = COALESCE(EXCLUDED.symbol, runner_bot_state_records.symbol),
    side = COALESCE(EXCLUDED.side, runner_bot_state_records.side),
    realized_pnl = COALESCE(EXCLUDED.realized_pnl, runner_bot_state_records.realized_pnl),
    occurred_at = COALESCE(EXCLUDED.occurred_at, runner_bot_state_records.occurred_at),
    payload_json = EXCLUDED.payload_json,
    context_json = EXCLUDED.context_json,
    updated_at = NOW()
RETURNING
    id, bot_id, schema_name, operation, record_type, record_key,
    account_id, deployment_id, runner_id, slot_id, status,
    symbol, side, realized_pnl, occurred_at, closed_at,
    payload_json, created_at, updated_at
