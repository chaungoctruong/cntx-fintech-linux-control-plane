SELECT id, account_id, binding_state
FROM account_slot_bindings
WHERE runner_id = %s
  AND slot_id = %s
  AND is_current = TRUE
ORDER BY updated_at DESC, id DESC
LIMIT 1
