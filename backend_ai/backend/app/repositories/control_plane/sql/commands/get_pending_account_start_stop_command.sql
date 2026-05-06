SELECT
    c.id,
    c.command_id,
    c.command_type,
    c.account_id,
    c.deployment_id,
    c.bot_id,
    c.runner_id,
    c.slot_id,
    c.delivery_status,
    c.redis_stream_id,
    c.trace_id,
    c.last_error,
    c.dispatched_at,
    c.acknowledged_at,
    c.created_at,
    c.updated_at
FROM execution_commands c
LEFT JOIN bot_deployments d ON d.id = c.deployment_id
WHERE c.account_id = %s
  AND c.command_type IN ('START_BOT', 'STOP_BOT')
  AND c.delivery_status IN ('pending', 'queued', 'dispatched')
  AND NOT (
      d.id IS NOT NULL
      AND d.desired_state = 'stopped'
      AND d.status = ANY(%s)
      AND COALESCE(d.is_active, FALSE) = FALSE
  )
ORDER BY c.updated_at DESC, c.id DESC
LIMIT 1
