SELECT
    runner_id,
    slot_id,
    status,
    allowed_profile_classes,
    current_account_id,
    metadata_json,
    last_heartbeat_at
FROM runner_slots
WHERE runner_id = %s
ORDER BY slot_id ASC
