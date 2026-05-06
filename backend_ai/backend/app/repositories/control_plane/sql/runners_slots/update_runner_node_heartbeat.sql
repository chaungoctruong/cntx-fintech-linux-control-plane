UPDATE runner_nodes
SET last_heartbeat_at = NOW(),
    metadata_json = %s::jsonb,
    updated_at = NOW(),
    status = CASE
        WHEN status = 'offline' THEN 'online'
        WHEN status = 'draining' AND %s THEN 'online'
        ELSE status
    END
WHERE runner_id = %s
