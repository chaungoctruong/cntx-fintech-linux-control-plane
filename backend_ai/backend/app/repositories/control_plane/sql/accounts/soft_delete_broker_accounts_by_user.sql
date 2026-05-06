UPDATE broker_accounts
SET status = 'disconnected',
    is_active = FALSE,
    last_error = %s,
    updated_at = NOW()
WHERE user_id = %s
