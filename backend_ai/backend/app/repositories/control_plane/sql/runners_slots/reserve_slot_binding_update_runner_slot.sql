UPDATE runner_slots AS s
SET current_account_id = %s,
    status = CASE
        WHEN status = 'broken' THEN status
        ELSE 'allocated'
    END,
    updated_at = NOW()
WHERE s.runner_id = %s
  AND s.slot_id = %s
  AND status IN ('ready', 'allocated')
  AND (current_account_id IS NULL OR current_account_id = %s)
  AND EXISTS (
      SELECT 1
      FROM runner_nodes n
      WHERE n.runner_id = s.runner_id
        AND (
            COALESCE(NULLIF(SUBSTRING(s.slot_id FROM '([0-9]+)$'), ''), '') = ''
            OR CAST(SUBSTRING(s.slot_id FROM '([0-9]+)$') AS INTEGER) <= GREATEST(1, n.max_slots)
        )
        AND n.status = 'online'
        AND COALESCE(LOWER(NULLIF(BTRIM(n.metadata_json->>'maintenance_mode'), '')), 'false')
            NOT IN ('true', '1', 'yes', 'y', 'on')
        AND COALESCE(LOWER(NULLIF(BTRIM(n.metadata_json->>'maintenance'), '')), 'false')
            NOT IN ('true', '1', 'yes', 'y', 'on')
        AND COALESCE(LOWER(NULLIF(BTRIM(n.metadata_json->>'paused'), '')), 'false')
            NOT IN ('true', '1', 'yes', 'y', 'on')
        AND COALESCE(LOWER(NULLIF(BTRIM(n.metadata_json->>'frozen'), '')), 'false')
            NOT IN ('true', '1', 'yes', 'y', 'on')
        AND COALESCE(LOWER(NULLIF(BTRIM(n.metadata_json->>'dispatch_paused'), '')), 'false')
            NOT IN ('true', '1', 'yes', 'y', 'on')
        AND COALESCE(LOWER(NULLIF(BTRIM(n.metadata_json->>'verification_paused'), '')), 'false')
            NOT IN ('true', '1', 'yes', 'y', 'on')
        AND COALESCE(LOWER(NULLIF(BTRIM(n.metadata_json->>'warm_guard_paused'), '')), 'false')
            NOT IN ('true', '1', 'yes', 'y', 'on')
        AND COALESCE(LOWER(NULLIF(BTRIM(n.metadata_json->>'warm_pool_paused'), '')), 'false')
            NOT IN ('true', '1', 'yes', 'y', 'on')
        AND COALESCE(LOWER(NULLIF(BTRIM(n.metadata_json->>'runner_state'), '')), 'online')
            NOT IN ('draining', 'frozen', 'maintenance', 'paused', 'verification_paused', 'warm_guard_paused')
        AND (
            COALESCE(LOWER(NULLIF(BTRIM(n.metadata_json->>'accepting_new_accounts'), '')), 'false')
                IN ('true', '1', 'yes', 'y', 'on')
            OR NOT EXISTS (
                SELECT 1
                FROM runner_slots ds
                WHERE ds.runner_id = n.runner_id
                  AND ds.status = 'degraded'
            )
        )
        AND (
            SELECT COUNT(*)
            FROM bot_deployments d
            WHERE d.runner_id = n.runner_id
              AND d.desired_state = 'running'
              AND d.status IN ('start_requested', 'starting', 'running', 'stop_requested')
        ) < CASE
            WHEN COALESCE(n.metadata_json->>'active_limit', '') ~ '^[0-9]+$'
                THEN GREATEST(1, (n.metadata_json->>'active_limit')::INTEGER)
            ELSE %s
        END
        AND EXISTS (
            SELECT 1
            FROM runner_slots hs
            WHERE hs.runner_id = n.runner_id
              AND (
                  COALESCE(NULLIF(SUBSTRING(hs.slot_id FROM '([0-9]+)$'), ''), '') = ''
                  OR CAST(SUBSTRING(hs.slot_id FROM '([0-9]+)$') AS INTEGER) <= GREATEST(1, n.max_slots)
              )
              AND hs.status IN ('ready', 'allocated')
        )
  )
  AND NOT EXISTS (
      SELECT 1
      FROM account_slot_bindings b
      WHERE b.runner_id = s.runner_id
        AND b.slot_id = s.slot_id
        AND b.is_current = TRUE
        AND b.binding_state = 'active'
        AND b.account_id <> %s
  )
  AND (
      %s IS NOT NULL
  )
  AND (
      NULLIF(BTRIM(COALESCE(s.metadata_json->>'reserved_account_id', '')), '') IS NULL
      OR LOWER(NULLIF(BTRIM(COALESCE(s.metadata_json->>'reserved_account_id', '')), '')) IN ('null', 'none', '0')
      OR NULLIF(BTRIM(COALESCE(s.metadata_json->>'reserved_account_id', '')), '') = %s
  )
              AND (
                  COALESCE(LOWER(NULLIF(BTRIM(s.metadata_json->>'available_for_new_account'), '')), 'true')
                        NOT IN ('false', '0', 'no', 'n', 'off')
                  OR %s IS NULL
                  OR NULLIF(BTRIM(COALESCE(s.metadata_json->>'reserved_account_id', '')), '') = %s
                  OR (
                      NULLIF(BTRIM(COALESCE(s.metadata_json->>'sticky_account_id', '')), '') IS NOT NULL
                      AND LOWER(NULLIF(BTRIM(COALESCE(s.metadata_json->>'sticky_account_id', '')), '')) NOT IN ('null', 'none', '0')
                  )
              )
  AND COALESCE(LOWER(NULLIF(BTRIM(s.metadata_json->>'control_plane_state'), '')), 'ready')
        NOT IN ('allocated', 'broken', 'degraded', 'disabled', 'offline', 'running', 'starting', 'stopping', 'verifying')
  AND COALESCE(LOWER(NULLIF(BTRIM(s.metadata_json->>'current_control_plane_state'), '')), 'ready')
        NOT IN ('allocated', 'broken', 'degraded', 'disabled', 'offline', 'running', 'starting', 'stopping', 'verifying')
  AND COALESCE(LOWER(NULLIF(BTRIM(s.metadata_json->>'runner_state'), '')), 'ready')
        NOT IN ('allocated', 'broken', 'degraded', 'disabled', 'offline', 'running', 'starting', 'stopping', 'verifying')
  AND COALESCE(LOWER(NULLIF(BTRIM(s.metadata_json->>'current_runner_state'), '')), 'ready')
        NOT IN ('allocated', 'broken', 'degraded', 'disabled', 'offline', 'running', 'starting', 'stopping', 'verifying')
  AND COALESCE(LOWER(NULLIF(BTRIM(s.metadata_json->>'mt5_liveness_state'), '')), 'ready')
        NOT IN ('broken', 'dead', 'degraded', 'disabled', 'failed', 'offline', 'stale')
  AND COALESCE(LOWER(NULLIF(BTRIM(s.metadata_json->>'verification_status'), '')), '')
        NOT IN ('dispatched', 'pending', 'queued', 'running', 'verifying')
RETURNING runner_id, slot_id, status, current_account_id
