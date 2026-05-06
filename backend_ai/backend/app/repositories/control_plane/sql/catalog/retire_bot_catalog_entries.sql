UPDATE bot_catalog
SET enabled = FALSE,
    status = 'RETIRED',
    superseded_by = NULL,
    updated_at = %s
WHERE regexp_replace(lower(COALESCE(bot_code, '')), '[^a-z0-9]+', '', 'g') = ANY(%s)
   OR regexp_replace(lower(COALESCE(bot_name, '')), '[^a-z0-9]+', '', 'g') = ANY(%s)
   OR regexp_replace(lower(COALESCE(display_name, '')), '[^a-z0-9]+', '', 'g') = ANY(%s)
RETURNING bot_code
