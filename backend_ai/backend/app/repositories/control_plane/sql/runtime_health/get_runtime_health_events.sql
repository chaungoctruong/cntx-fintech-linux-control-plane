SELECT
    COUNT(*) FILTER (WHERE created_at >= (NOW() - INTERVAL '30 minutes')) AS recent_event_count,
    EXTRACT(
        EPOCH FROM GREATEST(
            COALESCE(MAX(created_at), TO_TIMESTAMP(0)),
            COALESCE((SELECT MAX(last_heartbeat_at) FROM runner_nodes), TO_TIMESTAMP(0)),
            COALESCE((SELECT MAX(last_heartbeat_at) FROM bot_deployments), TO_TIMESTAMP(0))
        )
    )::BIGINT AS last_runtime_activity_ts
FROM execution_events
