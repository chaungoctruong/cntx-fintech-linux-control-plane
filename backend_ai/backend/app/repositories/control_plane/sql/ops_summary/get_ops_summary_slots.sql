WITH active_login_reservations AS (
    SELECT runner_id, slot_id, COUNT(*) AS active_count
    FROM account_login_reservations
    WHERE status IN ('pending', 'dispatched', 'verified')
      AND runner_id IS NOT NULL
      AND slot_id IS NOT NULL
    GROUP BY runner_id, slot_id
),
active_bindings AS (
    SELECT runner_id, slot_id, COUNT(*) AS active_count
    FROM account_slot_bindings
    WHERE is_current = TRUE
      AND binding_state = 'active'
    GROUP BY runner_id, slot_id
)
SELECT
    COUNT(*) AS total,
    COUNT(*) FILTER (WHERE s.status = 'ready') AS ready,
    COUNT(*) FILTER (
        WHERE s.status = 'allocated'
           OR s.current_account_id IS NOT NULL
           OR COALESCE(b.active_count, 0) > 0
    ) AS active,
    COUNT(*) FILTER (WHERE COALESCE(v.active_count, 0) > 0) AS login_reserved,
    COUNT(*) FILTER (WHERE s.status = 'degraded') AS degraded,
    COUNT(*) FILTER (WHERE s.status = 'broken') AS broken,
    COUNT(*) FILTER (
    WHERE s.status = 'ready'
      AND s.current_account_id IS NULL
      AND COALESCE(b.active_count, 0) = 0
      AND COALESCE(v.active_count, 0) = 0
      AND n.status = 'online'
          AND GREATEST(
              COALESCE(s.last_heartbeat_at, TO_TIMESTAMP(0)),
              COALESCE(n.last_heartbeat_at, TO_TIMESTAMP(0))
          ) >= (NOW() - (%s * INTERVAL '1 second'))
    ) AS available
FROM runner_slots s
JOIN runner_nodes n ON n.runner_id = s.runner_id
LEFT JOIN active_login_reservations v
  ON v.runner_id = s.runner_id
 AND v.slot_id = s.slot_id
LEFT JOIN active_bindings b
  ON b.runner_id = s.runner_id
 AND b.slot_id = s.slot_id
WHERE (
    COALESCE(NULLIF(SUBSTRING(s.slot_id FROM '([0-9]+)$'), ''), '') = ''
    OR CAST(SUBSTRING(s.slot_id FROM '([0-9]+)$') AS INTEGER) <= LEAST(10, GREATEST(1, n.max_slots))
)
