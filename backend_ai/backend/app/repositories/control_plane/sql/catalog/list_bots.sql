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
WHERE enabled = TRUE AND status IN ('ACTIVE', 'DEPRECATED')
ORDER BY display_name ASC, bot_name ASC, bot_code ASC
