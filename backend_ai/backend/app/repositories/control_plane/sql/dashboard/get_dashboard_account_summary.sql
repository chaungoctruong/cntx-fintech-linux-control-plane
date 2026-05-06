SELECT
    COUNT(*) AS account_count,
    COUNT(*) FILTER (
        WHERE verified_at IS NOT NULL
           OR status = 'connected'
    ) AS connected_account_count
FROM broker_accounts
WHERE user_id = %s
