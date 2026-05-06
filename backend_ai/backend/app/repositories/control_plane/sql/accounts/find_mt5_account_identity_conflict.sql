SELECT
    a.id,
    a.user_id,
    a.broker,
    a.server,
    a.login,
    a.status,
    a.is_active,
    a.verified_at,
    active_dep.id AS active_deployment_id,
    active_ver.id AS active_verification_job_id,
    a.created_at,
    a.updated_at
FROM broker_accounts a
LEFT JOIN LATERAL (
    SELECT d.id
    FROM bot_deployments d
    WHERE d.account_id = a.id
      AND d.desired_state = 'running'
      AND d.status IN ('start_requested', 'starting', 'running', 'stop_requested')
    ORDER BY d.updated_at DESC, d.id DESC
    LIMIT 1
) active_dep ON TRUE
LEFT JOIN LATERAL (
    SELECT v.id
    FROM account_verification_jobs v
    WHERE v.account_id = a.id
      AND v.status IN ('pending', 'dispatched')
      AND v.completed_at IS NULL
      AND COALESCE(v.updated_at, v.dispatched_at, v.requested_at)
            >= NOW() - INTERVAL '15 minutes'
    ORDER BY v.updated_at DESC, v.id DESC
    LIMIT 1
) active_ver ON TRUE
WHERE LOWER(TRIM(a.broker)) = LOWER(TRIM(%s))
  AND LOWER(TRIM(a.server)) = LOWER(TRIM(%s))
  AND TRIM(a.login) = TRIM(%s)
  AND COALESCE(a.is_active, TRUE) = TRUE
  AND a.status <> 'disconnected'
  AND (%s IS NULL OR a.id <> %s)
  AND (
      LOWER(a.status) IN ('connected', 'verified', 'pending_verification')
      OR a.verified_at IS NOT NULL
      OR active_dep.id IS NOT NULL
      OR active_ver.id IS NOT NULL
  )
ORDER BY
    CASE
        WHEN a.user_id = %s THEN 0
        WHEN LOWER(a.status) IN ('connected', 'verified', 'pending_verification') OR a.verified_at IS NOT NULL THEN 1
        WHEN active_dep.id IS NOT NULL THEN 2
        WHEN active_ver.id IS NOT NULL THEN 3
        ELSE 3
    END,
    a.updated_at DESC,
    a.id DESC
LIMIT 1
