SELECT
    command_id,
    command_type,
    account_id,
    deployment_id,
    bot_id,
    runner_id,
    slot_id,
    priority,
    payload_json,
    delivery_status,
    queue_name,
    redis_stream_id,
    trace_id,
    last_error,
    dispatched_at,
    acknowledged_at,
    created_at,
    updated_at
FROM execution_commands
WHERE LOWER(delivery_status) = ANY(%s)
  AND command_type = ANY(%s)
  AND COALESCE(runner_id, '') <> ''
  AND COALESCE(dispatched_at, updated_at, created_at)
        < (NOW() - (%s * INTERVAL '1 second'))
ORDER BY
    CASE WHEN command_type = 'STOP_BOT' THEN 0 ELSE 1 END,
    COALESCE(dispatched_at, updated_at, created_at) ASC,
    command_id ASC
LIMIT %s
