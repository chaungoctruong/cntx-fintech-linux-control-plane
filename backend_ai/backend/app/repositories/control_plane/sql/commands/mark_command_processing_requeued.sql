UPDATE execution_commands
SET delivery_status = 'queued',
    last_error = %s,
    updated_at = NOW()
WHERE command_id = %s
  AND delivery_status IN ('queued', 'dispatched')
