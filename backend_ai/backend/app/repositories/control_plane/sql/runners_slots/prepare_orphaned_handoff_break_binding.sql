UPDATE account_slot_bindings
SET binding_state = 'broken',
    is_current = FALSE,
    updated_at = NOW()
WHERE id = %s
