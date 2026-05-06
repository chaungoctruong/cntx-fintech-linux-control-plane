SELECT
    COUNT(*) AS deployment_count,
    COUNT(*) FILTER (WHERE status = 'running') AS running_deployment_count,
    COUNT(*) FILTER (WHERE status = 'failed') AS failed_deployment_count
FROM bot_deployments
WHERE user_id = %s
