INSERT INTO user_webhooks(user_id, url, secret_hex, event_filter, is_active, created_at, updated_at)
VALUES(%s, %s, %s, %s::jsonb, TRUE, NOW(), NOW())
RETURNING id, user_id, url, secret_hex, event_filter, is_active,
          last_delivered_at, last_error, fail_count,
          EXTRACT(EPOCH FROM created_at)::BIGINT AS created_at,
          EXTRACT(EPOCH FROM updated_at)::BIGINT AS updated_at
