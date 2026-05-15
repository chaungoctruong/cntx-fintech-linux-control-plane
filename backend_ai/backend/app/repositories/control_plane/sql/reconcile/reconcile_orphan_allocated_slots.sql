WITH candidate_slots AS (
    SELECT
        s.runner_id,
        s.slot_id
    FROM runner_slots s
    JOIN runner_nodes n ON n.runner_id = s.runner_id
    WHERE s.status = 'allocated'
      AND s.current_account_id IS NULL
      AND n.status = 'online'
      AND n.last_heartbeat_at IS NOT NULL
      AND n.last_heartbeat_at >= (NOW() - (%s * INTERVAL '1 second'))
      AND COALESCE(s.updated_at, s.last_heartbeat_at, NOW()) < (NOW() - (%s * INTERVAL '1 second'))
      AND NOT EXISTS (
          SELECT 1
          FROM bot_deployments d
          WHERE d.runner_id = s.runner_id
            AND d.slot_id = s.slot_id
            AND (
                d.status IN ('start_requested', 'starting', 'running', 'stop_requested')
                OR d.desired_state = 'running'
                OR COALESCE(d.is_active, FALSE) = TRUE
            )
      )
      AND NOT EXISTS (
          SELECT 1
          FROM account_login_reservations r
          WHERE r.runner_id = s.runner_id
            AND r.slot_id = s.slot_id
            AND r.status IN ('pending', 'dispatched', 'verified')
            AND (r.expires_at IS NULL OR r.expires_at > NOW())
      )
      AND NOT EXISTS (
          SELECT 1
          FROM execution_commands c
          WHERE c.runner_id = s.runner_id
            AND c.slot_id = s.slot_id
            AND c.delivery_status IN ('pending', 'queued', 'dispatched')
      )
),
updated_slots AS (
    UPDATE runner_slots s
    SET status = 'ready',
        current_account_id = NULL,
        metadata_json = jsonb_strip_nulls(
            (
                COALESCE(s.metadata_json, '{}'::jsonb)
                    - 'account_id'
                    - 'active_account_id'
                    - 'deployment_id'
                    - 'active_deployment_id'
                    - 'login_reservation_id'
                    - 'login_reservation_status'
                    - 'login_reservation_account_id'
                    - 'login_slot_status'
                    - 'login_slot_account_id'
                    - 'reserved_account_id'
                    - 'sticky_account_id'
                    - 'current_control_plane_state'
                    - 'previous_control_plane_state'
                    - 'current_runner_state'
                    - 'previous_runner_state'
                    - 'current_state'
                    - 'previous_state'
                    - 'runner_state'
                    - 'reason'
                    - 'last_error'
            ) || jsonb_build_object(
                'control_plane_state', 'ready',
                'current_control_plane_state', 'ready',
                'runner_state', 'ready',
                'current_runner_state', 'ready',
                'available_for_new_account', TRUE,
                'runtime_reconcile_reason', 'orphan_allocated_slot_released'
            )
        ),
        updated_at = NOW()
    FROM candidate_slots c
    WHERE s.runner_id = c.runner_id
      AND s.slot_id = c.slot_id
    RETURNING s.runner_id, s.slot_id
),
released_bindings AS (
    UPDATE account_slot_bindings b
    SET binding_state = 'released',
        is_current = FALSE,
        is_sticky = FALSE,
        updated_at = NOW()
    FROM updated_slots s
    WHERE b.runner_id = s.runner_id
      AND b.slot_id = s.slot_id
      AND b.is_current = TRUE
      AND b.binding_state IN ('active', 'reserved', 'sticky')
    RETURNING 1
)
SELECT
    (SELECT COUNT(*) FROM updated_slots) AS reconciled_orphan_allocated_slots,
    (SELECT COUNT(*) FROM released_bindings) AS released_orphan_allocated_bindings
