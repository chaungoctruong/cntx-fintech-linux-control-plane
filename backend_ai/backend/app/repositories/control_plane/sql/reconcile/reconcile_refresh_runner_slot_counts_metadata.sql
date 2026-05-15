WITH slot_stats AS (
    SELECT
        n.runner_id,
        COUNT(s.slot_id) AS total_slots,
        COUNT(*) FILTER (WHERE s.status = 'ready') AS ready_slots,
        COUNT(*) FILTER (WHERE s.status = 'allocated') AS allocated_slots,
        COUNT(*) FILTER (WHERE s.status = 'degraded') AS degraded_slots,
        COUNT(*) FILTER (WHERE s.status = 'broken') AS broken_slots
    FROM runner_nodes n
    LEFT JOIN runner_slots s
      ON s.runner_id = n.runner_id
     AND (
         COALESCE(NULLIF(SUBSTRING(s.slot_id FROM '([0-9]+)$'), ''), '') = ''
         OR CAST(SUBSTRING(s.slot_id FROM '([0-9]+)$') AS INTEGER) <= LEAST(10, GREATEST(1, n.max_slots))
     )
    GROUP BY n.runner_id
),
login_reservation_stats AS (
    SELECT
        runner_id,
        COUNT(*) FILTER (WHERE status IN ('pending', 'dispatched', 'verified')) AS login_reserved_slots
    FROM account_login_reservations
    WHERE runner_id IS NOT NULL
    GROUP BY runner_id
)
UPDATE runner_nodes n
SET metadata_json = COALESCE(n.metadata_json, '{}'::jsonb)
    || jsonb_build_object(
        'reported_slots_total', COALESCE(slot_stats.total_slots, 0),
        'reported_slots_ready', COALESCE(slot_stats.ready_slots, 0),
        'reported_ready_slots', COALESCE(slot_stats.ready_slots, 0),
        'reported_slots_active', COALESCE(slot_stats.allocated_slots, 0),
        'reported_active_slots', COALESCE(slot_stats.allocated_slots, 0),
        'reported_slots_degraded', COALESCE(slot_stats.degraded_slots, 0),
        'reported_degraded_slots', COALESCE(slot_stats.degraded_slots, 0),
        'reported_slots_broken', COALESCE(slot_stats.broken_slots, 0),
        'reported_broken_slots', COALESCE(slot_stats.broken_slots, 0),
        'reported_slots_login_reserved', COALESCE(login_reservation_stats.login_reserved_slots, 0),
        'reported_login_reserved_slots', COALESCE(login_reservation_stats.login_reserved_slots, 0)
    ),
    updated_at = NOW()
FROM slot_stats
LEFT JOIN login_reservation_stats ON login_reservation_stats.runner_id = slot_stats.runner_id
WHERE n.runner_id = slot_stats.runner_id
