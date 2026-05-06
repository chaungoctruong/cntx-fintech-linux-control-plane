SELECT id
FROM account_slot_bindings
WHERE account_id = %s
  AND runner_id = %s
  AND slot_id = %s
ORDER BY id DESC
LIMIT 1
