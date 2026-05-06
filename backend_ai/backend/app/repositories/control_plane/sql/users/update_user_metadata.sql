UPDATE users
SET metadata_json = COALESCE(metadata_json, '{}'::jsonb) || %s::jsonb,
    updated_at = NOW()
WHERE id = %s
RETURNING metadata_json
