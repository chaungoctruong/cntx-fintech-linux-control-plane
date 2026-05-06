INSERT INTO position_snapshots(
    account_id, deployment_id, position_key, symbol, side,
    volume, entry_price, mark_price, pnl, payload_json,
    snapshot_at, created_at, updated_at
)
VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW(), NOW(), NOW())
ON CONFLICT(account_id, deployment_id, position_key) DO UPDATE SET
    symbol = EXCLUDED.symbol,
    side = EXCLUDED.side,
    volume = EXCLUDED.volume,
    entry_price = EXCLUDED.entry_price,
    mark_price = EXCLUDED.mark_price,
    pnl = EXCLUDED.pnl,
    payload_json = EXCLUDED.payload_json,
    snapshot_at = NOW(),
    updated_at = NOW()
