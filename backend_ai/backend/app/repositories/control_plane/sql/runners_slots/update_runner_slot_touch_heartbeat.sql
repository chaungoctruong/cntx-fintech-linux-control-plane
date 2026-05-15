UPDATE runner_slots
SET last_heartbeat_at = NOW(),
    status = CASE
        WHEN status = 'degraded'
             AND current_account_id IS NULL
             AND (
                 COALESCE(NULLIF(SUBSTRING(slot_id FROM '([0-9]+)$'), ''), '') = ''
                 OR CAST(SUBSTRING(slot_id FROM '([0-9]+)$') AS INTEGER) <= 10
             ) THEN 'ready'
        WHEN status = 'degraded'
             AND current_account_id IS NOT NULL
             AND (
                 COALESCE(NULLIF(SUBSTRING(slot_id FROM '([0-9]+)$'), ''), '') = ''
                 OR CAST(SUBSTRING(slot_id FROM '([0-9]+)$') AS INTEGER) <= 10
             ) THEN 'allocated'
        ELSE status
    END,
    updated_at = NOW()
WHERE runner_id = %s AND slot_id = %s
  AND (
      NULLIF(BTRIM(COALESCE(metadata_json->>'auto_quarantine_until', '')), '') IS NULL
      OR NOT (
          NULLIF(BTRIM(COALESCE(metadata_json->>'auto_quarantine_until', '')), '') ~ '^[0-9]{4}-'
          AND (NULLIF(BTRIM(COALESCE(metadata_json->>'auto_quarantine_until', '')), ''))::TIMESTAMPTZ > NOW()
      )
  )
