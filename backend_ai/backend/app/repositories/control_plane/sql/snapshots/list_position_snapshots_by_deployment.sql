SELECT
    p.id,
    p.account_id,
    p.deployment_id,
    p.position_key,
    p.symbol,
    p.side,
    p.volume,
    p.entry_price,
    p.mark_price,
    p.pnl,
    p.snapshot_at,
    p.payload_json
FROM position_snapshots p
JOIN broker_accounts a ON a.id = p.account_id
WHERE p.account_id = %s
  AND a.user_id = %s
  AND p.deployment_id = %s
ORDER BY p.snapshot_at DESC, p.id DESC
LIMIT %s
