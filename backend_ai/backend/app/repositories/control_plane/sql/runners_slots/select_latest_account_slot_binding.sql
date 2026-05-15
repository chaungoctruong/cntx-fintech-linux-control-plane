SELECT id
FROM account_slot_bindings
WHERE account_id = %s
  AND runner_id = %s
  AND slot_id = %s
ORDER BY is_current DESC, updated_at DESC, id DESC
LIMIT 1
