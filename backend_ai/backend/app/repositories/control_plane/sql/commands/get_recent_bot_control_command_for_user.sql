SELECT
    c.id,
    c.command_id,
    c.command_type,
    c.account_id,
    c.deployment_id,
    c.bot_id,
    c.runner_id,
    c.slot_id,
    c.delivery_status,
    c.last_error,
    c.created_at,
    c.updated_at,
    GREATEST(
        1,
        CEIL(EXTRACT(EPOCH FROM (c.created_at + (%s * INTERVAL '1 second') - NOW())))
    )::INTEGER AS retry_after_sec
FROM execution_commands c
JOIN broker_accounts a ON a.id = c.account_id
WHERE a.user_id = %s
  AND c.command_type IN ('START_BOT', 'STOP_BOT')
  AND c.created_at >= NOW() - (%s * INTERVAL '1 second')
ORDER BY c.created_at DESC, c.id DESC
LIMIT 1
