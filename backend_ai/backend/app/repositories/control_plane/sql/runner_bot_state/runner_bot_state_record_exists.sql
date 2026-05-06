SELECT 1
FROM runner_bot_state_records
WHERE bot_id = %s
  AND record_type = %s
  AND account_id = %s
  AND deployment_id = %s
  AND record_key = %s
LIMIT 1
