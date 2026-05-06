UPDATE runner_nodes
SET status = CASE WHEN status = 'offline' THEN status ELSE 'degraded' END,
    updated_at = NOW()
WHERE runner_id = %s
RETURNING runner_id, status
