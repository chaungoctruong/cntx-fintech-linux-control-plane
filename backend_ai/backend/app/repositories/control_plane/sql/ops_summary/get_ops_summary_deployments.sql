SELECT
    COUNT(*) FILTER (WHERE status = 'running') AS running,
    COUNT(*) FILTER (WHERE status IN ('start_requested', 'starting', 'queued')) AS starting,
    COUNT(*) FILTER (WHERE status = 'stop_requested') AS stopping,
    COUNT(*) FILTER (WHERE status = 'failed') AS failed,
    COUNT(*) FILTER (
        WHERE desired_state = 'running'
          AND status IN ('start_requested', 'starting', 'running', 'stop_requested')
          AND COALESCE(last_heartbeat_at, created_at) < (NOW() - (%s * INTERVAL '1 second'))
    ) AS stale
FROM bot_deployments
