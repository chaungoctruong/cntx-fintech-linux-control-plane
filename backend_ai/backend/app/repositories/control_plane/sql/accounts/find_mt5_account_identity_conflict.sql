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
    active_login.id AS active_login_reservation_id,
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
    FROM account_login_reservations v
    WHERE v.account_id = a.id
      AND v.status IN ('pending', 'dispatched', 'verified')
      AND (v.expires_at IS NULL OR v.expires_at > NOW())
    ORDER BY v.updated_at DESC, v.id DESC
    LIMIT 1
) active_login ON TRUE
WHERE LOWER(TRIM(a.broker)) = LOWER(TRIM(%s))
  AND LOWER(TRIM(a.server)) = LOWER(TRIM(%s))
  AND TRIM(a.login) = TRIM(%s)
  AND COALESCE(a.is_active, TRUE) = TRUE
  AND a.status <> 'disconnected'
  AND (%s IS NULL OR a.id <> %s)
  AND (
      LOWER(a.status) IN ('connected', 'verified', 'pending_login')
      OR a.verified_at IS NOT NULL
      OR active_dep.id IS NOT NULL
      OR active_login.id IS NOT NULL
  )
ORDER BY
    CASE
        WHEN a.user_id = %s THEN 0
        WHEN LOWER(a.status) IN ('connected', 'verified', 'pending_login') OR a.verified_at IS NOT NULL THEN 1
        WHEN active_dep.id IS NOT NULL THEN 2
        WHEN active_login.id IS NOT NULL THEN 3
        ELSE 3
    END,
    a.updated_at DESC,
    a.id DESC
LIMIT 1
