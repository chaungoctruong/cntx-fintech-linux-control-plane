SELECT
    c.command_id,
    c.command_type,
    c.account_id,
    c.deployment_id,
    c.bot_id,
    c.runner_id,
    c.slot_id,
    c.priority,
    c.payload_json,
    c.delivery_status,
    c.queue_name,
    c.redis_stream_id,
    c.trace_id,
    c.last_error,
    c.dispatched_at,
    c.acknowledged_at,
    c.created_at,
    c.updated_at
FROM execution_commands c
JOIN bot_deployments d ON d.id = c.deployment_id
WHERE c.deployment_id = %s
  AND d.user_id = %s
ORDER BY c.created_at DESC, c.id DESC
LIMIT %s
