-- Fan-out lookup: tất cả account đang subscribe signal_id, kèm deployment + runner đang chạy.
-- Chỉ trả về subscriber có deployment đang RUNNING + active để dispatch order ngay được.
-- Nếu account subscribe nhưng bot tắt → bỏ qua (không dispatch).
SELECT
  s.id              AS subscription_id,
  s.account_id      AS account_id,
  s.signal_id       AS signal_id,
  s.volume_override AS volume_override,
  s.priority        AS subscription_priority,
  s.metadata_json   AS subscription_metadata,
  ba.broker         AS broker,
  ba.server         AS server,
  ba.login          AS login,
  ba.user_id        AS user_id,
  d.id              AS deployment_id,
  d.bot_code        AS bot_code,
  d.runner_id       AS runner_id,
  d.slot_id         AS slot_id,
  d.config_json     AS deployment_config_json
FROM tradingview_signal_subscriptions s
JOIN broker_accounts ba ON ba.id = s.account_id
JOIN bot_deployments d ON d.account_id = s.account_id
WHERE s.signal_id = %s
  AND s.enabled = TRUE
  AND ba.is_active = TRUE
  AND d.status = 'running'
  AND d.is_active = TRUE
  AND d.runner_id IS NOT NULL
  AND d.slot_id IS NOT NULL
ORDER BY s.priority DESC, s.id ASC
LIMIT %s
