UPDATE bot_deployments
SET status = 'failed',
    desired_state = 'stopped',
    is_active = FALSE,
    health_status = 'orphaned_handoff',
    last_error = %s,
    stopped_at = NOW(),
    updated_at = NOW()
WHERE runner_id = %s
  AND slot_id = %s
  AND status = ANY(%s)
RETURNING id, account_id, status, health_status
