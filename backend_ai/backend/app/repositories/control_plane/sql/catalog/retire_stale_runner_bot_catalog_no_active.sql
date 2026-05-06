UPDATE bot_catalog
SET enabled = FALSE,
    status = 'RETIRED',
    superseded_by = NULL,
    updated_at = %s
WHERE enabled = TRUE
  AND status IN ('ACTIVE', 'DEPRECATED')
  AND (
      COALESCE(metadata_json->>'catalog_origin', '') = 'runner'
      OR COALESCE(source_path, '') LIKE 'runner://%%'
  )
  AND (
      COALESCE(metadata_json->>'runner_id', '') = %s
      OR COALESCE(source_path, '') LIKE %s
  )
RETURNING bot_code
