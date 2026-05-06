SELECT COUNT(*)::INT AS n
FROM broker_accounts
WHERE user_id = %s
  AND COALESCE((risk_policy_json->>'daily_loss_limit_usd')::NUMERIC, 0) > 0
