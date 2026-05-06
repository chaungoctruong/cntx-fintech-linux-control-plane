SELECT
    id AS account_id,
    user_id,
    risk_policy_json
FROM broker_accounts
WHERE risk_policy_json ? 'daily_loss_limit_usd'
  AND COALESCE((risk_policy_json->>'daily_loss_limit_usd')::NUMERIC, 0) > 0
  AND COALESCE((risk_policy_json->>'auto_stop_on_breach')::BOOLEAN, FALSE) = TRUE
  AND status = 'connected'
ORDER BY id ASC
LIMIT %s
