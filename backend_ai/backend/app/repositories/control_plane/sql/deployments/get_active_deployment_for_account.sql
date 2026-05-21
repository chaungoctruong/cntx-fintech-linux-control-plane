SELECT
  d.*,
  n.capabilities_json AS runner_capabilities_json,
  n.metadata_json AS runner_metadata_json,
  n.capability_tags AS runner_capability_tags
FROM bot_deployments d
LEFT JOIN runner_nodes n ON n.runner_id = d.runner_id
WHERE d.account_id = %s
  AND d.status = ANY(%s)
ORDER BY d.updated_at DESC, d.id DESC
LIMIT 1
