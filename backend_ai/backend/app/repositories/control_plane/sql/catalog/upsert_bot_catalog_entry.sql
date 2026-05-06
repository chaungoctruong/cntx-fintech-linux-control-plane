INSERT INTO bot_catalog(
    bot_code, bot_name, strategy, tags, enabled, status, superseded_by,
    created_at, updated_at,
    display_name, language, version, profile_class, runtime_entry,
    required_params, risk_profile, indicator_requirements, strategy_tags,
    resource_hints, supports_demo, supports_live, default_config_path,
    runtime_env, checksum, source_path, metadata_json
)
VALUES(
    %s, %s, %s, %s::jsonb, TRUE, 'ACTIVE', NULL,
    %s, %s,
    %s, %s, %s, %s, %s,
    %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
    %s::jsonb, %s, %s, %s,
    %s::jsonb, %s, %s, %s::jsonb
)
ON CONFLICT(bot_code) DO UPDATE SET
    bot_name = EXCLUDED.bot_name,
    strategy = EXCLUDED.strategy,
    tags = EXCLUDED.tags,
    enabled = EXCLUDED.enabled,
    status = EXCLUDED.status,
    updated_at = EXCLUDED.updated_at,
    display_name = EXCLUDED.display_name,
    language = EXCLUDED.language,
    version = EXCLUDED.version,
    profile_class = EXCLUDED.profile_class,
    runtime_entry = COALESCE(NULLIF(EXCLUDED.runtime_entry, ''), bot_catalog.runtime_entry),
    required_params = EXCLUDED.required_params,
    risk_profile = EXCLUDED.risk_profile,
    indicator_requirements = EXCLUDED.indicator_requirements,
    strategy_tags = EXCLUDED.strategy_tags,
    resource_hints = EXCLUDED.resource_hints,
    supports_demo = EXCLUDED.supports_demo,
    supports_live = EXCLUDED.supports_live,
    default_config_path = COALESCE(EXCLUDED.default_config_path, bot_catalog.default_config_path),
    runtime_env = CASE
        WHEN NULLIF(EXCLUDED.runtime_entry, '') IS NULL
         AND NULLIF(bot_catalog.runtime_entry, '') IS NOT NULL
        THEN bot_catalog.runtime_env
        ELSE EXCLUDED.runtime_env
    END,
    checksum = EXCLUDED.checksum,
    source_path = EXCLUDED.source_path,
    metadata_json = EXCLUDED.metadata_json
RETURNING bot_code, bot_name, display_name, language, version, profile_class, runtime_entry
