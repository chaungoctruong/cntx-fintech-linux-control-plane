WITH candidate AS (
    SELECT c.id
    FROM execution_commands c
    CROSS JOIN (
        SELECT COALESCE(
            (SELECT max_slots FROM runner_nodes WHERE runner_id = %s),
            1
        ) AS max_slots
    ) runner_ctx
    WHERE c.runner_id = %s
      AND c.delivery_status = 'queued'
      AND c.command_type = ANY(%s)
      AND (
            %s::TEXT IS NULL
            OR c.slot_id = %s
            OR runner_ctx.max_slots > 1
      )
    ORDER BY
        CASE
            WHEN c.command_type = 'STOP_BOT' THEN 0
            WHEN c.command_type = 'UPDATE_BOT_CONFIG' THEN 1
            ELSE 2
        END,
        CASE
            WHEN %s::TEXT IS NULL OR c.slot_id = %s THEN 0
            ELSE 1
        END,
        c.priority DESC,
        c.created_at ASC,
        c.id ASC
    LIMIT 1
    FOR UPDATE SKIP LOCKED
)
UPDATE execution_commands c
SET delivery_status = 'dispatched',
    dispatched_at = COALESCE(c.dispatched_at, NOW()),
    payload_json = COALESCE(c.payload_json, '{}'::jsonb)
        || jsonb_build_object(
            'delivery_transport', 'http_poll',
            'claimed_by_runner_http', TRUE,
            'claimed_runner_id', %s,
            'claimed_slot_id', %s,
            'claimed_command_slot_id', c.slot_id,
            'runner_wide_claim', (%s::TEXT IS NOT NULL AND c.slot_id <> %s),
            'claimed_at_epoch', EXTRACT(EPOCH FROM NOW())::BIGINT
        ),
    updated_at = NOW()
FROM candidate
WHERE c.id = candidate.id
RETURNING c.*
