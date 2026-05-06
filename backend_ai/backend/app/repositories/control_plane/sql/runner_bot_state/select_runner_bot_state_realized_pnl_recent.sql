SELECT realized_pnl
FROM runner_bot_state_records
WHERE bot_id = %s
  AND record_type IN ('trade', 'execution')
  AND account_id = %s
  AND deployment_id = %s
  AND realized_pnl IS NOT NULL
ORDER BY COALESCE(occurred_at, closed_at, updated_at, created_at) DESC, id DESC
LIMIT %s
