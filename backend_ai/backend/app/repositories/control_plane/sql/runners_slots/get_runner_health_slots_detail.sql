SELECT
    s.runner_id,
    s.slot_id,
    s.status,
    s.allowed_profile_classes,
    s.current_account_id,
    s.metadata_json,
    s.last_heartbeat_at,
    n.status AS runner_status,
    n.last_heartbeat_at AS runner_last_heartbeat_at,
    GREATEST(
        COALESCE(s.last_heartbeat_at, TO_TIMESTAMP(0)),
        COALESCE(n.last_heartbeat_at, TO_TIMESTAMP(0))
    ) AS effective_last_heartbeat_at,
    EXTRACT(
        EPOCH FROM (
            NOW() - GREATEST(
                COALESCE(s.last_heartbeat_at, TO_TIMESTAMP(0)),
                COALESCE(n.last_heartbeat_at, TO_TIMESTAMP(0))
            )
        )
    )::BIGINT AS heartbeat_age_sec,
    (
        GREATEST(
            COALESCE(s.last_heartbeat_at, TO_TIMESTAMP(0)),
            COALESCE(n.last_heartbeat_at, TO_TIMESTAMP(0))
        ) < (NOW() - (%s * INTERVAL '1 second'))
    ) AS is_stale,
    bind.account_id AS sticky_account_id,
    login_hold.id AS login_reservation_id,
    login_hold.account_id AS login_reservation_account_id,
    login_hold.status AS login_reservation_status,
    login_hold.trace_id AS login_reservation_trace_id,
    login_hold.requested_at AS login_reservation_requested_at,
    login_hold.dispatched_at AS login_reservation_dispatched_at,
    login_hold.updated_at AS login_reservation_updated_at,
    dep.id AS active_deployment_id,
    dep.status AS active_deployment_status,
    dep.health_status AS active_deployment_health_status
FROM runner_slots s
JOIN runner_nodes n ON n.runner_id = s.runner_id
LEFT JOIN account_slot_bindings bind
  ON bind.runner_id = s.runner_id
 AND bind.slot_id = s.slot_id
 AND bind.is_current = TRUE
LEFT JOIN LATERAL (
    SELECT
        v.id,
        v.account_id,
        v.status,
        v.trace_id,
        v.requested_at,
        v.dispatched_at,
        v.updated_at
    FROM account_login_reservations v
    WHERE v.runner_id = s.runner_id
      AND v.slot_id = s.slot_id
      AND v.status IN ('pending', 'dispatched', 'verified')
    ORDER BY v.updated_at DESC, v.id DESC
    LIMIT 1
) login_hold ON TRUE
LEFT JOIN LATERAL (
    SELECT
        d.id,
        d.status,
        d.health_status
    FROM bot_deployments d
    WHERE d.runner_id = s.runner_id
      AND d.slot_id = s.slot_id
      AND d.desired_state = 'running'
      AND d.status IN ('start_requested', 'starting', 'running', 'stop_requested')
    ORDER BY d.updated_at DESC, d.id DESC
    LIMIT 1
) dep ON TRUE
WHERE s.runner_id = %s
  AND (
      COALESCE(NULLIF(SUBSTRING(s.slot_id FROM '([0-9]+)$'), ''), '') = ''
      OR CAST(SUBSTRING(s.slot_id FROM '([0-9]+)$') AS INTEGER) <= LEAST(10, GREATEST(1, n.max_slots))
  )
ORDER BY s.slot_id ASC
