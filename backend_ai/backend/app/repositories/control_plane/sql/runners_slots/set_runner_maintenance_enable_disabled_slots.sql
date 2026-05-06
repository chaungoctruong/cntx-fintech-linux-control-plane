UPDATE runner_slots
SET status = CASE WHEN current_account_id IS NOT NULL THEN 'allocated' ELSE 'ready' END,
    metadata_json = COALESCE(metadata_json, '{}'::jsonb)
        - 'maintenance_disabled'
        - 'maintenance_reason'
        - 'maintenance_actor'
        - 'maintenance_updated_at',
    updated_at = NOW()
WHERE runner_id = %s
  AND status = 'disabled'
