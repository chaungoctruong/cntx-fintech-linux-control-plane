UPDATE bot_catalog
SET enabled = FALSE,
    status = 'RETIRED',
    updated_at = %s
WHERE COALESCE(source_path, '') NOT LIKE 'runner://%%'
  AND COALESCE(metadata_json->>'catalog_origin', '') <> 'runner'
