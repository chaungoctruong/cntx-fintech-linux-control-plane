SELECT
    id, bot_id, schema_name, operation, record_type, record_key,
    account_id, deployment_id, runner_id, slot_id, status,
    symbol, side, realized_pnl, occurred_at, closed_at,
    payload_json, created_at, updated_at
FROM runner_bot_state_records
WHERE bot_id = %s
  AND record_type = 'pending_entry'
  AND account_id = %s
  AND deployment_id = %s
  AND status = 'active'
ORDER BY COALESCE(occurred_at, updated_at, created_at) DESC, id DESC
LIMIT 1
