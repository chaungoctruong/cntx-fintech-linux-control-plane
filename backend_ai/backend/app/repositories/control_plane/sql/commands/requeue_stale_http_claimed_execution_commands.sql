WITH stale AS (
    SELECT id
    FROM execution_commands
    WHERE delivery_status = 'dispatched'
      AND command_type = ANY(%s)
      AND (%s::TEXT IS NULL OR runner_id = %s)
      AND COALESCE(dispatched_at, updated_at, created_at)
            < (NOW() - (%s * INTERVAL '1 second'))
      AND (
            COALESCE(payload_json->>'delivery_transport', '') = 'http_poll'
            OR COALESCE(payload_json->>'claimed_by_runner_http', '') IN ('true', '1', 'yes')
      )
    ORDER BY COALESCE(dispatched_at, updated_at, created_at) ASC, id ASC
    LIMIT %s
    FOR UPDATE SKIP LOCKED
)
UPDATE execution_commands c
SET delivery_status = 'queued',
    dispatched_at = NULL,
    last_error = 'http_claim_lease_expired',
    payload_json = COALESCE(c.payload_json, '{}'::jsonb)
        || jsonb_build_object(
            'http_claim_requeued', TRUE,
            'http_claim_requeued_at_epoch', EXTRACT(EPOCH FROM NOW())::BIGINT,
            'http_claim_lease_sec', %s
        ),
    updated_at = NOW()
FROM stale
WHERE c.id = stale.id
RETURNING 1
