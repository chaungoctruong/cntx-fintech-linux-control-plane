UPDATE runner_nodes n
SET status = CASE
        WHEN n.status = 'offline' THEN 'offline'
        WHEN EXISTS(
            SELECT 1
            FROM runner_slots s
            WHERE s.runner_id = n.runner_id
              AND s.status IN ('broken', 'degraded')
        ) THEN 'degraded'
        ELSE 'online'
    END,
    metadata_json = COALESCE(metadata_json, '{}'::jsonb)
        - 'maintenance_mode'
        - 'maintenance_reason'
        - 'maintenance_actor'
        - 'maintenance_updated_at',
    updated_at = NOW()
WHERE n.runner_id = %s
RETURNING runner_id, status, metadata_json
