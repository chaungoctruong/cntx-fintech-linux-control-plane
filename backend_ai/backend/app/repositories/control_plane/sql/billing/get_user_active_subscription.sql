SELECT id, user_id, plan_code, status, renews_at, metadata_json,
       created_at, updated_at
FROM billing_subscriptions
WHERE user_id = %s AND status = 'active'
ORDER BY updated_at DESC, id DESC
LIMIT 1
