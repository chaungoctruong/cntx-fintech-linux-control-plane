INSERT INTO runtime_logs(
    account_id, deployment_id, runner_id, slot_id, level,
    message, payload_json, trace_id, created_at
)
VALUES(%s, %s, %s, %s, %s, %s, %s::jsonb, %s, NOW())
