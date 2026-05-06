INSERT INTO account_slot_bindings(
    account_id, runner_id, slot_id, binding_state,
    is_sticky, is_current, last_used_at, created_at, updated_at
)
VALUES(%s, %s, %s, 'active', %s, TRUE, NOW(), NOW(), NOW())
RETURNING id, account_id, runner_id, slot_id, binding_state, is_sticky, is_current, last_used_at, created_at, updated_at
