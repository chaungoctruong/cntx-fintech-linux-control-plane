UPDATE execution_commands
SET delivery_status = 'failed',
    last_error = COALESCE(NULLIF(last_error, ''), %s),
    updated_at = NOW()
WHERE deployment_id = %s
  AND command_type = 'START_BOT'
  AND delivery_status IN ('pending', 'queued', 'dispatched')
