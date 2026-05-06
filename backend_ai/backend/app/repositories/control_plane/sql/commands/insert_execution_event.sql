INSERT INTO execution_events(
    event_id, event_type, account_id, deployment_id, bot_id,
    runner_id, slot_id, command_id, severity, payload_json, trace_id, created_at
)
VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, NOW())
ON CONFLICT(event_id) DO UPDATE SET
    command_id = COALESCE(EXCLUDED.command_id, execution_events.command_id),
    severity = EXCLUDED.severity,
    payload_json = EXCLUDED.payload_json,
    trace_id = COALESCE(EXCLUDED.trace_id, execution_events.trace_id)
RETURNING *
