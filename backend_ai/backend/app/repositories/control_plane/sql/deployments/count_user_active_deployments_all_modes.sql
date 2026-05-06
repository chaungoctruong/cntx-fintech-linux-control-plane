SELECT COUNT(*)::INT AS n
FROM bot_deployments
WHERE user_id = %s
  AND status IN ('start_requested', 'starting', 'running', 'stop_requested')
