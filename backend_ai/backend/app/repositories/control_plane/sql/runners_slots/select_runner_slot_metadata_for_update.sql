SELECT metadata_json
FROM runner_slots
WHERE runner_id = %s AND slot_id = %s
FOR UPDATE
