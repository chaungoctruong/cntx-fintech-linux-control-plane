WITH active_login_reservations AS (
    SELECT runner_id, slot_id, COUNT(*) AS active_count
    FROM account_login_reservations
    WHERE runner_id = %s
      AND status IN ('pending', 'dispatched', 'verified')
      AND slot_id IS NOT NULL
    GROUP BY runner_id, slot_id
)
SELECT
    COUNT(*) AS total_slots,
    COUNT(*) FILTER (WHERE status = 'ready') AS ready_slots,
    COUNT(*) FILTER (WHERE status = 'allocated') AS allocated_slots,
    COUNT(*) FILTER (WHERE status = 'degraded') AS degraded_slots,
    COUNT(*) FILTER (WHERE status = 'broken') AS broken_slots,
    COUNT(*) FILTER (WHERE COALESCE(v.active_count, 0) > 0) AS login_reserved_slots
FROM runner_slots s
LEFT JOIN active_login_reservations v
  ON v.runner_id = s.runner_id
 AND v.slot_id = s.slot_id
WHERE s.runner_id = %s
  AND (
      COALESCE(NULLIF(SUBSTRING(s.slot_id FROM '([0-9]+)$'), ''), '') = ''
      OR CAST(SUBSTRING(s.slot_id FROM '([0-9]+)$') AS INTEGER) <= GREATEST(1, %s)
  )
