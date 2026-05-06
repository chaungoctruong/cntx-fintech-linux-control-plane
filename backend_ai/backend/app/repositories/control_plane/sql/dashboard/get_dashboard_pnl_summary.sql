SELECT COALESCE(SUM(pnl), 0) AS total_pnl
FROM account_state_snapshots snap
JOIN broker_accounts acc ON acc.id = snap.account_id
WHERE acc.user_id = %s
