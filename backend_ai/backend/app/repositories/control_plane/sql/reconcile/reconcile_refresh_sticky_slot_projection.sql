UPDATE runner_slots s
SET current_account_id = CASE
        WHEN s.status = 'allocated' THEN b.account_id
        ELSE s.current_account_id
    END,
    metadata_json = jsonb_strip_nulls(
        COALESCE(s.metadata_json, '{}'::jsonb)
        || jsonb_build_object(
            'sticky_account_id', b.account_id::TEXT,
            'available_for_new_account', FALSE
        )
    ),
    updated_at = NOW()
FROM account_slot_bindings b
WHERE b.runner_id = s.runner_id
  AND b.slot_id = s.slot_id
  AND b.is_current = TRUE
  AND b.binding_state = 'active'
  AND (
      NULLIF(BTRIM(COALESCE(s.metadata_json->>'sticky_account_id', '')), '') IS NULL
      OR LOWER(NULLIF(BTRIM(COALESCE(s.metadata_json->>'sticky_account_id', '')), '')) IN ('null', 'none', '0')
      OR NULLIF(BTRIM(COALESCE(s.metadata_json->>'sticky_account_id', '')), '') <> b.account_id::TEXT
      OR COALESCE(LOWER(NULLIF(BTRIM(s.metadata_json->>'available_for_new_account'), '')), 'false')
            IN ('true', '1', 'yes', 'y', 'on')
      OR (
          s.status = 'allocated'
          AND s.current_account_id IS DISTINCT FROM b.account_id
      )
  )
