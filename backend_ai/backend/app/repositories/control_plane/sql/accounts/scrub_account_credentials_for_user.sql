UPDATE account_credentials_encrypted
SET password_encrypted = '',
    metadata_json = jsonb_set(
        COALESCE(metadata_json, '{}'::jsonb),
        '{scrubbed}', 'true', true
    ),
    updated_at = NOW()
WHERE account_id IN (
    SELECT id FROM broker_accounts WHERE user_id = %s
)
