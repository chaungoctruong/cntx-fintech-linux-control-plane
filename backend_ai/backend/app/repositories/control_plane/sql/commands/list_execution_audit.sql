SELECT
    a.event_id,
    a.command_id,
    a.trace_id,
    a.account_id,
    a.deployment_id,
    a.runner_id,
    a.slot_id,
    a.event_type,
    a.severity,
    a.audit_status,
    a.payload_json,
    a.source_stream_id,
    a.created_at,
    a.processed_at
FROM execution_audit a
JOIN bot_deployments d ON d.id = a.deployment_id
WHERE a.deployment_id = %s
  AND d.user_id = %s
ORDER BY a.created_at DESC, a.id DESC
LIMIT %s
