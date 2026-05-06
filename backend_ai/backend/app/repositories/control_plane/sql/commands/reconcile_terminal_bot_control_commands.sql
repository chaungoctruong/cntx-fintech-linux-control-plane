WITH terminal_deployments AS (
    SELECT id, account_id, status
    FROM bot_deployments
    WHERE desired_state = 'stopped'
      AND status = ANY(%s)
      AND COALESCE(is_active, FALSE) = FALSE
      AND (%s::BIGINT IS NULL OR account_id = %s)
      AND (%s::BIGINT IS NULL OR id = %s)
),
failed_start AS (
    UPDATE execution_commands c
    SET delivery_status = 'failed',
        last_error = COALESCE(
            NULLIF(c.last_error, ''),
            CASE
                WHEN d.status = 'stopped' THEN 'start_command_superseded_by_stop'
                ELSE 'deployment_terminal_state_reconciled'
            END
        ),
        payload_json = COALESCE(c.payload_json, '{}'::jsonb)
            || jsonb_build_object(
                'terminal_reconciled', TRUE,
                'terminal_deployment_status', d.status
            ),
        updated_at = NOW()
    FROM terminal_deployments d
    WHERE c.deployment_id = d.id
      AND c.account_id = d.account_id
      AND c.command_type = 'START_BOT'
      AND c.delivery_status IN ('pending', 'queued', 'dispatched')
      AND (%s <= 0 OR c.updated_at < (NOW() - (%s * INTERVAL '1 second')))
    RETURNING 1
),
acknowledged_stop AS (
    UPDATE execution_commands c
    SET delivery_status = 'acknowledged',
        acknowledged_at = COALESCE(c.acknowledged_at, NOW()),
        payload_json = COALESCE(c.payload_json, '{}'::jsonb)
            || jsonb_build_object(
                'terminal_reconciled', TRUE,
                'terminal_deployment_status', d.status
            ),
        updated_at = NOW()
    FROM terminal_deployments d
    WHERE c.deployment_id = d.id
      AND c.account_id = d.account_id
      AND c.command_type = 'STOP_BOT'
      AND c.delivery_status IN ('pending', 'queued', 'dispatched')
      AND (%s <= 0 OR c.updated_at < (NOW() - (%s * INTERVAL '1 second')))
    RETURNING 1
)
SELECT
    (SELECT COUNT(*) FROM failed_start) AS failed_start_commands,
    (SELECT COUNT(*) FROM acknowledged_stop) AS acknowledged_stop_commands
