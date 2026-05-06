UPDATE execution_commands
SET delivery_status = CASE
        WHEN delivery_status IN ('pending', 'queued') THEN %s
        ELSE delivery_status
    END,
    redis_stream_id = COALESCE(%s, redis_stream_id),
    updated_at = NOW()
WHERE command_id = %s
