UPDATE runner_nodes
SET status = CASE WHEN status = 'offline' THEN status ELSE 'draining' END,
    metadata_json = COALESCE(metadata_json, '{}'::jsonb) || jsonb_strip_nulls(
        jsonb_build_object(
            'maintenance_mode', TRUE,
            'maintenance_reason', %s,
            'maintenance_actor', %s,
            'maintenance_updated_at', NOW()
        )
    ),
    updated_at = NOW()
WHERE runner_id = %s
RETURNING runner_id, status, metadata_json
