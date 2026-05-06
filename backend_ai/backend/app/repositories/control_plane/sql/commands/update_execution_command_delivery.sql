UPDATE execution_commands
SET delivery_status = %s,
    last_error = %s,
    payload_json = CASE
        WHEN %s::jsonb = '{}'::jsonb THEN payload_json
        ELSE payload_json || %s::jsonb
    END,
    dispatched_at = CASE
        WHEN %s = 'dispatched' AND dispatched_at IS NULL THEN NOW()
        ELSE dispatched_at
    END,
    acknowledged_at = CASE
        WHEN %s = 'acknowledged' THEN NOW()
        ELSE acknowledged_at
    END,
    updated_at = NOW()
WHERE command_id = %s
RETURNING *
