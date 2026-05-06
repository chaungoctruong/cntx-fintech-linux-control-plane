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
WHERE c.command_type = 'START_BOT'
  AND c.delivery_status = 'queued'
  AND c.created_at < (NOW() - (%s * INTERVAL '1 second'))
  AND d.status IN ('start_requested', 'starting')
  AND d.desired_state = 'running'
  AND COALESCE(d.is_active, FALSE) = TRUE
ORDER BY c.created_at ASC, c.id ASC
LIMIT %s
