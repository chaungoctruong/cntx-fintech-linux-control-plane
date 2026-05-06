SELECT
    COUNT(*) AS total,
    COUNT(*) FILTER (WHERE status = 'online') AS online,
    COUNT(*) FILTER (
        WHERE last_heartbeat_at IS NULL
           OR last_heartbeat_at < (NOW() - (%s * INTERVAL '1 second'))
    ) AS stale,
    COUNT(*) FILTER (WHERE status = 'degraded') AS degraded
FROM runner_nodes
