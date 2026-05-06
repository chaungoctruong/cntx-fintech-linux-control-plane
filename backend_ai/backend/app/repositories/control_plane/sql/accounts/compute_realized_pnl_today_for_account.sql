WITH today_start AS (
    SELECT date_trunc(
        'day',
        (NOW() AT TIME ZONE 'UTC') + make_interval(secs => %s)
    ) - make_interval(secs => %s) AS ts
)
SELECT
    COALESCE(SUM(
        COALESCE(
            NULLIF(payload_json->>'realized_pnl', '')::NUMERIC,
            NULLIF(payload_json->>'closed_pnl', '')::NUMERIC,
            NULLIF(payload_json->>'net_pnl', '')::NUMERIC,
            0
        )
    ), 0) AS pnl,
    COUNT(*) AS event_count,
    (SELECT EXTRACT(EPOCH FROM ts)::BIGINT FROM today_start) AS today_start_ts
FROM execution_events e, today_start t
WHERE e.account_id = %s
  AND e.event_type = 'ORDER_FILLED'
  AND e.created_at >= t.ts
