SELECT
    s.runner_id,
    s.slot_id,
    s.status,
    s.current_account_id,
    s.metadata_json,
    n.status AS runner_status
FROM runner_slots s
JOIN runner_nodes n ON n.runner_id = s.runner_id
WHERE s.runner_id = %s AND s.slot_id = %s
FOR UPDATE
