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
