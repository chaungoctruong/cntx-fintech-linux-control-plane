WITH target AS (
    SELECT id
    FROM runner_bot_state_records
    WHERE bot_id = %s
      AND record_type = 'pending_entry'
      AND account_id = %s
      AND deployment_id = %s
      AND status = 'active'
      AND (%s IS NULL OR record_key = %s)
    ORDER BY COALESCE(occurred_at, updated_at, created_at) DESC, id DESC
    LIMIT 1
    FOR UPDATE
)
UPDATE runner_bot_state_records r
SET operation = 'close_pending_entry',
    status = 'closed',
    closed_at = COALESCE(%s::timestamptz, NOW()),
    payload_json = COALESCE(r.payload_json, '{}'::jsonb) || %s::jsonb,
    context_json = %s::jsonb,
    updated_at = NOW()
FROM target
WHERE r.id = target.id
RETURNING
    r.id, r.bot_id, r.schema_name, r.operation, r.record_type, r.record_key,
    r.account_id, r.deployment_id, r.runner_id, r.slot_id, r.status,
    r.symbol, r.side, r.realized_pnl, r.occurred_at, r.closed_at,
    r.payload_json, r.created_at, r.updated_at
