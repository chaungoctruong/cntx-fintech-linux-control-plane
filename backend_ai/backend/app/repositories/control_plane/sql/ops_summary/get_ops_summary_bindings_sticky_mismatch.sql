SELECT COUNT(*) AS sticky_mismatch
FROM account_slot_bindings b
JOIN runner_slots s
  ON s.runner_id = b.runner_id
 AND s.slot_id = b.slot_id
WHERE b.is_current = TRUE
  AND b.binding_state IN ('sticky', 'active')
  AND NULLIF(BTRIM(COALESCE(s.metadata_json->>'sticky_account_id', '')), '') IS NOT NULL
  AND LOWER(NULLIF(BTRIM(COALESCE(s.metadata_json->>'sticky_account_id', '')), '')) NOT IN ('null', 'none', '0')
  AND NULLIF(BTRIM(COALESCE(s.metadata_json->>'sticky_account_id', '')), '') <> b.account_id::TEXT
