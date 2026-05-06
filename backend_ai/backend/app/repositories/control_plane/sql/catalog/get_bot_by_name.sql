SELECT
    bot_code,
    bot_name,
    display_name,
    language,
    version,
    profile_class,
    runtime_entry,
    required_params,
    risk_profile,
    indicator_requirements,
    strategy_tags,
    resource_hints,
    supports_demo,
    supports_live,
    default_config_path,
    runtime_env,
    checksum,
    source_path,
    enabled,
    status
FROM bot_catalog
WHERE LOWER(bot_code) = LOWER(%s)
   OR LOWER(bot_name) = LOWER(%s)
   OR LOWER(display_name) = LOWER(%s)
ORDER BY enabled DESC, updated_at DESC
LIMIT 1
