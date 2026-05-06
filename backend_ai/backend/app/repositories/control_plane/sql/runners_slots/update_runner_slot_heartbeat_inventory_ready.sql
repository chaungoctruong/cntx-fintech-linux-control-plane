UPDATE runner_slots AS s
SET status = 'ready',
    current_account_id = NULL,
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
        ) || %s::jsonb || jsonb_build_object(
            'control_plane_state', 'ready',
            'current_control_plane_state', 'ready'
        )
    ),
    last_heartbeat_at = NOW(),
    updated_at = NOW()
WHERE s.runner_id = %s
  AND s.slot_id = %s
  AND NOT EXISTS (
      SELECT 1
      FROM bot_deployments d
      WHERE d.runner_id = s.runner_id
        AND d.slot_id = s.slot_id
        AND d.status IN ('start_requested', 'starting', 'running', 'stop_requested')
        AND (COALESCE(d.is_active, FALSE) = TRUE OR d.desired_state = 'running')
  )
  AND NOT EXISTS (
      SELECT 1
      FROM account_verification_jobs v
      WHERE v.runner_id = s.runner_id
        AND v.slot_id = s.slot_id
        AND v.status IN ('pending', 'dispatched')
  )
  AND NOT EXISTS (
      SELECT 1
      FROM execution_commands c
      WHERE c.runner_id = s.runner_id
        AND c.slot_id = s.slot_id
        AND c.delivery_status IN ('pending', 'queued', 'dispatched')
  )
  AND (
      NULLIF(BTRIM(COALESCE(s.metadata_json->>'auto_quarantine_until', '')), '') IS NULL
      OR NOT (
          NULLIF(BTRIM(COALESCE(s.metadata_json->>'auto_quarantine_until', '')), '') ~ '^[0-9]{4}-'
          AND (NULLIF(BTRIM(COALESCE(s.metadata_json->>'auto_quarantine_until', '')), ''))::TIMESTAMPTZ > NOW()
      )
  )
