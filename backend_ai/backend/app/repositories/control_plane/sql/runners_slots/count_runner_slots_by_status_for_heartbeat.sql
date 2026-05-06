SELECT
    COUNT(*) AS total_slots,
    COUNT(*) FILTER (WHERE status = 'ready') AS ready_slots,
    COUNT(*) FILTER (WHERE status = 'allocated') AS allocated_slots,
    COUNT(*) FILTER (WHERE status = 'degraded') AS degraded_slots,
    COUNT(*) FILTER (WHERE status = 'broken') AS broken_slots,
    COUNT(*) FILTER (WHERE status = 'verifying') AS verifying_slots
FROM runner_slots
WHERE runner_id = %s
  AND (
      COALESCE(NULLIF(SUBSTRING(slot_id FROM '([0-9]+)$'), ''), '') = ''
      OR CAST(SUBSTRING(slot_id FROM '([0-9]+)$') AS INTEGER) <= GREATEST(1, %s)
  )
