WITH durations AS (
    SELECT (EXTRACT(EPOCH FROM (completed_at - requested_at)) * 1000.0) AS duration_ms
    FROM account_login_reservations
    WHERE completed_at IS NOT NULL
      AND requested_at IS NOT NULL
      AND completed_at >= requested_at
      AND completed_at >= (NOW() - INTERVAL '24 hours')
)
SELECT
    COUNT(*) FILTER (WHERE status = 'pending') AS pending,
    COUNT(*) FILTER (WHERE status = 'dispatched') AS dispatched,
    COUNT(*) FILTER (
        WHERE status = 'failed'
          AND COALESCE(completed_at, updated_at, created_at) >= (NOW() - INTERVAL '1 hour')
    ) AS failed_recent_1h,
    (SELECT (PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY duration_ms))::BIGINT FROM durations) AS p50_ms,
    (SELECT (PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms))::BIGINT FROM durations) AS p95_ms
FROM account_login_reservations
