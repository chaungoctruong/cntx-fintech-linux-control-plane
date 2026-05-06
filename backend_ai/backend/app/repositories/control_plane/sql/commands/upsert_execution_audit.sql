INSERT INTO execution_audit(
    event_id, command_id, trace_id, account_id, deployment_id,
    runner_id, slot_id, event_type, severity, audit_status,
    payload_json, source_stream_id, created_at, processed_at
)
VALUES(
    %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s,
    %s::jsonb, %s, NOW(), NOW()
)
ON CONFLICT(event_id) DO UPDATE SET
    command_id = COALESCE(EXCLUDED.command_id, execution_audit.command_id),
    trace_id = COALESCE(EXCLUDED.trace_id, execution_audit.trace_id),
    severity = EXCLUDED.severity,
    audit_status = EXCLUDED.audit_status,
    payload_json = EXCLUDED.payload_json,
    source_stream_id = COALESCE(EXCLUDED.source_stream_id, execution_audit.source_stream_id),
    processed_at = NOW()
RETURNING *
