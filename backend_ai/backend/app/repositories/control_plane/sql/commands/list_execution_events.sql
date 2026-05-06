SELECT
    e.event_id,
    e.event_type,
    e.account_id,
    e.deployment_id,
    e.bot_id,
    e.runner_id,
    e.slot_id,
    e.command_id,
    e.severity,
    e.payload_json,
    e.trace_id,
    e.created_at
FROM execution_events e
JOIN bot_deployments d ON d.id = e.deployment_id
WHERE e.deployment_id = %s
  AND d.user_id = %s
ORDER BY e.created_at DESC, e.id DESC
LIMIT %s
