SELECT EXTRACT(EPOCH FROM created_at)::BIGINT AS created_at_ts, payload_json
FROM execution_events
WHERE deployment_id = %s
  AND event_type = 'ORDER_FILLED'
  AND created_at >= TO_TIMESTAMP(%s)
ORDER BY created_at ASC
LIMIT %s
