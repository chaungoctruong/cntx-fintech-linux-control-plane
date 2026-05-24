SELECT
    COUNT(*) AS total_slots,
    COUNT(*) FILTER (WHERE s.status = 'ready') AS ready_slots,
    COUNT(*) FILTER (WHERE s.status = 'allocated') AS allocated_slots,
    COUNT(*) FILTER (WHERE s.status = 'degraded') AS degraded_slots,
    COUNT(*) FILTER (WHERE s.status = 'broken') AS broken_slots
FROM runner_slots s
JOIN runner_nodes n ON n.runner_id = s.runner_id
WHERE (
    COALESCE(NULLIF(SUBSTRING(s.slot_id FROM '([0-9]+)$'), ''), '') = ''
    OR CAST(SUBSTRING(s.slot_id FROM '([0-9]+)$') AS INTEGER) <= LEAST(12, GREATEST(1, n.max_slots))
)
