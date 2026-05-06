UPDATE runner_slots
SET status = 'broken',
    current_account_id = NULL,
    metadata_json = COALESCE(metadata_json, '{}'::jsonb) || jsonb_strip_nulls(
        jsonb_build_object(
            'orphaned_handoff', TRUE,
            'orphaned_handoff_reason', %s,
            'orphaned_handoff_actor', %s,
            'orphaned_handoff_confirmed_at', NOW(),
            'last_orphaned_account_id', %s
        )
    ),
    updated_at = NOW()
WHERE runner_id = %s AND slot_id = %s
RETURNING runner_id, slot_id, status, metadata_json
