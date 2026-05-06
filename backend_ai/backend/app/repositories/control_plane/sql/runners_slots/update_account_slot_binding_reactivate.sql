UPDATE account_slot_bindings
SET binding_state = 'active',
    is_sticky = %s,
    is_current = TRUE,
    last_used_at = NOW(),
    updated_at = NOW()
WHERE id = %s
RETURNING id, account_id, runner_id, slot_id, binding_state, is_sticky, is_current, last_used_at, created_at, updated_at
