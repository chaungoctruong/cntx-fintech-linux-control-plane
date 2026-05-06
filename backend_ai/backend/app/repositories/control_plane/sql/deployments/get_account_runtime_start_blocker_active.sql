SELECT
    d.*,
    'active_deployment' AS blocker_source,
    d.id AS blocker_deployment_id,
    d.runner_id AS blocker_runner_id,
    d.slot_id AS blocker_slot_id,
    NULL::TEXT AS runtime_connection_status,
    NULL::JSONB AS runtime_payload_json,
    d.last_heartbeat_at AS runtime_heartbeat_at
FROM bot_deployments d
WHERE d.account_id = %s
  AND d.status = ANY(%s)
ORDER BY d.updated_at DESC, d.id DESC
LIMIT 1
