WITH failed_events AS (
    SELECT DISTINCT ON (e.deployment_id)
        e.deployment_id,
        e.account_id,
        e.runner_id,
        e.slot_id,
        NULLIF(BTRIM(COALESCE(e.command_id, e.payload_json->>'command_id', '')), '') AS command_id,
        LEFT(
            COALESCE(
                NULLIF(BTRIM(e.payload_json->>'exact_exception'), ''),
                NULLIF(BTRIM(e.payload_json->>'message'), ''),
                NULLIF(BTRIM(e.payload_json->>'reason'), ''),
                'start_bootstrap_failed'
            ),
            200
        ) AS reason
    FROM execution_events e
    WHERE e.event_type IN ('RUNTIME_LOG', 'SLOT_STATE_CHANGED')
      AND e.deployment_id IS NOT NULL
      AND (
          LOWER(COALESCE(e.payload_json->>'exact_exception', '')) LIKE 'slot_bootstrap_failed:fatal_%%'
          OR LOWER(COALESCE(e.payload_json->>'message', '')) LIKE 'slot_bootstrap_failed:fatal_%%'
          OR LOWER(COALESCE(e.payload_json->>'reason', '')) = 'start_bot_command_bootstrap_failed'
      )
    ORDER BY e.deployment_id, e.created_at DESC, e.id DESC
),
candidate_deployments AS (
    SELECT
        d.id,
        d.account_id,
        d.runner_id,
        d.slot_id,
        d.binding_id,
        failed_events.command_id,
        failed_events.reason
    FROM bot_deployments d
    JOIN failed_events ON failed_events.deployment_id = d.id
    WHERE d.status IN ('start_requested', 'starting')
      AND d.desired_state = 'running'
      AND COALESCE(d.is_active, FALSE) = TRUE
      AND NOT EXISTS (
          SELECT 1
          FROM bot_deployments other
          WHERE other.runner_id = d.runner_id
            AND other.slot_id = d.slot_id
            AND other.id <> d.id
            AND other.status = ANY(%s)
      )
    FOR UPDATE OF d SKIP LOCKED
),
updated_deployments AS (
    UPDATE bot_deployments d
    SET status = 'failed',
        desired_state = 'stopped',
        is_active = FALSE,
        health_status = 'bootstrap_failed',
        last_error = COALESCE(NULLIF(d.last_error, ''), c.reason),
        stopped_at = COALESCE(d.stopped_at, NOW()),
        updated_at = NOW()
    FROM candidate_deployments c
    WHERE d.id = c.id
    RETURNING d.id, d.runner_id, d.slot_id, d.binding_id, c.command_id, c.reason
),
failed_start_commands AS (
    UPDATE execution_commands c
    SET delivery_status = 'failed',
        last_error = COALESCE(NULLIF(c.last_error, ''), d.reason),
        payload_json = COALESCE(c.payload_json, '{}'::jsonb)
            || jsonb_build_object(
                'delivery_status', 'failed',
                'last_event_type', 'RUNTIME_LOG',
                'failure_reason', d.reason
            ),
        updated_at = NOW()
    FROM updated_deployments d
    WHERE c.deployment_id = d.id
      AND c.command_type = 'START_BOT'
      AND (d.command_id IS NULL OR c.command_id = d.command_id)
      AND c.delivery_status <> 'failed'
    RETURNING 1
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
                    - 'verification_job_id'
                    - 'verification_status'
                    - 'verification_account_id'
                    - 'verification_attempt'
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
                'start_reconcile_reason', 'start_bootstrap_failure_reconciled'
            )
        ),
        updated_at = NOW()
    FROM updated_deployments d
    WHERE s.runner_id = d.runner_id
      AND s.slot_id = d.slot_id
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
)
SELECT
    (SELECT COUNT(*) FROM updated_deployments) AS reconciled_start_bootstrap_failures,
    (SELECT COUNT(*) FROM failed_start_commands) AS failed_bootstrap_start_commands,
    (SELECT COUNT(*) FROM released_slots) AS released_bootstrap_failure_slots,
    (SELECT COUNT(*) FROM refreshed_bindings) AS refreshed_bootstrap_failure_bindings
