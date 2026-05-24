SELECT
    d.id,
    d.account_id,
    d.bot_code,
    d.bot_name,
    d.profile_class,
    d.status,
    d.desired_state,
    d.runner_id,
    d.slot_id,
    d.health_status,
    d.last_error,
    d.last_runner_recovery_reason,
    d.last_runner_recovery_at,
    d.runner_recovery_first_seen_at,
    d.runner_recovery_last_seen_at,
    d.runner_recovery_attempt_count,
    d.runner_recovery_cooldown_until,
    d.runner_recovery_in_flight,
    d.runner_recovery_last_command_id,
    d.runner_recovery_last_command_at,
    d.last_heartbeat_at,
    d.config_json,
    d.created_at,
    d.updated_at,
    a.broker,
    a.server,
    a.login
FROM bot_deployments d
JOIN broker_accounts a ON a.id = d.account_id
WHERE d.user_id = %s
  AND (
      d.status = ANY(%s)
      OR d.updated_at >= COALESCE(a.login_requested_at, a.created_at)
  )
ORDER BY d.updated_at DESC, d.id DESC
