UPDATE runner_slots
SET status = 'disabled',
    metadata_json = COALESCE(metadata_json, '{}'::jsonb) || jsonb_strip_nulls(
        jsonb_build_object(
            'maintenance_disabled', TRUE,
            'maintenance_reason', %s,
            'maintenance_actor', %s,
            'maintenance_updated_at', NOW()
        )
    ),
    updated_at = NOW()
WHERE runner_id = %s
  AND current_account_id IS NULL
  AND status IN ('ready', 'degraded')
