UPDATE account_slot_bindings
SET is_current = FALSE,
    binding_state = CASE
        WHEN binding_state = 'broken' THEN binding_state
        ELSE 'released'
    END,
    updated_at = NOW()
WHERE account_id = %s
  AND is_current = TRUE
  AND NOT (runner_id = %s AND slot_id = %s)
