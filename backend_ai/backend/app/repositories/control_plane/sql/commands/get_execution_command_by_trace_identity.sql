SELECT *
FROM execution_commands
WHERE account_id = %s
  AND deployment_id = %s
  AND command_type = %s
  AND trace_id = %s
ORDER BY id DESC
LIMIT 1
