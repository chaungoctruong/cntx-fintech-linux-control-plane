SELECT COUNT(*)::INT AS n
FROM broker_accounts
WHERE user_id = %s
  AND status <> 'disconnected'
  AND COALESCE(is_active, TRUE) = TRUE
