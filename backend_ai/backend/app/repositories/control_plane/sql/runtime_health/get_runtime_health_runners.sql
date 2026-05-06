SELECT
    COUNT(*) AS total_runners,
    COUNT(*) FILTER (WHERE status = 'online') AS online_runners,
    COUNT(*) FILTER (WHERE status = 'degraded') AS degraded_runners,
    COUNT(*) FILTER (WHERE status = 'offline') AS offline_runners,
    COUNT(*) FILTER (
        WHERE last_heartbeat_at IS NULL
           OR last_heartbeat_at < (NOW() - (%s * INTERVAL '1 second'))
    ) AS stale_runners
FROM runner_nodes
