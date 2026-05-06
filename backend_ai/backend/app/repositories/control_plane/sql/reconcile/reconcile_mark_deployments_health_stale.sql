UPDATE bot_deployments
SET health_status = 'stale',
    updated_at = NOW()
WHERE desired_state = 'running'
  AND status IN ('start_requested', 'starting', 'running', 'stop_requested')
  AND COALESCE(last_heartbeat_at, created_at) < (NOW() - (%s * INTERVAL '1 second'))
  AND COALESCE(LOWER(health_status), '') <> 'stale'
