SELECT
    COUNT(*) FILTER (WHERE delivery_status IN ('pending', 'queued')) AS pending,
    COUNT(*) FILTER (WHERE delivery_status = 'dispatched') AS processing,
    COUNT(*) FILTER (
        WHERE delivery_status = 'failed'
          AND updated_at >= (NOW() - INTERVAL '1 hour')
    ) AS failed_recent_1h
FROM execution_commands
