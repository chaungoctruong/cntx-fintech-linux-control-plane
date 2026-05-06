UPDATE runner_nodes
SET status = 'offline',
    updated_at = NOW()
WHERE status <> 'offline'
  AND last_heartbeat_at IS NOT NULL
  AND last_heartbeat_at < (NOW() - (%s * INTERVAL '1 second'))
