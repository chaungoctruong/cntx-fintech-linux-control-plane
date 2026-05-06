UPDATE execution_commands
SET last_error = %s,
    updated_at = NOW()
WHERE command_id = %s
  AND delivery_status IN ('pending', 'queued')
  AND COALESCE(redis_stream_id, '') = ''
