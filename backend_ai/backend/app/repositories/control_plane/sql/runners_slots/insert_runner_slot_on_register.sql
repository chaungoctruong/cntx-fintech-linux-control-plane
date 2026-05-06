INSERT INTO runner_slots(
    runner_id, slot_id, status, allowed_profile_classes, current_account_id,
    metadata_json, last_heartbeat_at, created_at, updated_at
)
VALUES(%s, %s, %s, %s::jsonb, NULL, %s::jsonb, NOW(), NOW(), NOW())
