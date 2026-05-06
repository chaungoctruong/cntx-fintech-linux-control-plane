SELECT
    l.account_id,
    l.deployment_id,
    l.runner_id,
    l.slot_id,
    l.level,
    l.message,
    l.payload_json,
    l.trace_id,
    l.created_at
FROM runtime_logs l
JOIN bot_deployments d ON d.id = l.deployment_id
WHERE l.deployment_id = %s
  AND d.user_id = %s
ORDER BY l.created_at DESC, l.id DESC
LIMIT %s
