SELECT
    n.runner_id,
    n.label,
    n.host,
    n.status,
    n.supported_profiles,
    n.capability_tags,
    n.capabilities_json,
    n.metadata_json,
    n.max_slots,
    n.last_registered_at,
    n.last_heartbeat_at,
    COALESCE(slot_stats.total_slots, 0) AS total_slots,
    COALESCE(slot_stats.healthy_slots, 0) AS healthy_slots,
    COALESCE(slot_stats.ready_slots, 0) AS ready_slots,
    COALESCE(slot_stats.available_slots, 0) AS available_slots,
    COALESCE(slot_stats.allocated_slots, 0) AS allocated_slots,
    COALESCE(slot_stats.degraded_slots, 0) AS degraded_slots,
    COALESCE(slot_stats.broken_slots, 0) AS broken_slots,
    COALESCE(slot_stats.stale_slots, 0) AS stale_slots,
    COALESCE(ver_stats.verifying_slots, 0) AS verifying_slots,
    latest_ver.id AS last_verification_job_id,
    latest_ver.status AS last_verification_status,
    latest_ver.last_error AS last_verification_error,
    latest_ver.completed_at AS last_verification_completed_at,
    COALESCE(dep_stats.running_deployments, 0) AS running_deployments,
    COALESCE(dep_stats.failed_deployments, 0) AS failed_deployments,
    EXTRACT(EPOCH FROM (NOW() - COALESCE(n.last_heartbeat_at, TO_TIMESTAMP(0))))::BIGINT AS heartbeat_age_sec,
    (
        n.last_heartbeat_at IS NULL
        OR n.last_heartbeat_at < (NOW() - (%s * INTERVAL '1 second'))
    ) AS is_stale
FROM runner_nodes n
LEFT JOIN LATERAL (
    SELECT
        COUNT(*) AS total_slots,
        COUNT(*) FILTER (WHERE s.status IN ('ready', 'allocated')) AS healthy_slots,
        COUNT(*) FILTER (WHERE s.status = 'ready') AS ready_slots,
        COUNT(*) FILTER (
            WHERE s.status = 'ready'
              AND GREATEST(
                  COALESCE(s.last_heartbeat_at, TO_TIMESTAMP(0)),
                  COALESCE(n.last_heartbeat_at, TO_TIMESTAMP(0))
              ) >= (NOW() - (%s * INTERVAL '1 second'))
        ) AS available_slots,
        COUNT(*) FILTER (
            WHERE s.status = 'allocated' OR s.current_account_id IS NOT NULL
        ) AS allocated_slots,
        COUNT(*) FILTER (WHERE s.status = 'degraded') AS degraded_slots,
        COUNT(*) FILTER (WHERE s.status = 'broken') AS broken_slots,
        COUNT(*) FILTER (
            WHERE GREATEST(
                COALESCE(s.last_heartbeat_at, TO_TIMESTAMP(0)),
                COALESCE(n.last_heartbeat_at, TO_TIMESTAMP(0))
            ) < (NOW() - (%s * INTERVAL '1 second'))
        ) AS stale_slots
    FROM runner_slots s
    WHERE s.runner_id = n.runner_id
      AND (
          COALESCE(NULLIF(SUBSTRING(s.slot_id FROM '([0-9]+)$'), ''), '') = ''
          OR CAST(SUBSTRING(s.slot_id FROM '([0-9]+)$') AS INTEGER) <= GREATEST(1, n.max_slots)
      )
) slot_stats ON TRUE
LEFT JOIN LATERAL (
    SELECT
        COUNT(*) FILTER (
            WHERE status IN ('pending', 'dispatched')
        ) AS verifying_slots
    FROM account_verification_jobs v
    WHERE v.runner_id = n.runner_id
) ver_stats ON TRUE
LEFT JOIN LATERAL (
    SELECT id, status, last_error, completed_at
    FROM account_verification_jobs v
    WHERE v.runner_id = n.runner_id
    ORDER BY updated_at DESC, id DESC
    LIMIT 1
) latest_ver ON TRUE
LEFT JOIN LATERAL (
    SELECT
        COUNT(*) FILTER (
            WHERE d.desired_state = 'running'
              AND d.status IN ('start_requested', 'starting', 'running', 'stop_requested')
        ) AS running_deployments,
        COUNT(*) FILTER (
            WHERE d.desired_state = 'running'
              AND d.status = 'failed'
        ) AS failed_deployments
    FROM bot_deployments d
    WHERE d.runner_id = n.runner_id
) dep_stats ON TRUE
ORDER BY n.runner_id ASC
