SELECT
    COUNT(*) AS total_accounts,
    COUNT(*) FILTER (
        WHERE verified_at IS NOT NULL
           OR status = 'connected'
    ) AS connected_accounts,
    COUNT(*) FILTER (WHERE status = 'pending_verification') AS pending_accounts
FROM broker_accounts
