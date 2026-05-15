SELECT
    n.runner_id,
    n.label,
    n.host,
    n.status,
    n.supported_profiles,
    n.capability_tags,
    n.capabilities_json,
    n.metadata_json,
    n.max_slots,
    n.last_registered_at,
    n.last_heartbeat_at,
    COALESCE(slot_stats.total_slots, 0) AS total_slots,
    COALESCE(slot_stats.allocated_slots, 0) AS allocated_slots,
    COALESCE(slot_stats.broken_slots, 0) AS broken_slots
FROM runner_nodes n
LEFT JOIN LATERAL (
    SELECT
        COUNT(*) AS total_slots,
        COUNT(*) FILTER (WHERE current_account_id IS NOT NULL) AS allocated_slots,
        COUNT(*) FILTER (WHERE status = 'broken') AS broken_slots
    FROM runner_slots s
    WHERE s.runner_id = n.runner_id
      AND (
          COALESCE(NULLIF(SUBSTRING(s.slot_id FROM '([0-9]+)$'), ''), '') = ''
          OR CAST(SUBSTRING(s.slot_id FROM '([0-9]+)$') AS INTEGER) <= LEAST(10, GREATEST(1, n.max_slots))
      )
) slot_stats ON TRUE
ORDER BY n.runner_id ASC
