UPDATE execution_commands
SET last_error = %s,
    payload_json = COALESCE(payload_json, '{}'::jsonb) || jsonb_build_object(
        'delivery_replay_failures',
        CASE
            WHEN COALESCE(payload_json->>'delivery_replay_failures', '') ~ '^[0-9]+$'
                THEN (payload_json->>'delivery_replay_failures')::int + 1
            ELSE 1
        END,
        'last_replay_failure_at',
        NOW()
    ),
    updated_at = NOW()
WHERE command_id = %s
  AND delivery_status IN ('pending', 'queued')
  AND COALESCE(redis_stream_id, '') = ''
