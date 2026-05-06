SELECT max_slots, metadata_json
FROM runner_nodes
WHERE runner_id = %s
FOR UPDATE
