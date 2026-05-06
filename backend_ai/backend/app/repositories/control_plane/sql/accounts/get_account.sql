SELECT
    a.id,
    a.user_id,
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
    ) AS has_credentials
FROM broker_accounts a
LEFT JOIN LATERAL (
    SELECT id, status, payload_json, requested_at, completed_at
    FROM account_verification_jobs
    WHERE account_id = a.id
      AND requested_at >= COALESCE(a.verification_requested_at, a.created_at)
    ORDER BY requested_at DESC, id DESC
    LIMIT 1
) v ON TRUE
WHERE a.id = %s AND a.user_id = %s
