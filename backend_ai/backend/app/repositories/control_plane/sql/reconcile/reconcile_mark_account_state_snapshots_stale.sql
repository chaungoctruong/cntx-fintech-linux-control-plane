UPDATE account_state_snapshots s
SET connection_status = 'stale',
    updated_at = NOW()
FROM bot_deployments d
WHERE s.account_id = d.account_id
  AND d.desired_state = 'running'
  AND d.status IN ('start_requested', 'starting', 'running', 'stop_requested')
  AND COALESCE(d.last_heartbeat_at, d.created_at) < (NOW() - (%s * INTERVAL '1 second'))
  AND COALESCE(LOWER(s.connection_status), '') <> 'stale'
