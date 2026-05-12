SELECT
    d.*,
    'account_state_snapshot' AS blocker_source,
    snap.deployment_id AS blocker_deployment_id,
    snap.runner_id AS blocker_runner_id,
    snap.slot_id AS blocker_slot_id,
    snap.connection_status AS runtime_connection_status,
    snap.payload_json AS runtime_payload_json,
    snap.heartbeat_at AS runtime_heartbeat_at
FROM account_state_snapshots snap
LEFT JOIN bot_deployments d ON d.id = snap.deployment_id
WHERE snap.account_id = %s
  AND snap.heartbeat_at >= NOW() - (%s * INTERVAL '1 second')
  AND (
      d.id IS NULL
      OR d.status IN ('start_requested', 'starting', 'running', 'stop_requested')
      OR COALESCE(d.is_active, FALSE) = TRUE
  )
  AND (
      LOWER(COALESCE(snap.connection_status, '')) IN ('connected', 'running', 'ok', 'active')
      OR LOWER(COALESCE(snap.payload_json->>'terminal_running', '')) IN ('true', '1', 'yes', 'y', 'on')
      OR COALESCE(NULLIF(BTRIM(snap.payload_json->>'worker_pid'), ''), '0') <> '0'
      OR COALESCE(NULLIF(BTRIM(snap.payload_json->>'pid'), ''), '0') <> '0'
  )
ORDER BY snap.heartbeat_at DESC, snap.updated_at DESC
LIMIT 1
