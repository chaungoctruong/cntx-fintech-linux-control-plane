-- Fan-out lookup: tất cả account đang subscribe signal_id, kèm deployment + runner đang chạy.
-- Chỉ trả về subscriber có deployment đang RUNNING + active để dispatch order ngay được.
-- Nếu account subscribe nhưng bot tắt → bỏ qua (không dispatch).
SELECT
  s.id              AS subscription_id,
  s.account_id      AS account_id,
  s.signal_id       AS signal_id,
  s.bot_code        AS subscription_bot_code,
  s.volume_override AS volume_override,
  s.priority        AS subscription_priority,
  s.metadata_json   AS subscription_metadata,
  ba.broker         AS broker,
  ba.server         AS server,
  ba.login          AS login,
  ba.user_id        AS user_id,
  ba.risk_policy_json AS account_risk_policy_json,
  d.id              AS deployment_id,
  d.bot_code        AS bot_code,
  d.runner_id       AS runner_id,
  d.slot_id         AS slot_id,
  d.config_json     AS deployment_config_json,
  c.runtime_env     AS bot_runtime_env,
  c.resource_hints  AS bot_resource_hints,
  c.metadata_json   AS bot_metadata_json,
  n.capabilities_json AS runner_capabilities_json,
  n.metadata_json     AS runner_metadata_json,
  n.capability_tags   AS runner_capability_tags
FROM tradingview_signal_subscriptions s
JOIN broker_accounts ba ON ba.id = s.account_id
JOIN bot_deployments d ON d.account_id = s.account_id
JOIN bot_catalog c ON c.bot_code = d.bot_code
LEFT JOIN runner_nodes n ON n.runner_id = d.runner_id
WHERE s.signal_id = %s
  AND s.enabled = TRUE
  AND ba.is_active = TRUE
  AND c.enabled = TRUE
  AND LOWER(COALESCE(c.status, 'ACTIVE')) = 'active'
  AND d.status = 'running'
  AND d.is_active = TRUE
  AND d.runner_id IS NOT NULL
  AND d.slot_id IS NOT NULL
  AND (COALESCE(s.bot_code, '') = '' OR d.bot_code = s.bot_code)
  AND (%s = '' OR d.bot_code = %s)
  AND LOWER(COALESCE(
        NULLIF(BTRIM(c.runtime_env->>'bot_type'), ''),
        NULLIF(BTRIM(c.resource_hints->>'bot_type'), ''),
        ''
      )) = 'backend_webhook_signal'
  AND LOWER(COALESCE(
        NULLIF(BTRIM(c.runtime_env->>'windows_role'), ''),
        NULLIF(BTRIM(c.resource_hints->>'windows_role'), ''),
        ''
      )) = 'mt5_executor_only'
  AND LOWER(COALESCE(
        NULLIF(BTRIM(c.runtime_env->>'execution_owner'), ''),
        NULLIF(BTRIM(c.resource_hints->>'execution_owner'), ''),
        ''
      )) = 'linux_backend'
  AND LOWER(COALESCE(
        NULLIF(BTRIM(c.runtime_env->>'tradingview_webhook_owner'), ''),
        NULLIF(BTRIM(c.resource_hints->>'tradingview_webhook_owner'), ''),
        ''
      )) = 'linux'
ORDER BY s.priority DESC, s.id ASC
LIMIT %s
