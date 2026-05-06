SELECT
    COUNT(*) AS total_deployments,
    COUNT(*) FILTER (WHERE status = 'running') AS running_deployments,
    COUNT(*) FILTER (WHERE desired_state = 'running') AS desired_running_deployments,
    COUNT(*) FILTER (
        WHERE desired_state = 'running'
          AND status = 'failed'
    ) AS failed_deployments,
    COUNT(*) FILTER (WHERE status IN ('start_requested', 'starting', 'stop_requested')) AS transitional_deployments,
    COUNT(*) FILTER (
        WHERE desired_state = 'running'
          AND status IN ('start_requested', 'starting', 'running', 'stop_requested')
          AND COALESCE(last_heartbeat_at, created_at) < (NOW() - (%s * INTERVAL '1 second'))
    ) AS stale_deployments
FROM bot_deployments
