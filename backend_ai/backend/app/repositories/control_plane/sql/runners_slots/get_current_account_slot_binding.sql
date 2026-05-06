SELECT
    id,
    account_id,
    runner_id,
    slot_id,
    binding_state,
    is_sticky,
    is_current,
    last_used_at,
    created_at,
    updated_at
FROM account_slot_bindings
WHERE account_id = %s
  AND is_current = TRUE
ORDER BY updated_at DESC, id DESC
LIMIT 1
