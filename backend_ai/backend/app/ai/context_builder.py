from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from app.repositories.control_plane_repository import ControlPlaneRepository
from app.services.control_plane_service import MT5ControlPlaneService
from app.services.store_service import get_process_store
from app.settings import settings

log = logging.getLogger("ai_context_builder")


_ACTIVE_DEPLOYMENT_STATUSES = {"start_requested", "starting", "running", "stop_requested"}
_MISSING_USER_IDS = {"", "guest", "unknown", "none", "null", "0"}
_SENSITIVE_KEYS = ("password", "secret", "token", "api_key", "apikey", "credential")
_SECRET_TEXT_PATTERNS = (
    re.compile(r"(?i)\b(password|passwd|pwd|token|secret|api\s*key|private\s*key|authorization|bearer)\b\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)\b(password|passwd|pwd|token|secret|api\s*key|private\s*key|authorization|bearer)\b\s+(?:is|la|là)\s+[^\s,;]+"),
    re.compile(r"(?i)\b(api\s*key|private\s*key|bearer)\b\s+[^\s,;]+"),
    re.compile(r"(?i)\b(mật\s*khẩu|mat\s*khau|mk|otp|2fa)\b\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)\b(mật\s*khẩu|mat\s*khau|mk|otp|2fa)\b\s+(?:(?:của|cua)\s+(?:tôi|toi|em|anh|chị|chi|mình|minh)\s+)?(?:là|la)\s+[^\s,;]+"),
    re.compile(r"(?i)\b(mk|otp|2fa)\b\s+[^\s,;]+"),
    re.compile(r"(?i)\b(?:redis|postgres|postgresql|mysql|mongodb)://[^\s]+"),
    re.compile(r"(?i)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
)


@dataclass
class AIBackendContext:
    requested: bool = False
    source: str = "not_requested"
    telegram_id: str = ""
    user_id: Optional[int] = None
    missing_context: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    accounts: list[dict[str, Any]] = field(default_factory=list)
    deployments: list[dict[str, Any]] = field(default_factory=list)
    account_state: dict[str, Any] = field(default_factory=dict)
    deployment: dict[str, Any] = field(default_factory=dict)
    runner: dict[str, Any] = field(default_factory=dict)
    recent_commands: list[dict[str, Any]] = field(default_factory=list)
    recent_events: list[dict[str, Any]] = field(default_factory=list)
    recent_logs: list[dict[str, Any]] = field(default_factory=list)
    ops_summary: dict[str, Any] = field(default_factory=dict)

    @property
    def has_user_context(self) -> bool:
        return bool(self.telegram_id and self.user_id)

    @property
    def has_runtime_data(self) -> bool:
        return bool(self.accounts or self.deployments or self.account_state or self.deployment)

    def latest_command(self) -> dict[str, Any]:
        return self.recent_commands[0] if self.recent_commands else {}

    def latest_error(self) -> str:
        candidates: list[Any] = [
            self.deployment.get("last_error"),
            self.account_state.get("last_error"),
            self.latest_command().get("last_error"),
        ]
        for row in self.recent_logs:
            candidates.append(row.get("message") or _payload_error(row.get("payload_json")))
        for row in self.recent_events:
            candidates.append(_payload_error(row.get("payload_json")))
        for value in candidates:
            text = _clean_text(value)
            if text:
                return text[:240]
        return ""

    def to_prompt_block(self) -> str:
        if not self.requested:
            return "backend_context_requested=false"
        rows = [
            "backend_context_requested=true",
            f"source={self.source}",
            f"telegram_id={self.telegram_id or 'missing'}",
            f"user_id={self.user_id if self.user_id is not None else 'missing'}",
        ]
        if self.missing_context:
            rows.append(f"missing_context={', '.join(self.missing_context)}")
        if self.errors:
            rows.append(f"context_errors={'; '.join(self.errors[:3])}")
        if self.summary:
            rows.append(
                "summary="
                + _kv_line(
                    self.summary,
                    (
                        "linked_accounts",
                        "running_accounts",
                        "balance",
                        "equity",
                        "last_activity_ts",
                    ),
                )
            )
        if self.accounts:
            rows.append("accounts=" + json.dumps(self.accounts[:5], ensure_ascii=False, default=str))
        if self.deployments:
            rows.append("deployments=" + json.dumps(self.deployments[:5], ensure_ascii=False, default=str))
        if self.account_state:
            rows.append(
                "selected_account="
                + json.dumps(self.account_state, ensure_ascii=False, default=str)
            )
        if self.deployment:
            rows.append("selected_deployment=" + json.dumps(self.deployment, ensure_ascii=False, default=str))
        if self.runner:
            rows.append("runner=" + json.dumps(self.runner, ensure_ascii=False, default=str))
        if self.recent_commands:
            rows.append("recent_commands=" + json.dumps(self.recent_commands[:5], ensure_ascii=False, default=str))
        if self.recent_events:
            rows.append("recent_events=" + json.dumps(self.recent_events[:5], ensure_ascii=False, default=str))
        if self.recent_logs:
            rows.append("recent_logs=" + json.dumps(self.recent_logs[:5], ensure_ascii=False, default=str))
        if self.ops_summary:
            rows.append("ops_summary=" + json.dumps(self.ops_summary, ensure_ascii=False, default=str))
        return "\n".join(rows)


class AIContextBuilder:
    """Read-only backend context for AI answers.

    This stays on the Linux control-plane side. It only reads Postgres/Redis
    state through existing repository/service APIs and never dispatches runner
    commands.
    """

    async def build(
        self,
        *,
        user_id: str,
        context: Optional[dict[str, Any]],
        intent: str,
        query: str,
    ) -> AIBackendContext:
        return await asyncio.to_thread(
            self._build_sync,
            user_id=user_id,
            context=context or {},
            intent=intent,
            query=query,
        )

    def _build_sync(
        self,
        *,
        user_id: str,
        context: dict[str, Any],
        intent: str,
        query: str,
    ) -> AIBackendContext:
        result = AIBackendContext(requested=True, source="control_plane")
        telegram_id = _resolve_telegram_id(user_id=user_id, context=context)
        result.telegram_id = telegram_id
        if not telegram_id:
            result.missing_context.append("telegram_id")
            result.source = "missing_user_context"
            return result

        store = get_process_store()
        repo = ControlPlaneRepository(store)
        try:
            summary = repo.get_user_runtime_summary(telegram_id)
        except Exception as exc:
            result.errors.append(f"user_runtime_summary_failed:{type(exc).__name__}")
            log.warning("AI context user summary failed: %s", exc)
            return result

        result.summary = _public_summary(summary)
        uid = _safe_int(summary.get("user_id"))
        if uid <= 0:
            result.missing_context.append("registered_user")
            result.source = "user_not_found"
            return result
        result.user_id = uid

        try:
            accounts = repo.list_accounts_for_user(user_id=uid)
            result.accounts = [_public_account(row) for row in accounts[:8]]
        except Exception as exc:
            accounts = []
            result.errors.append(f"accounts_failed:{type(exc).__name__}")
            log.warning("AI context accounts failed: %s", exc)

        try:
            deployments = repo.list_deployments(user_id=uid)
            result.deployments = [_public_deployment(row) for row in deployments[:8]]
        except Exception as exc:
            deployments = []
            result.errors.append(f"deployments_failed:{type(exc).__name__}")
            log.warning("AI context deployments failed: %s", exc)

        account_id = _resolve_account_id(context=context, query=query, accounts=accounts, deployments=deployments)
        deployment_id = _resolve_deployment_id(context=context, query=query, deployments=deployments, account_id=account_id)

        if account_id > 0:
            try:
                account_state = repo.get_account_state(account_id=account_id, user_id=uid) or {}
                result.account_state = _public_account_state(account_state)
                if deployment_id <= 0:
                    deployment_id = _safe_int(account_state.get("deployment_id"))
            except Exception as exc:
                result.errors.append(f"account_state_failed:{type(exc).__name__}")
                log.warning("AI context account state failed: %s", exc)

        if deployment_id > 0:
            try:
                deployment = repo.get_deployment(deployment_id=deployment_id, user_id=uid) or {}
                result.deployment = _public_deployment_detail(deployment)
                if account_id <= 0:
                    account_id = _safe_int(deployment.get("account_id"))
            except Exception as exc:
                result.errors.append(f"deployment_failed:{type(exc).__name__}")
                log.warning("AI context deployment failed: %s", exc)

            try:
                result.recent_commands = [
                    _public_command(row)
                    for row in repo.list_execution_commands(deployment_id=deployment_id, user_id=uid, limit=5)
                ]
            except Exception as exc:
                result.errors.append(f"commands_failed:{type(exc).__name__}")
                log.warning("AI context commands failed: %s", exc)

            try:
                result.recent_events = [
                    _public_event(row)
                    for row in repo.list_execution_events(deployment_id=deployment_id, user_id=uid, limit=5)
                ]
            except Exception as exc:
                result.errors.append(f"events_failed:{type(exc).__name__}")
                log.warning("AI context events failed: %s", exc)

            try:
                result.recent_logs = [
                    _public_log(row)
                    for row in repo.list_runtime_logs(deployment_id=deployment_id, user_id=uid, limit=5)
                ]
            except Exception as exc:
                result.errors.append(f"logs_failed:{type(exc).__name__}")
                log.warning("AI context logs failed: %s", exc)

        runner_id = _clean_text(
            result.deployment.get("runner_id")
            or result.account_state.get("runner_id")
            or context.get("runner_id")
        )
        if runner_id:
            try:
                service = MT5ControlPlaneService(store=store, repo=repo)
                runner_health = service.get_runner_health(runner_id=runner_id) or {}
                result.runner = _public_runner((runner_health.get("runner") or runner_health) if isinstance(runner_health, dict) else {})
            except Exception as exc:
                result.errors.append(f"runner_health_failed:{type(exc).__name__}")
                log.warning("AI context runner health failed: %s", exc)

        if intent in {"technical_debug", "account_or_bot_status"}:
            try:
                result.ops_summary = _public_ops_summary(
                    repo.get_runtime_health_summary(
                        runner_stale_sec=max(30, int(getattr(settings, "CONTROL_PLANE_RUNNER_STALE_SEC", 180) or 180)),
                        deployment_stale_sec=max(30, int(getattr(settings, "CONTROL_PLANE_DEPLOYMENT_STALE_SEC", 180) or 180)),
                    )
                )
            except Exception as exc:
                result.errors.append(f"ops_summary_failed:{type(exc).__name__}")
                log.warning("AI context ops summary failed: %s", exc)

        return result


def _resolve_telegram_id(*, user_id: str, context: dict[str, Any]) -> str:
    candidates = (
        context.get("telegram_id"),
        context.get("user_id"),
        context.get("tg_user_id"),
        user_id,
    )
    for value in candidates:
        text = _clean_text(value)
        if text.lower() not in _MISSING_USER_IDS:
            return text
    return ""


def _resolve_account_id(*, context: dict[str, Any], query: str, accounts: list[dict[str, Any]], deployments: list[dict[str, Any]]) -> int:
    for key in ("account_id", "mt5_account_id"):
        value = _safe_int(context.get(key))
        if value > 0:
            return value
    match = re.search(r"\baccount(?:_id)?\s*[:#]?\s*(\d{1,12})\b", str(query or ""), flags=re.IGNORECASE)
    if match:
        return _safe_int(match.group(1))
    active_accounts = [
        _safe_int(row.get("id"))
        for row in accounts
        if _safe_int(row.get("id")) > 0 and _safe_int(row.get("active_deployment_id")) > 0
    ]
    if len(active_accounts) == 1:
        return active_accounts[0]
    connected_accounts = [
        _safe_int(row.get("id"))
        for row in accounts
        if _safe_int(row.get("id")) > 0 and _clean_text(row.get("status")).lower() == "connected"
    ]
    if len(connected_accounts) == 1:
        return connected_accounts[0]
    if len(accounts) == 1:
        return _safe_int(accounts[0].get("id"))
    active_deployments = [row for row in deployments if _clean_text(row.get("status")).lower() in _ACTIVE_DEPLOYMENT_STATUSES]
    if len(active_deployments) == 1:
        return _safe_int(active_deployments[0].get("account_id"))
    return 0


def _resolve_deployment_id(
    *,
    context: dict[str, Any],
    query: str,
    deployments: list[dict[str, Any]],
    account_id: int,
) -> int:
    value = _safe_int(context.get("deployment_id"))
    if value > 0:
        return value
    match = re.search(r"\bdeployment(?:_id)?\s*[:#]?\s*(\d{1,12})\b", str(query or ""), flags=re.IGNORECASE)
    if match:
        return _safe_int(match.group(1))
    account_deployments = [
        row
        for row in deployments
        if account_id > 0 and _safe_int(row.get("account_id")) == account_id
        and _clean_text(row.get("status")).lower() in _ACTIVE_DEPLOYMENT_STATUSES
    ]
    if len(account_deployments) == 1:
        return _safe_int(account_deployments[0].get("id"))
    active = [row for row in deployments if _clean_text(row.get("status")).lower() in _ACTIVE_DEPLOYMENT_STATUSES]
    if len(active) == 1:
        return _safe_int(active[0].get("id"))
    return 0


def _public_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "linked_accounts": _safe_int(row.get("linked_accounts")),
        "running_accounts": _safe_int(row.get("running_accounts")),
        "balance": _safe_float(row.get("balance")),
        "equity": _safe_float(row.get("equity")),
        "last_activity_ts": _safe_int(row.get("last_activity_ts")),
    }


def _public_account(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "account_id": _safe_int(row.get("id")),
        "broker": _clean_text(row.get("broker")),
        "server": _clean_text(row.get("server")),
        "login_hint": _mask_login(row.get("login")),
        "status": _clean_text(row.get("status")),
        "is_active": bool(row.get("is_active")),
        "last_error": _redact_sensitive_text(_clean_text(row.get("last_error")))[:200],
        "login_reservation_id": _safe_int(row.get("login_reservation_id")),
        "login_reservation_status": _clean_text(row.get("login_reservation_status")),
        "login_state": _clean_text(row.get("login_state")),
        "active_deployment_id": _safe_int(row.get("active_deployment_id")),
        "active_deployment_status": _clean_text(row.get("active_deployment_status")),
        "runner_id": _clean_text(row.get("runner_id")),
        "slot_id": _clean_text(row.get("slot_id")),
    }


def _public_deployment(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "deployment_id": _safe_int(row.get("id")),
        "account_id": _safe_int(row.get("account_id")),
        "bot_code": _clean_text(row.get("bot_code")),
        "bot_name": _clean_text(row.get("bot_name")),
        "status": _clean_text(row.get("status")),
        "desired_state": _clean_text(row.get("desired_state")),
        "health_status": _clean_text(row.get("health_status")),
        "runner_id": _clean_text(row.get("runner_id")),
        "slot_id": _clean_text(row.get("slot_id")),
        "last_error": _redact_sensitive_text(_clean_text(row.get("last_error")))[:200],
        "last_heartbeat_at": _clean_text(row.get("last_heartbeat_at")),
        "broker": _clean_text(row.get("broker")),
        "server": _clean_text(row.get("server")),
        "login_hint": _mask_login(row.get("login")),
    }


def _public_account_state(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "account_id": _safe_int(row.get("account_id")),
        "broker": _clean_text(row.get("broker")),
        "server": _clean_text(row.get("server")),
        "login_hint": _mask_login(row.get("login")),
        "connection_status": _clean_text(row.get("connection_status")),
        "last_error": _redact_sensitive_text(_clean_text(row.get("last_error")))[:200],
        "runner_id": _clean_text(row.get("runner_id")),
        "slot_id": _clean_text(row.get("slot_id")),
        "binding_state": _clean_text(row.get("binding_state")),
        "login_reservation_id": _safe_int(row.get("login_reservation_id")),
        "login_reservation_status": _clean_text(row.get("login_reservation_status")),
        "login_state": _clean_text(row.get("login_state")),
        "deployment_id": _safe_int(row.get("deployment_id")),
        "bot_code": _clean_text(row.get("bot_code")),
        "bot_name": _clean_text(row.get("bot_name")),
        "deployment_status": _clean_text(row.get("deployment_status")),
        "health_status": _clean_text(row.get("health_status")),
        "pnl": _safe_float(row.get("pnl")),
        "balance": _safe_float(row.get("balance")),
        "equity": _safe_float(row.get("equity")),
        "free_margin": _safe_float(row.get("free_margin")),
        "snapshot_heartbeat_at": _clean_text(row.get("snapshot_heartbeat_at")),
    }


def _public_deployment_detail(row: dict[str, Any]) -> dict[str, Any]:
    base = _public_deployment(row)
    base.update(
        {
            "slot_status": _clean_text(row.get("slot_status")),
            "runner_status": _clean_text(row.get("runner_status")),
            "connection_status": _clean_text(row.get("connection_status")),
            "pnl": _safe_float(row.get("pnl")),
            "balance": _safe_float(row.get("balance")),
            "equity": _safe_float(row.get("equity")),
            "free_margin": _safe_float(row.get("free_margin")),
            "snapshot_heartbeat_at": _clean_text(row.get("snapshot_heartbeat_at")),
        }
    )
    return base


def _public_command(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "command_id": _clean_text(row.get("command_id")),
        "command_type": _clean_text(row.get("command_type")),
        "delivery_status": _clean_text(row.get("delivery_status")),
        "runner_id": _clean_text(row.get("runner_id")),
        "slot_id": _clean_text(row.get("slot_id")),
        "last_error": _redact_sensitive_text(_clean_text(row.get("last_error")))[:200],
        "created_at": _clean_text(row.get("created_at")),
        "dispatched_at": _clean_text(row.get("dispatched_at")),
        "acknowledged_at": _clean_text(row.get("acknowledged_at")),
    }


def _public_event(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": _clean_text(row.get("event_id")),
        "event_type": _clean_text(row.get("event_type")),
        "command_id": _clean_text(row.get("command_id")),
        "severity": _clean_text(row.get("severity")),
        "runner_id": _clean_text(row.get("runner_id")),
        "slot_id": _clean_text(row.get("slot_id")),
        "error": _payload_error(row.get("payload_json")),
        "created_at": _clean_text(row.get("created_at")),
    }


def _public_log(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "level": _clean_text(row.get("level")),
        "message": _redact_sensitive_text(_clean_text(row.get("message")))[:240],
        "runner_id": _clean_text(row.get("runner_id")),
        "slot_id": _clean_text(row.get("slot_id")),
        "error": _payload_error(row.get("payload_json")),
        "created_at": _clean_text(row.get("created_at")),
    }


def _public_runner(row: dict[str, Any]) -> dict[str, Any]:
    queue_depth = row.get("queue_depth") if isinstance(row.get("queue_depth"), dict) else {}
    return {
        "runner_id": _clean_text(row.get("runner_id")),
        "status": _clean_text(row.get("status")),
        "operational_status": _clean_text(row.get("operational_status")),
        "is_stale": bool(row.get("is_stale")),
        "accepts_new_work": bool(row.get("accepts_new_work")),
        "total_slots": _safe_int(row.get("total_slots")),
        "healthy_slots": _safe_int(row.get("healthy_slots")),
        "available_slots": _safe_int(row.get("available_slots")),
        "allocated_slots": _safe_int(row.get("allocated_slots")),
        "degraded_slots": _safe_int(row.get("degraded_slots")),
        "broken_slots": _safe_int(row.get("broken_slots")),
        "last_heartbeat_at": _clean_text(row.get("last_heartbeat_at")),
        "queue_depth": {
            "commands": _safe_int(queue_depth.get("commands")),
            "commands_processing": _safe_int(queue_depth.get("commands_processing")),
        },
    }


def _public_ops_summary(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for bucket in ("runners", "deployments", "slots", "accounts", "events"):
        value = row.get(bucket)
        if isinstance(value, dict):
            out[bucket] = {
                key: _safe_int(val) if _looks_int_like(val) else _clean_text(val)
                for key, val in value.items()
                if key in {
                    "total_runners",
                    "online_runners",
                    "degraded_runners",
                    "offline_runners",
                    "stale_runners",
                    "total_deployments",
                    "running_deployments",
                    "desired_running_deployments",
                    "failed_deployments",
                    "transitional_deployments",
                    "stale_deployments",
                    "total_slots",
                    "ready_slots",
                    "allocated_slots",
                    "degraded_slots",
                    "broken_slots",
                    "total_accounts",
                    "connected_accounts",
                    "pending_accounts",
                    "recent_event_count",
                    "last_runtime_activity_ts",
                }
            }
    return out


def _payload_error(payload: Any) -> str:
    data = _json_dict(payload)
    if not data:
        return ""
    for key in (
        "last_error",
        "error",
        "error_text",
        "error_code",
        "reason",
        "message",
        "mt5_last_error",
        "retcode",
        "retcode_external",
    ):
        text = _clean_text(data.get(key))
        if text:
            return _redact_sensitive_text(text)[:240]
    return ""


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return _strip_sensitive(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return _strip_sensitive(parsed) if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _strip_sensitive(data: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in data.items():
        key_s = str(key or "")
        if any(token in key_s.lower() for token in _SENSITIVE_KEYS):
            continue
        clean[key_s] = _strip_sensitive_value(value)
    return clean


def _strip_sensitive_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _strip_sensitive(value)
    if isinstance(value, list):
        return [_strip_sensitive_value(item) for item in value[:20]]
    if isinstance(value, tuple):
        return tuple(_strip_sensitive_value(item) for item in value[:20])
    if isinstance(value, str):
        return _redact_sensitive_text(value)
    return value


def _redact_sensitive_text(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    for pattern in _SECRET_TEXT_PATTERNS:
        text = pattern.sub("[redacted_sensitive]", text)
    return text


def _kv_line(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    parts: list[str] = []
    for key in keys:
        value = row.get(key)
        if value not in (None, "", [], {}):
            parts.append(f"{key}={value}")
    return "; ".join(parts) if parts else "none"


def _mask_login(value: Any) -> str:
    raw = _clean_text(value)
    if not raw:
        return ""
    if len(raw) <= 4:
        return "*" * len(raw)
    return f"{'*' * max(0, len(raw) - 4)}{raw[-4:]}"


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, default=str)[:500]
    return re.sub(r"\s+", " ", str(value)).strip()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except Exception:
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def _looks_int_like(value: Any) -> bool:
    try:
        int(value)
        return True
    except Exception:
        return False
