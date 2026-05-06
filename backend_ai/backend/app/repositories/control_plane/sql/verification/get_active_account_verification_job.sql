SELECT
    id,
    user_id,
    account_id,
    runner_id,
    slot_id,
    status,
    last_error,
    trace_id,
    redis_stream_id,
    payload_json,
    requested_at,
    dispatched_at,
    completed_at,
    created_at,
    updated_at
FROM account_verification_jobs
WHERE account_id = %s
  AND status IN ('pending', 'dispatched')
ORDER BY requested_at DESC, id DESC
LIMIT 1
