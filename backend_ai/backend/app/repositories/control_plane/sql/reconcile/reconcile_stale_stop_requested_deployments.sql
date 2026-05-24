WITH stop_account_not_active AS (
    SELECT DISTINCT deployment_id
    FROM (
        SELECT e.deployment_id
        FROM execution_events e
        JOIN execution_commands c ON c.command_id = e.command_id
        WHERE e.event_type = 'COMMAND_REJECTED'
          AND c.command_type = 'STOP_BOT'
          AND LOWER(COALESCE(
              e.payload_json->>'reason',
              e.payload_json->>'error',
              e.payload_json->>'error_text',
              e.payload_json->>'message',
              ''
          )) = 'account_not_active'
          AND e.deployment_id IS NOT NULL
        UNION
        SELECT c.deployment_id
        FROM execution_commands c
        WHERE c.command_type = 'STOP_BOT'
          AND c.delivery_status = 'failed'
          AND LOWER(COALESCE(c.last_error, '')) = 'account_not_active'
          AND c.deployment_id IS NOT NULL
    ) rejected_stop
),
candidate_deployments AS (
    SELECT
        d.id,
        d.account_id,
        d.runner_id,
        d.slot_id,
        d.binding_id
    FROM bot_deployments d
    JOIN runner_nodes n ON n.runner_id = d.runner_id
    LEFT JOIN runner_slots s
      ON s.runner_id = d.runner_id
     AND s.slot_id = d.slot_id
    WHERE d.status = 'stop_requested'
      AND d.desired_state = 'stopped'
      AND COALESCE(d.is_active, FALSE) = TRUE
      AND COALESCE(d.updated_at, d.created_at) < (NOW() - (%s * INTERVAL '1 second'))
      AND n.status = 'online'
      AND n.last_heartbeat_at IS NOT NULL
      AND n.last_heartbeat_at >= (NOW() - (%s * INTERVAL '1 second'))
      AND (
          EXISTS (
              SELECT 1
              FROM stop_account_not_active stop_na
              WHERE stop_na.deployment_id = d.id
          )
          OR (
              s.slot_id IS NOT NULL
              AND s.status = 'ready'
              AND s.current_account_id IS NULL
          )
          OR (
              s.slot_id IS NOT NULL
              AND LOWER(COALESCE(
                  NULLIF(BTRIM(s.metadata_json->>'runner_state'), ''),
                  NULLIF(BTRIM(s.metadata_json->>'current_runner_state'), ''),
                  NULLIF(BTRIM(s.metadata_json->>'control_plane_state'), ''),
                  NULLIF(BTRIM(s.metadata_json->>'current_control_plane_state'), ''),
                  ''
              )) IN ('ready', 'empty', 'stopped')
              AND COALESCE(NULLIF(BTRIM(s.metadata_json->>'terminal_pid'), ''), '0') = '0'
              AND LOWER(COALESCE(s.metadata_json->>'mt5_observed', 'false')) IN ('false', '0', 'no', 'off')
              AND LOWER(COALESCE(s.metadata_json->>'deployment_id', '')) IN ('', 'null', 'none', '0')
          )
          OR (
              (
                  CASE
                      WHEN COALESCE(n.metadata_json->>'reported_slots_active', n.metadata_json->>'reported_active_slots', '') ~ '^[0-9]+$'
                          THEN COALESCE(n.metadata_json->>'reported_slots_active', n.metadata_json->>'reported_active_slots')::INTEGER
                      ELSE 0
                  END
              ) = 0
              AND (
                  CASE
                      WHEN COALESCE(n.metadata_json->>'reported_slots_degraded', n.metadata_json->>'reported_degraded_slots', '') ~ '^[0-9]+$'
                          THEN COALESCE(n.metadata_json->>'reported_slots_degraded', n.metadata_json->>'reported_degraded_slots')::INTEGER
                      ELSE 0
                  END
              ) = 0
              AND (
                  CASE
                      WHEN COALESCE(n.metadata_json->>'reported_slots_broken', n.metadata_json->>'reported_broken_slots', '') ~ '^[0-9]+$'
                          THEN COALESCE(n.metadata_json->>'reported_slots_broken', n.metadata_json->>'reported_broken_slots')::INTEGER
                      ELSE 0
                  END
              ) = 0
          )
      )
      AND (
          s.slot_id IS NULL
          OR (
              s.status = 'ready'
              AND s.current_account_id IS NULL
          )
          OR (
              EXISTS (
                  SELECT 1
                  FROM stop_account_not_active stop_na
                  WHERE stop_na.deployment_id = d.id
              )
              AND s.current_account_id = d.account_id
              AND LOWER(COALESCE(
                  s.metadata_json->>'runner_state',
                  s.metadata_json->>'current_runner_state',
                  s.metadata_json->>'control_plane_state',
                  ''
              )) IN ('ready', 'empty', 'stopped')
              AND LOWER(COALESCE(s.metadata_json->>'active_account_id', '')) IN ('', 'null', 'none', '0')
              AND LOWER(COALESCE(s.metadata_json->>'deployment_id', '')) IN ('', 'null', 'none', '0')
          )
          OR (
              s.current_account_id = d.account_id
              AND LOWER(COALESCE(
                  NULLIF(BTRIM(s.metadata_json->>'runner_state'), ''),
                  NULLIF(BTRIM(s.metadata_json->>'current_runner_state'), ''),
                  NULLIF(BTRIM(s.metadata_json->>'control_plane_state'), ''),
                  NULLIF(BTRIM(s.metadata_json->>'current_control_plane_state'), ''),
                  ''
              )) IN ('ready', 'empty', 'stopped')
              AND COALESCE(NULLIF(BTRIM(s.metadata_json->>'terminal_pid'), ''), '0') = '0'
              AND LOWER(COALESCE(s.metadata_json->>'mt5_observed', 'false')) IN ('false', '0', 'no', 'off')
              AND LOWER(COALESCE(s.metadata_json->>'deployment_id', '')) IN ('', 'null', 'none', '0')
          )
      )
      AND (
          d.stopped_at IS NOT NULL
          OR EXISTS (
              SELECT 1
              FROM execution_events stopped
              WHERE stopped.deployment_id = d.id
                AND stopped.event_type = 'BOT_STOPPED'
          )
          OR EXISTS (
              SELECT 1
              FROM execution_commands stop_cmd
              WHERE stop_cmd.deployment_id = d.id
                AND stop_cmd.command_type = 'STOP_BOT'
                AND stop_cmd.delivery_status IN ('acknowledged', 'completed', 'succeeded')
          )
          OR EXISTS (
              SELECT 1
              FROM stop_account_not_active stop_na
              WHERE stop_na.deployment_id = d.id
          )
          OR (
              s.slot_id IS NOT NULL
              AND s.status = 'ready'
              AND s.current_account_id IS NULL
          )
          OR (
              s.slot_id IS NOT NULL
              AND LOWER(COALESCE(
                  NULLIF(BTRIM(s.metadata_json->>'runner_state'), ''),
                  NULLIF(BTRIM(s.metadata_json->>'current_runner_state'), ''),
                  NULLIF(BTRIM(s.metadata_json->>'control_plane_state'), ''),
                  NULLIF(BTRIM(s.metadata_json->>'current_control_plane_state'), ''),
                  ''
              )) IN ('ready', 'empty', 'stopped')
              AND COALESCE(NULLIF(BTRIM(s.metadata_json->>'terminal_pid'), ''), '0') = '0'
              AND LOWER(COALESCE(s.metadata_json->>'mt5_observed', 'false')) IN ('false', '0', 'no', 'off')
              AND LOWER(COALESCE(s.metadata_json->>'deployment_id', '')) IN ('', 'null', 'none', '0')
          )
      )
      AND (
          EXISTS (
              SELECT 1
              FROM stop_account_not_active stop_na
              WHERE stop_na.deployment_id = d.id
          )
          OR NOT EXISTS (
              SELECT 1
              FROM execution_events started
              WHERE started.deployment_id = d.id
                AND started.event_type = 'BOT_STARTED'
                AND started.created_at > COALESCE(
                    (
                        SELECT MAX(stopped.created_at)
                        FROM execution_events stopped
                        WHERE stopped.deployment_id = d.id
                          AND stopped.event_type = 'BOT_STOPPED'
                    ),
                    COALESCE(d.stopped_at, TO_TIMESTAMP(0))
                )
            )
      )
      AND NOT EXISTS (
          SELECT 1
          FROM bot_deployments other
          WHERE other.account_id = d.account_id
            AND other.id <> d.id
            AND other.status = ANY(%s)
      )
    FOR UPDATE OF d SKIP LOCKED
),
updated_deployments AS (
    UPDATE bot_deployments d
    SET status = 'stopped',
        desired_state = 'stopped',
        is_active = FALSE,
        health_status = 'stopped',
        stopped_at = COALESCE(d.stopped_at, NOW()),
        last_error = COALESCE(NULLIF(d.last_error, ''), 'stale_stop_requested_reconciled'),
        updated_at = NOW()
    FROM candidate_deployments c
    WHERE d.id = c.id
    RETURNING d.id, d.runner_id, d.slot_id, d.binding_id
),
released_slots AS (
    UPDATE runner_slots s
    SET current_account_id = NULL,
        status = CASE WHEN s.status = 'broken' THEN s.status ELSE 'ready' END,
        metadata_json = jsonb_strip_nulls(
            (
                COALESCE(s.metadata_json, '{}'::jsonb)
                    - 'account_id'
                    - 'active_account_id'
                    - 'deployment_id'
                    - 'login_reservation_id'
                    - 'login_reservation_status'
                    - 'login_reservation_account_id'
                    - 'login_slot_status'
                    - 'login_slot_account_id'
                    - 'current_control_plane_state'
                    - 'previous_control_plane_state'
                    - 'current_runner_state'
                    - 'previous_runner_state'
                    - 'current_state'
                    - 'previous_state'
                    - 'reason'
                    - 'last_error'
            ) || jsonb_build_object(
                'control_plane_state', 'ready',
                'current_control_plane_state', 'ready',
                'available_for_new_account', TRUE,
                'stop_reconcile_reason', 'stale_stop_requested_reconciled'
            )
        ),
        updated_at = NOW()
    FROM updated_deployments d
    WHERE s.runner_id = d.runner_id
      AND s.slot_id = d.slot_id
      AND (
          COALESCE(NULLIF(SUBSTRING(s.slot_id FROM '([0-9]+)$'), ''), '') = ''
          OR CAST(SUBSTRING(s.slot_id FROM '([0-9]+)$') AS INTEGER) <= 12
      )
    RETURNING 1
),
refreshed_bindings AS (
    UPDATE account_slot_bindings b
    SET binding_state = CASE WHEN b.binding_state = 'broken' THEN b.binding_state ELSE 'sticky' END,
        is_sticky = TRUE,
        is_current = TRUE,
        last_used_at = NOW(),
        updated_at = NOW()
    FROM updated_deployments d
    WHERE b.id = d.binding_id
    RETURNING 1
),
failed_stale_start_commands AS (
    UPDATE execution_commands c
    SET delivery_status = 'failed',
        last_error = COALESCE(NULLIF(c.last_error, ''), 'stale_start_command_reconciled_after_stop'),
        updated_at = NOW()
    FROM updated_deployments d
    WHERE c.deployment_id = d.id
      AND c.command_type = 'START_BOT'
      AND c.delivery_status IN ('pending', 'queued', 'dispatched')
    RETURNING 1
),
acknowledged_stale_stop_commands AS (
    UPDATE execution_commands c
    SET delivery_status = 'acknowledged',
        acknowledged_at = COALESCE(c.acknowledged_at, NOW()),
        last_error = COALESCE(NULLIF(c.last_error, ''), 'stale_stop_command_reconciled_after_slot_ready'),
        updated_at = NOW()
    FROM updated_deployments d
    WHERE c.deployment_id = d.id
      AND c.command_type = 'STOP_BOT'
      AND c.delivery_status IN ('pending', 'queued', 'dispatched')
    RETURNING 1
)
SELECT
    (SELECT COUNT(*) FROM updated_deployments) AS reconciled_stop_requested_deployments,
    (SELECT COUNT(*) FROM failed_stale_start_commands) AS failed_stale_start_commands,
    (SELECT COUNT(*) FROM acknowledged_stale_stop_commands) AS acknowledged_stale_stop_commands
