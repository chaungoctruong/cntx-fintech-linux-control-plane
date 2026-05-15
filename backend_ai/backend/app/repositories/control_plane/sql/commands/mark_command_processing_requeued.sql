UPDATE execution_commands
SET delivery_status = 'queued',
    last_error = %s,
    payload_json = COALESCE(payload_json, '{}'::jsonb) || jsonb_build_object(
        'processing_requeue_count',
        CASE
            WHEN COALESCE(payload_json->>'processing_requeue_count', '') ~ '^[0-9]+$'
                THEN (payload_json->>'processing_requeue_count')::int + 1
            ELSE 1
        END,
        'last_processing_requeued_at',
        NOW()
    ),
    updated_at = NOW()
WHERE command_id = %s
  AND delivery_status IN ('queued', 'dispatched')
