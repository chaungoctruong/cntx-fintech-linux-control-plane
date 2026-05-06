SELECT *
FROM bot_deployments
WHERE account_id = %s
  AND status = ANY(%s)
ORDER BY updated_at DESC, id DESC
LIMIT 1
