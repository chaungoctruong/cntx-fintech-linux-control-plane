SELECT
    a.id,
    a.broker,
    a.server,
    a.login,
    a.status,
    a.label,
    a.is_active,
    a.last_error,
    a.verified_at,
    a.verification_requested_at AS account_verification_requested_at,
    a.created_at,
    a.updated_at,
    v.id AS verification_job_id,
    v.status AS verification_job_status,
    v.payload_json AS verification_payload_json,
    v.requested_at AS verification_requested_at,
    v.completed_at AS verification_completed_at,
    EXISTS(
        SELECT 1 FROM account_credentials_encrypted c
        WHERE c.account_id = a.id
          AND COALESCE(NULLIF(BTRIM(c.password_encrypted), ''), '') <> ''
    ) AS has_credentials,
    d.id AS active_deployment_id,
    d.status AS active_deployment_status,
    d.runner_id,
    d.slot_id
FROM broker_accounts a
LEFT JOIN LATERAL (
    SELECT id, status, payload_json, requested_at, completed_at
    FROM account_verification_jobs
    WHERE account_id = a.id
      AND requested_at >= COALESCE(a.verification_requested_at, a.created_at)
    ORDER BY requested_at DESC, id DESC
    LIMIT 1
) v ON TRUE
LEFT JOIN LATERAL (
    SELECT id, status, runner_id, slot_id
    FROM bot_deployments
    WHERE account_id = a.id
      AND status = ANY(%s)
    ORDER BY updated_at DESC, id DESC
    LIMIT 1
) d ON TRUE
WHERE a.user_id = %s
  AND a.status <> 'disconnected'
  AND COALESCE(a.is_active, TRUE) = TRUE
ORDER BY a.updated_at DESC, a.id DESC
