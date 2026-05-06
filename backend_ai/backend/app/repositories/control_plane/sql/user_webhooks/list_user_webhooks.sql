SELECT id, user_id, url, event_filter, is_active, last_delivered_at, last_error, fail_count,
       EXTRACT(EPOCH FROM created_at)::BIGINT AS created_at,
       EXTRACT(EPOCH FROM updated_at)::BIGINT AS updated_at
FROM user_webhooks
WHERE user_id = %s
ORDER BY id ASC
