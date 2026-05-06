SELECT
    COUNT(*) FILTER (WHERE created_at >= (NOW() - INTERVAL '30 minutes')) AS recent_30m,
    EXTRACT(EPOCH FROM (NOW() - COALESCE(MAX(created_at), TO_TIMESTAMP(0))))::BIGINT AS last_event_age_sec
FROM execution_events
