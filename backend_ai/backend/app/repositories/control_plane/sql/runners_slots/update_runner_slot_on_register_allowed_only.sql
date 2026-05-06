UPDATE runner_slots
SET allowed_profile_classes = %s::jsonb,
    updated_at = NOW(),
    last_heartbeat_at = NOW()
WHERE runner_id = %s AND slot_id = %s
