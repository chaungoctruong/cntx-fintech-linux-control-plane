SELECT COUNT(*) AS count
FROM execution_commands
WHERE LOWER(delivery_status) = ANY(%s)
  AND command_type = ANY(%s)
