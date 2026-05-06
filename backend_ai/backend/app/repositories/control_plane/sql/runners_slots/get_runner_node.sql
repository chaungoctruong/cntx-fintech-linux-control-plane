SELECT
    runner_id,
    label,
    host,
    status,
    supported_profiles,
    capability_tags,
    capabilities_json,
    metadata_json,
    max_slots,
    last_registered_at,
    last_heartbeat_at
FROM runner_nodes
WHERE runner_id = %s
