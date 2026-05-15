from __future__ import annotations

from typing import Any, Optional


class OrchestrationPolicyError(ValueError):
    pass


def requires_dedicated_runner(bot: dict[str, Any]) -> bool:
    hints = dict(bot.get("resource_hints") or {})
    if bool(hints.get("requires_isolated_runner")):
        return True
    tags = {str(item).strip().lower() for item in (bot.get("strategy_tags") or []) if str(item).strip()}
    return bool(tags.intersection({"dca", "basket", "ensemble", "rl"}))


def validate_account_ready(account: Optional[dict[str, Any]]) -> None:
    if not account:
        raise OrchestrationPolicyError("account_not_found")
    status = str(account.get("status") or "").strip().lower()
    if account.get("has_credentials") is False:
        raise OrchestrationPolicyError("account_credentials_unavailable")
    login_state = str(account.get("login_state") or "").strip().upper()
    if status != "connected" and login_state != "READY":
        raise OrchestrationPolicyError("account_not_connected")


def validate_no_active_deployment(active_deployment: Optional[dict[str, Any]]) -> None:
    if active_deployment:
        raise OrchestrationPolicyError("account_has_active_deployment")


def validate_bot_available(bot: Optional[dict[str, Any]]) -> None:
    if not bot:
        raise OrchestrationPolicyError("bot_not_found")
    if not bool(bot.get("enabled", True)):
        raise OrchestrationPolicyError("bot_disabled")


def validate_start_request(
    *,
    account: Optional[dict[str, Any]],
    bot: Optional[dict[str, Any]],
    active_deployment: Optional[dict[str, Any]],
) -> None:
    validate_account_ready(account)
    validate_bot_available(bot)
    validate_no_active_deployment(active_deployment)


def validate_runtime_command_request(*, deployment: Optional[dict[str, Any]], allowed_statuses: set[str] | None = None) -> None:
    if not deployment:
        raise OrchestrationPolicyError("deployment_not_found")
    status = str(deployment.get("status") or "").strip().lower()
    allowed = {str(item).strip().lower() for item in (allowed_statuses or {"running", "start_requested", "starting"}) if str(item).strip()}
    if status not in allowed:
        raise OrchestrationPolicyError("deployment_not_running")
