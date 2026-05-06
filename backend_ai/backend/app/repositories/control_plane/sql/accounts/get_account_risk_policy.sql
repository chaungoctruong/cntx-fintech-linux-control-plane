SELECT id, risk_policy_json
FROM broker_accounts
WHERE id = %s AND user_id = %s
