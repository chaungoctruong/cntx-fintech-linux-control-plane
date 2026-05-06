INSERT INTO bot_versions(
    bot_code, version, checksum, source_path, metadata_json, created_at, updated_at
)
VALUES(%s, %s, %s, %s, %s::jsonb, NOW(), NOW())
ON CONFLICT(bot_code, version) DO UPDATE SET
    checksum = EXCLUDED.checksum,
    source_path = EXCLUDED.source_path,
    metadata_json = EXCLUDED.metadata_json,
    updated_at = NOW()
