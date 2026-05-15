WITH slot_state_events AS (
    SELECT DISTINCT ON (runner_id, slot_id)
        runner_id,
        slot_id,
        payload_json,
        id,
        created_at,
        CASE
            WHEN COALESCE(payload_json->>'event_at', '') ~ '^[0-9]{4}-'
                THEN (payload_json->>'event_at')::timestamptz
            ELSE created_at
        END AS event_order_at
    FROM execution_events
    WHERE event_type = 'SLOT_STATE_CHANGED'
      AND runner_id IS NOT NULL
      AND slot_id IS NOT NULL
      AND slot_id <> ''
      AND created_at >= (NOW() - (%s * INTERVAL '1 second'))
    ORDER BY runner_id, slot_id, event_order_at DESC, created_at DESC, id DESC
),
latest_heartbeat_events AS (
    SELECT DISTINCT ON (runner_id)
        event_id,
        runner_id,
        payload_json,
        id,
        created_at
    FROM execution_events
    WHERE event_type = 'HEARTBEAT'
      AND runner_id IS NOT NULL
      AND created_at >= (NOW() - (%s * INTERVAL '1 second'))
    ORDER BY runner_id, created_at DESC, id DESC
),
heartbeat_slot_events AS (
    SELECT
        e.runner_id,
        REPLACE(NULLIF(BTRIM(COALESCE(entry->>'slot_id', entry->>'storage_slot_id', '')), ''), 'slot_', 'slot-') AS slot_id,
        jsonb_strip_nulls(
            entry
            || jsonb_build_object(
                'slot_inventory_entry', entry,
                'event_type', 'HEARTBEAT_SLOT_INVENTORY',
                'heartbeat_event_id', e.event_id,
                'heartbeat_created_at', e.created_at
            )
        ) AS payload_json,
        e.id,
        e.created_at,
        e.created_at AS event_order_at
    FROM latest_heartbeat_events e
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN jsonb_typeof(e.payload_json->'slot_inventory') = 'array'
                THEN e.payload_json->'slot_inventory'
            ELSE '[]'::jsonb
        END
    ) AS inv(entry)
    WHERE NULLIF(BTRIM(COALESCE(entry->>'slot_id', entry->>'storage_slot_id', '')), '') IS NOT NULL
),
latest_slot_events AS (
    SELECT DISTINCT ON (runner_id, slot_id)
        runner_id,
        slot_id,
        payload_json,
        created_at,
        event_order_at
    FROM (
        SELECT * FROM slot_state_events
        UNION ALL
        SELECT * FROM heartbeat_slot_events
    ) events
    ORDER BY runner_id, slot_id, event_order_at DESC, created_at DESC, id DESC
),
	normalized AS (
	    SELECT
	        runner_id,
	        slot_id,
	        payload_json,
	        created_at,
	        (
	            COALESCE(NULLIF(SUBSTRING(slot_id FROM '([0-9]+)$'), ''), '') <> ''
	            AND CAST(SUBSTRING(slot_id FROM '([0-9]+)$') AS INTEGER) > 10
	        ) AS over_node_slot_cap,
	        CASE
	            WHEN (
	                COALESCE(NULLIF(SUBSTRING(slot_id FROM '([0-9]+)$'), ''), '') <> ''
	                AND CAST(SUBSTRING(slot_id FROM '([0-9]+)$') AS INTEGER) > 10
	            ) THEN 'disabled'
	            WHEN LOWER(COALESCE(
                payload_json->>'current_control_plane_state',
                payload_json->>'control_plane_state',
                payload_json->>'new_state',
                payload_json->>'slot_state',
                payload_json->>'to_state',
                payload_json->>'runner_state',
                payload_json->>'current_state',
                payload_json->>'current_runner_state',
                ''
            )) IN ('ready', 'empty', 'stopped') THEN 'ready'
            WHEN LOWER(COALESCE(
                payload_json->>'current_control_plane_state',
                payload_json->>'control_plane_state',
                payload_json->>'new_state',
                payload_json->>'slot_state',
                payload_json->>'to_state',
                payload_json->>'runner_state',
                payload_json->>'current_state',
                payload_json->>'current_runner_state',
                ''
            )) IN ('allocated', 'active', 'running', 'verifying', 'preparing', 'stopping') THEN 'allocated'
            WHEN LOWER(COALESCE(
                payload_json->>'current_control_plane_state',
                payload_json->>'control_plane_state',
                payload_json->>'new_state',
                payload_json->>'slot_state',
                payload_json->>'to_state',
                payload_json->>'runner_state',
                payload_json->>'current_state',
                payload_json->>'current_runner_state',
                ''
            )) IN ('degraded', 'rebuilding') THEN 'degraded'
            WHEN LOWER(COALESCE(
                payload_json->>'current_control_plane_state',
                payload_json->>'control_plane_state',
                payload_json->>'new_state',
                payload_json->>'slot_state',
                payload_json->>'to_state',
                payload_json->>'runner_state',
                payload_json->>'current_state',
                payload_json->>'current_runner_state',
                ''
            )) = 'broken' THEN 'broken'
            WHEN LOWER(COALESCE(
                payload_json->>'current_control_plane_state',
                payload_json->>'control_plane_state',
                payload_json->>'new_state',
                payload_json->>'slot_state',
                payload_json->>'to_state',
                payload_json->>'runner_state',
                payload_json->>'current_state',
                payload_json->>'current_runner_state',
                ''
            )) = 'disabled' THEN 'disabled'
            ELSE NULL
        END AS slot_status
    FROM latest_slot_events
)
UPDATE runner_slots s
SET status = normalized.slot_status,
    current_account_id = CASE
        WHEN normalized.slot_status IN ('ready', 'disabled') THEN NULL
        ELSE s.current_account_id
    END,
    metadata_json = CASE
        WHEN normalized.over_node_slot_cap THEN
            jsonb_strip_nulls(
                (
                    COALESCE(s.metadata_json, '{}'::jsonb)
                        - 'account_id'
                        - 'active_account_id'
                        - 'deployment_id'
                        - 'login_reservation_id'
                        - 'login_reservation_status'
                        - 'login_reservation_account_id'
                        - 'login_slot_status'
                        - 'login_slot_account_id'
                        - 'reserved_account_id'
                        - 'sticky_account_id'
                        - 'slot_inventory_entry'
                ) || (
                    normalized.payload_json
                        - 'account_id'
                        - 'active_account_id'
                        - 'deployment_id'
                        - 'login_reservation_id'
                        - 'login_reservation_status'
                        - 'login_reservation_account_id'
                        - 'login_slot_status'
                        - 'login_slot_account_id'
                        - 'reserved_account_id'
                        - 'sticky_account_id'
                        - 'slot_inventory_entry'
                )
                || jsonb_build_object(
                    'disabled_by_node_slot_cap', TRUE,
                    'node_slot_cap', 10,
                    'disabled_reason', 'node_slot_cap_10',
                    'available_for_new_account', FALSE,
                    'control_plane_state', 'disabled',
                    'current_control_plane_state', 'disabled'
                )
            )
        WHEN normalized.slot_status = 'ready' THEN
            jsonb_strip_nulls(
                (
                    COALESCE(s.metadata_json, '{}'::jsonb)
                        - 'account_id'
                        - 'active_account_id'
                        - 'deployment_id'
                        - 'login_reservation_id'
                        - 'login_reservation_status'
                        - 'login_reservation_account_id'
                        - 'login_slot_status'
                        - 'login_slot_account_id'
                        - 'reserved_account_id'
                        - 'sticky_account_id'
                        - 'current_control_plane_state'
                        - 'previous_control_plane_state'
                        - 'current_runner_state'
                        - 'previous_runner_state'
                        - 'current_state'
                        - 'previous_state'
                        - 'reason'
                        - 'last_error'
                ) || (
                    normalized.payload_json
                        - 'account_id'
                        - 'active_account_id'
                        - 'deployment_id'
                        - 'login_reservation_id'
                        - 'login_reservation_status'
                        - 'login_reservation_account_id'
                        - 'login_slot_status'
                        - 'login_slot_account_id'
                        - 'reserved_account_id'
                        - 'sticky_account_id'
                ) || jsonb_build_object(
                    'control_plane_state', 'ready',
                    'current_control_plane_state', 'ready',
                    'available_for_new_account', TRUE
                )
            )
        ELSE normalized.payload_json
    END,
    last_heartbeat_at = GREATEST(COALESCE(s.last_heartbeat_at, TO_TIMESTAMP(0)), normalized.created_at),
    updated_at = NOW()
FROM normalized
WHERE s.runner_id = normalized.runner_id
  AND s.slot_id = normalized.slot_id
  AND normalized.slot_status IS NOT NULL
  AND (
      normalized.slot_status <> 'ready'
      OR NOT EXISTS (
          SELECT 1
          FROM bot_deployments d
          WHERE d.runner_id = s.runner_id
            AND d.slot_id = s.slot_id
            AND d.status IN ('start_requested', 'starting', 'running', 'stop_requested')
            AND (COALESCE(d.is_active, FALSE) = TRUE OR d.desired_state = 'running')
      )
      AND NOT EXISTS (
          SELECT 1
          FROM account_login_reservations v
          WHERE v.runner_id = s.runner_id
            AND v.slot_id = s.slot_id
            AND v.status IN ('pending', 'dispatched', 'verified')
      )
      AND NOT EXISTS (
          SELECT 1
          FROM execution_commands c
          WHERE c.runner_id = s.runner_id
            AND c.slot_id = s.slot_id
            AND c.delivery_status IN ('pending', 'queued', 'dispatched')
      )
  )
	  AND (
	      s.status IS DISTINCT FROM normalized.slot_status
	      OR (
	          normalized.over_node_slot_cap
	          AND COALESCE(LOWER(NULLIF(BTRIM(s.metadata_json->>'disabled_by_node_slot_cap'), '')), 'false')
	                NOT IN ('true', '1', 'yes', 'y', 'on')
	      )
	      OR (
	          NOT normalized.over_node_slot_cap
	          AND s.metadata_json IS DISTINCT FROM normalized.payload_json
	      )
	  )
