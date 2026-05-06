UPDATE broker_accounts
SET risk_policy_json = %s::jsonb,
    updated_at = NOW()
WHERE id = %s AND user_id = %s
RETURNING risk_policy_json
