INSERT INTO execution_commands(
    command_id, command_type, account_id, deployment_id, bot_id,
    runner_id, slot_id, priority, payload_json, delivery_status,
    queue_name, redis_stream_id, trace_id, created_at, updated_at
)
VALUES(
    %s, %s, %s, %s, %s,
    %s, %s, %s, %s::jsonb, 'pending',
    %s, NULL, %s, NOW(), NOW()
)
ON CONFLICT (account_id, deployment_id, command_type, trace_id)
    WHERE trace_id IS NOT NULL
DO UPDATE SET
    updated_at = execution_commands.updated_at
RETURNING *
