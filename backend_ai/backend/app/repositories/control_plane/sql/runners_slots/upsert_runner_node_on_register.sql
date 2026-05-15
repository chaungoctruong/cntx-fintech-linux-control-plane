INSERT INTO runner_nodes(
    runner_id, label, host, status, supported_profiles, capability_tags,
    capabilities_json, max_slots, metadata_json, last_registered_at, last_heartbeat_at,
    created_at, updated_at
)
VALUES(%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, LEAST(%s, 10), '{}'::jsonb, NOW(), NOW(), NOW(), NOW())
ON CONFLICT(runner_id) DO UPDATE SET
    label = EXCLUDED.label,
    host = EXCLUDED.host,
    status = EXCLUDED.status,
    supported_profiles = EXCLUDED.supported_profiles,
    capability_tags = EXCLUDED.capability_tags,
    capabilities_json = EXCLUDED.capabilities_json,
    max_slots = EXCLUDED.max_slots,
    last_registered_at = NOW(),
    last_heartbeat_at = NOW(),
    updated_at = NOW()
RETURNING runner_id, label, host, status, supported_profiles, capability_tags, capabilities_json, max_slots, metadata_json, last_registered_at, last_heartbeat_at
