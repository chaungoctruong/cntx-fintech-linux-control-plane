from __future__ import annotations

import copy
import hashlib
import json
import logging
from functools import lru_cache
import time
import uuid
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Optional
from urllib.parse import urlparse, urlunparse

from app.bot_catalog.mt5_repository_loader import (
    MT5BotCatalogLoader,
    MT5_RUNNER_CANARY_BOT_ID,
    disabled_mt5_bot_identities,
    is_disabled_mt5_bot_catalog_entry,
)
from app.events.runner_event_ingest import RunnerEventIngestService
from app.infra.redis_streams import RedisStreamPublisher
from app.models.control_plane import ACTIVE_DEPLOYMENT_STATUSES, CommandType
from app.monitoring.control_plane_metrics import ControlPlaneMetricsService
from app.monitoring.control_plane_reconciler import ControlPlaneReconcilerService
from app.orchestration.account_verification_manager import (
    AccountVerificationManagerService,
    _verification_job_stale_for_retry,
)
from app.orchestration.deployment_config import (
    TRADING_CONFIG_SCHEMA_VERSION,
    TRADING_CONFIG_KEY,
    bot_is_gsalgo_trading_config_bot,
    bot_requires_restart_on_config_update,
    bot_supports_trading_config,
    build_trading_config_audit_patch,
    is_dca_only_config_update,
    normalize_deployment_config,
)
from app.orchestration.deployment_manager import DeploymentManagerService, _inject_runner_queue_depths
from app.orchestration.runner_payload_identity import normalize_runner_payload_identity
from app.orchestration.scheduler import preview_slots_for_account
from app.repositories.control_plane_repository import ControlPlaneRepository
from app.runner.protocol import build_runner_command_from_row
from app.risk.account_risk_policy_service import AccountRiskPolicyService
from app.risk.quota_policy import (
    describe_quota,
    validate_can_connect_new_account,
    validate_can_start_new_deployment,
)
from app.risk.orchestration_policy import OrchestrationPolicyError
from app.security import CryptoBox
from app.services.runner_gsalgo_state import GsAlgoBackendStateService
from app.services.store_service import get_process_store
from app.settings import settings

log = logging.getLogger(__name__)

_VERIFICATION_CREDENTIAL_ERROR_CODES = {
    "INVALID_CREDENTIALS",
    "INVALID_PASSWORD",
    "INVALID_SERVER",
    "ACCOUNT_NOT_FOUND",
}

_SINGLE_TELEGRAM_BOT_LIMITS = {
    "free": {"max_active_deployments": 1},
    "pro": {"max_active_deployments": 1},
    "enterprise": {"max_active_deployments": 1},
}
_ADMIN_ACCOUNT_QUOTA_LIMIT = 10**9
_ADMIN_QUOTA_LIMITS = {
    "free": {"max_active_deployments": _ADMIN_ACCOUNT_QUOTA_LIMIT, "max_accounts": _ADMIN_ACCOUNT_QUOTA_LIMIT},
    "pro": {"max_active_deployments": _ADMIN_ACCOUNT_QUOTA_LIMIT, "max_accounts": _ADMIN_ACCOUNT_QUOTA_LIMIT},
    "enterprise": {"max_active_deployments": _ADMIN_ACCOUNT_QUOTA_LIMIT, "max_accounts": _ADMIN_ACCOUNT_QUOTA_LIMIT},
}
_DBG_MARKETS_FIXED_SERVER = "DBGMarkets-Live"
_GSALGO_DISPLAY_NAME = "Gs Algo"
_GSALGO_DISPLAY_IDENTITIES = {"gsalgo", "gsalgomt5bot"}


def _bot_control_cooldown_sec() -> int:
    try:
        return max(0, int(getattr(settings, "BOT_CONTROL_COOLDOWN_SEC", 60) or 0))
    except (TypeError, ValueError):
        return 60


def _norm_text(value: Any) -> str:
    return str(value or "").strip()


def _merge_deployment_config_update(current_config: Any, requested_config: Any) -> dict[str, Any]:
    merged = copy.deepcopy(current_config) if isinstance(current_config, dict) else {}
    requested = requested_config if isinstance(requested_config, dict) else {}
    for key, value in requested.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            nested = copy.deepcopy(merged[key])
            nested.update(copy.deepcopy(value))
            merged[key] = nested
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _norm_broker_identity(value: Any) -> str:
    return "".join(ch for ch in _norm_text(value).lower() if ch.isalnum())


def _norm_bot_display_identity(value: Any) -> str:
    return "".join(ch for ch in _norm_text(value).lower() if ch.isalnum())


def _is_gsalgo_display_identity(*values: Any) -> bool:
    return any(_norm_bot_display_identity(value) in _GSALGO_DISPLAY_IDENTITIES for value in values)


def _bot_display_name(*, bot_code: Any, bot_name: Any, display_name: Any) -> str:
    if _is_gsalgo_display_identity(bot_code, bot_name, display_name):
        return _GSALGO_DISPLAY_NAME
    return _norm_text(display_name or bot_name or bot_code)


def _normalize_mt5_server_for_broker(*, broker: Any, server: Any) -> str:
    if _norm_broker_identity(broker) in {"dbgmarkets", "dbg"}:
        return _DBG_MARKETS_FIXED_SERVER
    return _norm_text(server)


def _split_admin_telegram_ids(raw: Any) -> set[str]:
    normalized = _norm_text(raw).replace(";", ",").replace("\n", ",").replace(" ", ",")
    return {item.strip() for item in normalized.split(",") if item.strip()}


def _admin_telegram_ids() -> set[str]:
    admin_ids = _split_admin_telegram_ids(getattr(settings, "ADMIN_TELEGRAM_IDS", ""))
    admin_ids.update(_split_admin_telegram_ids(getattr(settings, "DEV_CHAT_ID", "")))
    return admin_ids


def _is_admin_telegram_id(telegram_id: Any) -> bool:
    value = _norm_text(telegram_id)
    return bool(value and value in _admin_telegram_ids())


def _norm_verification_error_code(value: Any) -> str:
    return _norm_text(value).upper().replace("-", "_").replace(" ", "_")


def _canonical_slot_id(value: Any) -> str:
    raw = _norm_text(value)
    if not raw:
        return ""
    lowered = raw.lower()
    if lowered.startswith("slot_") or lowered.startswith("slot-"):
        return f"slot-{raw[5:]}"
    return raw


def _verification_result_slot_matches(expected_slot_id: str, incoming_slot_id: str, current_job: dict[str, Any]) -> bool:
    if not expected_slot_id or not incoming_slot_id or expected_slot_id == incoming_slot_id:
        return True
    payload = current_job.get("payload_json") if isinstance(current_job, dict) else {}
    if not isinstance(payload, dict):
        payload = {}
    is_verification_lane = (
        _norm_text(payload.get("mode")) == "verify_account"
        or _norm_text(payload.get("verification_lane_contract")) == "session0_lane"
    )
    if is_verification_lane and "template" in {expected_slot_id.strip().lower(), incoming_slot_id.strip().lower()}:
        return True
    return False


def _verification_failure_is_auth_only_with_healthy_mt5(error_text: str, payload: dict[str, Any]) -> bool:
    payload_map = payload if isinstance(payload, dict) else {}
    error_code = _norm_verification_error_code(payload_map.get("error_code"))
    if error_code in _VERIFICATION_CREDENTIAL_ERROR_CODES:
        return True
    if error_code:
        return False
    normalized_error = _norm_text(error_text).lower()
    mt5_last_error = _norm_text(payload_map.get("mt5_last_error")).lower()
    phase = _norm_text(payload_map.get("phase")).lower()
    reason = _norm_text(payload_map.get("reason")).lower()
    terminal_log_line = _norm_text(payload_map.get("terminal_log_line")).lower()
    liveness_state = _norm_text(payload_map.get("mt5_liveness_state")).lower()
    terminal_info = payload_map.get("terminal_info") if isinstance(payload_map.get("terminal_info"), dict) else {}
    terminal_connected = str((terminal_info or {}).get("connected") or "").strip().lower()

    auth_text = " ".join([normalized_error, reason, phase, mt5_last_error, terminal_log_line])
    normalized_auth_text = auth_text.replace("_", " ").replace("-", " ")
    transient_tokens = (
        "transient_mt5",
        "template_verification_worker_timeout",
        "terminal_log_verification_timeout",
        "template_terminal_lock_timeout",
        "mt5_initialize_failed",
        "verification_subprocess_timeout",
        "verification_hard_timeout",
        "interactive_verification_timeout",
        "interactive_verification_worker_timeout",
        "terminal_initialize_failed",
        "verification_mt5_init_lock_timeout",
        "warm_attach_failed",
        "warm_attach_direct_credentials_failed",
        "broker_connection_timeout",
    )
    if any(token in auth_text for token in transient_tokens) or "ipc" in auth_text:
        return False

    auth_failure_tokens = (
        "authorization failed",
        "authorization_failed",
        "auth failed",
        "auth_failed",
        "login failed",
        "invalid account",
        "unknown account",
        "account_not_found",
        "account not found",
        "wrong password",
        "bad credentials",
    )
    has_auth_failure_token = any(
        token in auth_text or token in normalized_auth_text for token in auth_failure_tokens
    )
    terminal_log_auth_failure = any(
        token in terminal_log_line or token in terminal_log_line.replace("_", " ").replace("-", " ")
        for token in ("authorization failed", "authorization_failed", "invalid account", "login failed")
    )
    login_returned_false_with_auth_log = "login_returned_false" in auth_text and terminal_log_auth_failure
    mt5_login_failed_with_auth_error = phase == "mt5_login_failed" and (
        has_auth_failure_token
        or any(
            token in mt5_last_error or token in mt5_last_error.replace("_", " ").replace("-", " ")
            for token in ("auth", "login", "password", "invalid server", "server not found", "unknown server")
        )
    )
    verify_identity_mismatch = "verify_login_mismatch" in auth_text or "verify_server_mismatch" in auth_text
    explicit_auth_failure = (
        has_auth_failure_token
        or login_returned_false_with_auth_log
        or mt5_login_failed_with_auth_error
        or verify_identity_mismatch
    )
    if explicit_auth_failure:
        return True

    auth_failure = (
        "login_returned_false" in auth_text
        or phase == "mt5_login_failed"
        or mt5_last_error.startswith("-6")
        or "authorization failed" in mt5_last_error
    )
    mt5_healthy = liveness_state == "healthy" or terminal_connected == "true"
    return bool(auth_failure and mt5_healthy)


def _runner_queue_depths(runner_ids: list[str]) -> dict[str, dict[str, int]]:
    ids = [str(item or "").strip() for item in runner_ids if str(item or "").strip()]
    if not ids:
        return {}
    try:
        from redis import Redis

        from app.core.redis_client import get_resolved_redis_write_url

        client = Redis.from_url(
            get_resolved_redis_write_url(),
            decode_responses=True,
            socket_connect_timeout=0.25,
            socket_timeout=0.25,
        )
        pipe = client.pipeline()
        keys: list[tuple[str, str]] = []
        for runner_id in ids:
            for name, key in (
                ("verification", f"mt5:runner:{runner_id}:verification"),
                ("verification_processing", f"mt5:runner:{runner_id}:verification:processing"),
                ("commands", f"mt5:runner:{runner_id}:commands"),
                ("commands_processing", f"mt5:runner:{runner_id}:commands:processing"),
            ):
                keys.append((runner_id, name))
                pipe.llen(key)
        values = pipe.execute()
    except Exception:
        return {}

    out: dict[str, dict[str, int]] = {
        runner_id: {
            "verification": 0,
            "verification_processing": 0,
            "commands": 0,
            "commands_processing": 0,
        }
        for runner_id in ids
    }
    for (runner_id, name), value in zip(keys, values):
        try:
            out[runner_id][name] = int(value or 0)
        except Exception:
            out[runner_id][name] = 0
    return out


def _is_backend_ctrader_reserved_bot(bot: Optional[dict[str, Any]]) -> bool:
    if not bot:
        return False
    source_path = _norm_text(bot.get("source_path")).replace("\\", "/").lower()
    if not source_path:
        return False
    parts = [part for part in source_path.split("/") if part]
    return "bot-trading" in parts


def _as_string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _norm_text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _normalize_profile_class(value: Any) -> str:
    normalized = _norm_text(value).lower()
    return normalized if normalized in {"light", "normal", "heavy"} else "normal"


def _is_internal_canary_bot(bot: Optional[dict[str, Any]]) -> bool:
    if not bot:
        return False
    code = _norm_text(bot.get("bot_code") or bot.get("bot_id") or bot.get("bot_name")).lower()
    if code == MT5_RUNNER_CANARY_BOT_ID:
        return True
    source_path = _norm_text(bot.get("source_path")).lower()
    runtime_env = bot.get("runtime_env") if isinstance(bot.get("runtime_env"), dict) else {}
    return source_path.startswith("system://") and bool(runtime_env.get("canary"))


def _is_active_catalog_entry(bot: Optional[dict[str, Any]]) -> bool:
    if not bot:
        return False
    if bot.get("enabled") is False:
        return False
    status = _norm_text(bot.get("status")).upper()
    if status and status not in {"ACTIVE", "DEPRECATED"}:
        return False
    return True


def _is_user_visible_catalog_bot(bot: Optional[dict[str, Any]]) -> bool:
    return (
        bool(bot)
        and _is_active_catalog_entry(bot)
        and not _is_backend_ctrader_reserved_bot(bot)
        and not _is_internal_canary_bot(bot)
        and not is_disabled_mt5_bot_catalog_entry(bot)
    )


def _filter_enabled_runner_bot_strings(value: Any) -> list[str]:
    out: list[str] = []
    for item in _as_string_list(value):
        if is_disabled_mt5_bot_catalog_entry({"bot_id": item, "bot_code": item, "bot_name": item, "display_name": item}):
            continue
        out.append(item)
    return out


def _filter_runner_bot_catalog_payload(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    catalog = dict(value)
    raw_items = catalog.get("bots")
    if isinstance(raw_items, list):
        items = [
            dict(item)
            for item in raw_items
            if isinstance(item, dict) and not is_disabled_mt5_bot_catalog_entry(item)
        ]
        catalog["bots"] = items
        catalog["count"] = len(items)
        catalog["bot_codes"] = [
            str(item.get("bot_code") or item.get("bot_id") or "").strip()
            for item in items
            if str(item.get("bot_code") or item.get("bot_id") or "").strip()
        ]
    return catalog


def _runner_catalog_is_authoritative(bot_catalog: Any) -> bool:
    if not isinstance(bot_catalog, dict) or not bot_catalog:
        return False
    if bot_catalog.get("error") or bot_catalog.get("missing") or bot_catalog.get("disabled"):
        return False
    return isinstance(bot_catalog.get("bots"), list) or "count" in bot_catalog


def _stable_checksum(value: Any) -> str:
    try:
        rendered = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        rendered = repr(value)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _runner_catalog_checksum(
    *,
    source: str,
    items: list[Any],
    authoritative: bool,
) -> str:
    normalized_items = sorted(
        list(items or []),
        key=lambda item: _stable_checksum(item),
    )
    return _stable_checksum(
        {
            "source": source,
            "authoritative": bool(authoritative),
            "items": normalized_items,
            "disabled": sorted(disabled_mt5_bot_identities()),
        }
    )


def _runner_catalog_items(
    *,
    available_bots: Any,
    available_bot_names: Any,
    bot_catalog: Any,
) -> tuple[str, list[dict[str, Any]]]:
    catalog = bot_catalog if isinstance(bot_catalog, dict) else {}
    source = _norm_text(catalog.get("source")) or "runner"
    raw_items = catalog.get("bots")
    items = [item for item in raw_items if isinstance(item, dict)] if isinstance(raw_items, list) else []
    if items:
        return source, items

    codes = _as_string_list(available_bots)
    names = _as_string_list(available_bot_names)
    generated: list[dict[str, Any]] = []
    for idx, code in enumerate(codes):
        generated.append(
            {
                "bot_id": code,
                "bot_code": code,
                "bot_name": names[idx] if idx < len(names) and names[idx] else code,
            }
        )
    return source, generated


def _runner_bot_definition(*, runner_id: str, source: str, raw: dict[str, Any]) -> Optional[dict[str, Any]]:
    bot_code = _norm_text(raw.get("bot_code") or raw.get("bot_id") or raw.get("bot_name"))
    if not bot_code:
        return None
    bot_name = _norm_text(raw.get("bot_name") or raw.get("display_name") or bot_code)
    display_name = _bot_display_name(bot_code=bot_code, bot_name=bot_name, display_name=raw.get("display_name"))
    package_dir = _norm_text(raw.get("package_dir") or raw.get("package") or bot_code)
    language = _norm_text(raw.get("runtime_language") or raw.get("language") or "python")
    raw_runtime_env = raw.get("runtime_env") if isinstance(raw.get("runtime_env"), dict) else {}
    raw_resource_hints = raw.get("resource_hints") if isinstance(raw.get("resource_hints"), dict) else {}
    runtime_entry = _norm_text(
        raw.get("entrypoint")
        or raw.get("runtime_entry")
        or raw_runtime_env.get("entrypoint")
        or raw_runtime_env.get("runtime_entry")
        or raw_resource_hints.get("entrypoint")
        or raw_resource_hints.get("runtime_entry")
    )
    config_path = _norm_text(raw.get("config_path") or raw.get("default_config_path")) or None
    profile_class = _normalize_profile_class(raw.get("profile_class"))
    strategy_tags = _as_string_list(raw.get("strategy_tags"))
    version = _norm_text(raw.get("version") or "0.1.0") or "0.1.0"
    checksum_seed = "|".join([runner_id, bot_code, version, runtime_entry, config_path or "", package_dir])
    checksum = _norm_text(raw.get("checksum")) or hashlib.sha1(checksum_seed.encode("utf-8")).hexdigest()
    source_path = f"runner://{runner_id}/{package_dir or bot_code}"
    runtime_env = {
        "runtime": "windows_mt5",
        "lane": "mt5_runner",
        "broker_type": "mt5",
        "source": source,
        "runner_id": runner_id,
        "package_dir": package_dir,
    }
    if runtime_entry:
        runtime_env["entrypoint"] = runtime_entry
    for schema_key in ("config_schema", "deployment_config_schema", "trading"):
        schema_payload = raw.get(schema_key)
        if isinstance(schema_payload, dict):
            runtime_env[schema_key] = dict(schema_payload)
    resource_hints = {
        "profile_class": profile_class,
        "runtime": "windows_mt5",
        "lane": "mt5_runner",
        "runner_id": runner_id,
        "package_dir": package_dir,
    }
    return {
        "bot_id": bot_code,
        "bot_code": bot_code,
        "bot_name": bot_name,
        "display_name": display_name,
        "language": language,
        "version": version,
        "runtime_entry": runtime_entry,
        "profile_class": profile_class,
        "strategy_tags": strategy_tags,
        "required_params": list(raw.get("required_params") or []),
        "risk_profile": dict(raw.get("risk_profile") or {"class": "standard", "strategy_tags": strategy_tags}),
        "resource_hints": resource_hints,
        "indicator_requirements": list(raw.get("indicator_requirements") or []),
        "supports_demo": bool(raw.get("supports_demo", True)),
        "supports_live": bool(raw.get("supports_live", True)),
        "default_config_path": config_path,
        "runtime_env": runtime_env,
        "checksum": checksum,
        "source_path": source_path,
        "metadata": {
            "catalog_origin": "runner",
            "catalog_source": source,
            "runner_id": runner_id,
            "runner_availability": [{"runner_id": runner_id, "status": "available"}],
            "package_dir": package_dir,
        },
    }


def _mini_bot_item(bot: dict[str, Any]) -> dict[str, Any]:
    bot_code = _norm_text(bot.get("bot_code") or bot.get("bot_id") or bot.get("bot_name"))
    bot_name = _norm_text(bot.get("bot_name") or bot.get("display_name") or bot_code)
    display_name = _bot_display_name(bot_code=bot_code, bot_name=bot_name, display_name=bot.get("display_name"))
    profile_class = _normalize_profile_class(bot.get("profile_class"))
    required_params = list(bot.get("required_params") or [])
    trading_keys = {"lot_size", "stop_loss", "take_profit"}
    if bot_supports_trading_config(bot) and not trading_keys.issubset({_norm_text(item).lower() for item in required_params}):
        required_params = [*required_params, "lot_size", "stop_loss", "take_profit"]
    return {
        "bot_code": bot_code,
        "bot_id": _norm_text(bot.get("bot_id") or bot_code),
        "bot_name": bot_name,
        "display_name": display_name,
        "profile_class": profile_class,
        "language": _norm_text(bot.get("language") or "python"),
        "version": _norm_text(bot.get("version") or ""),
        "runtime_entry": _norm_text(bot.get("runtime_entry") or ""),
        "required_params": required_params,
        "risk_profile": dict(bot.get("risk_profile") or {}),
        "indicator_requirements": list(bot.get("indicator_requirements") or []),
        "strategy_tags": list(bot.get("strategy_tags") or []),
        "resource_hints": dict(bot.get("resource_hints") or {}),
        "supports_demo": bool(bot.get("supports_demo", True)),
        "supports_live": bool(bot.get("supports_live", True)),
        "default_config_path": bot.get("default_config_path"),
        "runtime_env": dict(bot.get("runtime_env") or {}),
        "checksum": _norm_text(bot.get("checksum") or ""),
        "source_path": _norm_text(bot.get("source_path") or ""),
        "label": f"{display_name} · {profile_class}",
    }


class MT5ControlPlaneService:
    def __init__(
        self,
        *,
        store: Any | None = None,
        repo: ControlPlaneRepository | None = None,
        loader: MT5BotCatalogLoader | None = None,
        deployment_manager: DeploymentManagerService | None = None,
        verification_manager: AccountVerificationManagerService | None = None,
        event_ingest: RunnerEventIngestService | None = None,
        metrics: ControlPlaneMetricsService | None = None,
        crypto: CryptoBox | None = None,
    ) -> None:
        self._store = store or get_process_store()
        self._repo = repo or ControlPlaneRepository(self._store)
        self._loader = loader or MT5BotCatalogLoader(repo=self._repo)
        self._deployment_manager = deployment_manager or DeploymentManagerService(self._repo, catalog_loader=self._loader)
        self._verification_manager = verification_manager or AccountVerificationManagerService(self._repo)
        self._event_ingest = event_ingest or RunnerEventIngestService(self._repo)
        self._metrics = metrics or ControlPlaneMetricsService(self._repo)
        self._reconciler = ControlPlaneReconcilerService(self._repo)
        self._risk_policy = AccountRiskPolicyService(self._repo, deployment_manager=self._deployment_manager)
        self._gsalgo_state = GsAlgoBackendStateService(self._repo)
        self._crypto = crypto or CryptoBox(settings.APP_SECRET_KEY, old_secrets=settings.secret_old_keys())
        self._runner_catalog_sync_cache: dict[str, dict[str, Any]] = {}
        self._runner_catalog_sync_lock = Lock()
        self._runner_catalog_sync_ttl_sec = max(60, int(getattr(settings, "RUNNER_CATALOG_SYNC_TTL_SEC", 600) or 600))
        self._dashboard_cache: dict[int, dict[str, Any]] = {}
        self._dashboard_cache_lock = Lock()
        self._dashboard_cache_ttl_sec = max(
            0.0,
            float(getattr(settings, "MINIAPP_DASHBOARD_CACHE_TTL_SEC", 5.0) or 5.0),
        )

    def _dashboard_cache_get(self, *, user_id: int) -> dict[str, Any] | None:
        if self._dashboard_cache_ttl_sec <= 0:
            return None
        now = time.monotonic()
        with self._dashboard_cache_lock:
            cached = self._dashboard_cache.get(int(user_id))
            if not cached:
                return None
            if now - float(cached.get("cached_at") or 0.0) > self._dashboard_cache_ttl_sec:
                self._dashboard_cache.pop(int(user_id), None)
                return None
            return copy.deepcopy(cached.get("payload") or {})

    def _dashboard_cache_set(self, *, user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        if self._dashboard_cache_ttl_sec <= 0:
            return payload
        with self._dashboard_cache_lock:
            self._dashboard_cache[int(user_id)] = {
                "cached_at": time.monotonic(),
                "payload": copy.deepcopy(payload),
            }
        return payload

    def _invalidate_dashboard_cache(self, *, user_id: int | None = None) -> None:
        with self._dashboard_cache_lock:
            if user_id is None:
                self._dashboard_cache.clear()
            else:
                self._dashboard_cache.pop(int(user_id), None)

    def _stored_runner_bot_catalog(self, *, runner_id: str) -> dict[str, Any]:
        if not hasattr(self._repo, "get_runner"):
            return {}
        try:
            runner = self._repo.get_runner(runner_id=runner_id) or {}
        except Exception:
            return {}
        for key in ("metadata_json", "capabilities_json"):
            container = runner.get(key)
            if not isinstance(container, dict):
                continue
            catalog = container.get("bot_catalog")
            if isinstance(catalog, dict) and isinstance(catalog.get("bots"), list) and catalog.get("bots"):
                return catalog
        return {}

    @staticmethod
    def _catalog_detail_index(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
        items = catalog.get("bots") if isinstance(catalog, dict) else None
        if not isinstance(items, list):
            return {}
        index: dict[str, dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            for key in ("bot_id", "bot_code", "bot_name", "display_name", "package_dir"):
                value = _norm_text(item.get(key)).lower()
                if value:
                    index[value] = item
        return index

    @staticmethod
    def _merge_catalog_details(items: list[dict[str, Any]], detail_index: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        if not detail_index:
            return items
        enriched: list[dict[str, Any]] = []
        for item in items:
            keys = [
                _norm_text(item.get(key)).lower()
                for key in ("bot_id", "bot_code", "bot_name", "display_name", "package_dir")
            ]
            detail = next((detail_index[key] for key in keys if key and key in detail_index), None)
            if not detail:
                enriched.append(item)
                continue
            merged = dict(detail)
            merged.update({key: value for key, value in item.items() if value not in (None, "", [], {})})
            enriched.append(merged)
        return enriched

    def ensure_user(self, *, telegram_id: str, username: Optional[str]) -> dict[str, Any]:
        return self._repo.ensure_user(telegram_id=telegram_id, username=username)

    def _raise_if_bot_control_cooldown_active(self, *, user_id: int, telegram_id: str) -> None:
        if _is_admin_telegram_id(telegram_id):
            return
        cooldown_sec = _bot_control_cooldown_sec()
        if cooldown_sec <= 0:
            return
        finder = getattr(self._repo, "get_recent_bot_control_command_for_user", None)
        if not callable(finder):
            return
        recent = finder(user_id=int(user_id), cooldown_sec=cooldown_sec)
        if recent:
            raise OrchestrationPolicyError("bot_control_cooldown_active")

    def connect_account(
        self,
        *,
        telegram_id: str,
        username: Optional[str],
        broker: str,
        server: str,
        login: str,
        password: str,
        label: Optional[str] = None,
    ) -> dict[str, Any]:
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        server = _normalize_mt5_server_for_broker(broker=broker, server=server)
        identity_conflict = self._find_mt5_account_identity_conflict(
            user_id=int(user["id"]),
            broker=broker,
            server=server,
            login=login,
        )
        if identity_conflict and int(identity_conflict.get("user_id") or 0) != int(user["id"]):
            raise OrchestrationPolicyError("mt5_account_already_used")
        # Quota check truoc khi connect (chong free user spam them broker account)
        same_user_existing = bool(identity_conflict and int(identity_conflict.get("user_id") or 0) == int(user["id"]))
        if same_user_existing and identity_conflict.get("active_deployment_id"):
            raise OrchestrationPolicyError("account_has_active_deployment")
        if not same_user_existing and not _is_admin_telegram_id(telegram_id):
            subscription = self._repo.get_user_active_subscription(user_id=int(user["id"]))
            existing_count = self._repo.count_user_accounts(user_id=int(user["id"]))
            validate_can_connect_new_account(
                subscription=subscription,
                existing_account_count=existing_count,
            )
        password_encrypted = self._crypto.encrypt_json({"password": password})
        account = self._repo.connect_account(
            user_id=int(user["id"]),
            broker=broker,
            server=server,
            login=login,
            password_encrypted=password_encrypted,
            label=label,
        )
        self._store.add_audit(
            telegram_id=telegram_id,
            action="account.connect",
            payload={"account_id": account.get("id"), "broker": broker, "server": server, "login": login},
            result=str(account.get("status") or "pending_verification"),
        )
        self._invalidate_dashboard_cache(user_id=int(user["id"]))
        return account

    def _find_mt5_account_identity_conflict(
        self,
        *,
        user_id: int,
        broker: Any,
        server: Any,
        login: Any,
        exclude_account_id: Optional[int] = None,
    ) -> Optional[dict[str, Any]]:
        finder = getattr(self._repo, "find_mt5_account_identity_conflict", None)
        if not callable(finder):
            return None
        return finder(
            user_id=int(user_id),
            broker=str(broker or ""),
            server=str(server or ""),
            login=str(login or ""),
            exclude_account_id=exclude_account_id,
        )

    def patch_account_label(
        self,
        *,
        telegram_id: str,
        username: Optional[str],
        account_id: int,
        label: Optional[str] = None,
        sort_order: Optional[int] = None,
    ) -> dict[str, Any]:
        """Update label/sort_order cho 1 account. KHONG cho update khi account khong thuoc user.

        - label: 0..120 ky tu (truncate). None -> giu nguyen.
        - sort_order: int. None -> giu nguyen.
        - Tra account row sau update.
        """
        if label is not None and not isinstance(label, str):
            raise ValueError("invalid_request")
        if sort_order is not None:
            try:
                sort_order = int(sort_order)
            except (TypeError, ValueError):
                raise ValueError("invalid_request")
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        account = self._repo.update_account_label(
            account_id=int(account_id),
            user_id=int(user["id"]),
            label=label,
            sort_order=sort_order,
        )
        if not account:
            raise ValueError("account_not_found")
        self._store.add_audit(
            telegram_id=telegram_id,
            action="account.label.update",
            payload={
                "account_id": int(account_id),
                "label": account.get("label"),
                "sort_order": account.get("sort_order"),
            },
            result="updated",
        )
        self._invalidate_dashboard_cache(user_id=int(user["id"]))
        return account

    def update_account_credentials(
        self,
        *,
        telegram_id: str,
        username: Optional[str],
        account_id: int,
        password: str,
    ) -> dict[str, Any]:
        """Re-key broker password mà KHÔNG xóa account.

        Yêu cầu password 8-256 ký tự. Nếu account có active deployment -> 409.
        START_BOT sẽ mở worker + MT5, đăng nhập bằng credential đã lưu và chỉ
        xác nhận account connected khi runner báo BOT_STARTED.
        """
        if not isinstance(password, str):
            raise ValueError("invalid_credentials_payload")
        plain = password.strip()
        if len(plain) < 8 or len(plain) > 256:
            raise ValueError("invalid_credentials_payload")
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        password_encrypted = self._crypto.encrypt_json({"password": plain})
        account = self._repo.update_account_credentials(
            account_id=int(account_id),
            user_id=int(user["id"]),
            password_encrypted=password_encrypted,
        )
        self._store.add_audit(
            telegram_id=telegram_id,
            action="account.credentials.update",
            payload={
                "account_id": int(account_id),
                "broker": account.get("broker"),
                "login": account.get("login"),
            },
            result="rotated",
        )
        self._invalidate_dashboard_cache(user_id=int(user["id"]))
        return account

    def verify_account(self, *, telegram_id: str, username: Optional[str], account_id: int, ok: bool = True, error_text: Optional[str] = None) -> dict[str, Any]:
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        account = self._repo.verify_account(account_id=account_id, user_id=int(user["id"]), ok=ok, error_text=error_text)
        if not account:
            raise ValueError("account_not_found")
        self._store.add_audit(
            telegram_id=telegram_id,
            action="account.verify",
            payload={"account_id": account_id, "ok": ok},
            result=str(account.get("status") or ("connected" if ok else "verification_failed")),
        )
        self._invalidate_dashboard_cache(user_id=int(user["id"]))
        return account

    async def request_account_verification(self, *, telegram_id: str, username: Optional[str], account_id: int) -> dict[str, Any]:
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        account = self._repo.get_account(account_id=account_id, user_id=int(user["id"]))
        if not account:
            raise ValueError("account_not_found")
        self._store.add_audit(
            telegram_id=telegram_id,
            action="account.verify.skipped",
            payload={
                "account_id": account_id,
                "verification_job_id": None,
                "verification_state": "VERIFIED",
                "verification_ui_state": "VERIFIED",
                "next_action": "START_BOT",
                "reason": "runtime_login_on_start",
            },
            result="skipped_runtime_login",
        )
        self._invalidate_dashboard_cache(user_id=int(user["id"]))
        account_response = dict(account or {})
        account_response.setdefault("raw_status", account_response.get("status"))
        account_response["status"] = "connected"
        account_response["has_credentials"] = True
        account_response["connect_status"] = "PENDING_RUNTIME_LOGIN"
        account_response["connection_state"] = "PENDING_RUNTIME_LOGIN"
        account_response["verification_state"] = "VERIFIED"
        account_response["verification_ui_state"] = "VERIFIED"
        account_response["runtime_login_required"] = True
        account_response["next_action"] = "START_BOT"
        return {
            "id": None,
            "account_id": int(account_id),
            "verification_job_id": None,
            "job_id": None,
            "status": "connected",
            "job_status": "verified",
            "verification_state": "VERIFIED",
            "verification_ui_state": "VERIFIED",
            "connect_status": "PENDING_RUNTIME_LOGIN",
            "connection_state": "PENDING_RUNTIME_LOGIN",
            "next_action": "START_BOT",
            "runner_id": None,
            "slot_id": None,
            "trace_id": None,
            "redis_stream_id": None,
            "job": None,
            "account": account_response,
            "runtime_login_required": True,
            "credential_check_policy": "login_before_start",
            "mt5_recovery_policy": "recover_or_launch",
        }

    def get_account(self, *, telegram_id: str, username: Optional[str], account_id: int) -> Optional[dict[str, Any]]:
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        return self._repo.get_account(account_id=account_id, user_id=int(user["id"]))

    def list_accounts(self, *, telegram_id: str, username: Optional[str]) -> list[dict[str, Any]]:
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        return self._repo.list_accounts_for_user(user_id=int(user["id"]))

    async def delete_account(
        self,
        *,
        telegram_id: str,
        username: Optional[str],
        account_id: int,
        reason: Optional[str] = None,
    ) -> dict[str, Any]:
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        user_id = int(user["id"])
        account = self._repo.get_account(account_id=int(account_id), user_id=user_id)
        if not account:
            raise ValueError("account_not_found")

        self._repo.reconcile_terminal_bot_control_commands(account_id=int(account_id))
        active = self._repo.get_active_deployment_for_account(account_id=int(account_id))
        if active:
            raise OrchestrationPolicyError("account_has_active_deployment")

        pending_command_finder = getattr(self._repo, "get_pending_account_start_stop_command", None)
        if callable(pending_command_finder):
            pending_command = pending_command_finder(account_id=int(account_id))
            if pending_command:
                raise OrchestrationPolicyError("start_transition_in_progress")

        clean_reason = (reason or "account_deleted_by_user").strip()[:200]
        verification_cancelled_total = 0
        try:
            cancel_result = await self._verification_manager.cancel_all_verifications_for_account(
                user_id=user_id,
                account_id=int(account_id),
                reason=clean_reason,
            )
            verification_cancelled_total = int(cancel_result.get("cancelled_count") or 0)
        except Exception:
            verification_cancelled_total = 0

        binding = self._repo.get_current_binding(account_id=int(account_id))
        deleted_account = self._repo.soft_delete_account(
            account_id=int(account_id),
            user_id=user_id,
            reason=clean_reason,
        )
        if not deleted_account:
            raise ValueError("account_not_found")

        slot_released = False
        runner_id = str((binding or {}).get("runner_id") or "").strip()
        slot_id = str((binding or {}).get("slot_id") or "").strip()
        if runner_id and slot_id:
            try:
                self._repo.release_account_slot_binding(
                    account_id=int(account_id),
                    runner_id=runner_id,
                    slot_id=slot_id,
                    keep_sticky=False,
                )
                slot_released = True
            except Exception:
                slot_released = False

        self._store.add_audit(
            telegram_id=telegram_id,
            action="account.delete",
            payload={
                "account_id": int(account_id),
                "broker": account.get("broker"),
                "server": account.get("server"),
                "login": account.get("login"),
                "verification_cancelled_total": verification_cancelled_total,
                "slot_released": slot_released,
                "reason": clean_reason,
            },
            result="soft_deleted",
        )
        self._invalidate_dashboard_cache(user_id=user_id)
        return {
            "account_id": int(account_id),
            "deleted": True,
            "status": deleted_account.get("status") or "disconnected",
            "verification_cancelled_total": verification_cancelled_total,
            "slot_released": slot_released,
        }

    def list_account_verifications(
        self,
        *,
        telegram_id: str,
        username: Optional[str],
        account_id: int,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        return self._repo.list_account_verification_jobs(
            account_id=account_id,
            user_id=int(user["id"]),
            limit=limit,
        )

    def get_account_verification(
        self,
        *,
        telegram_id: str,
        username: Optional[str],
        job_id: int,
    ) -> Optional[dict[str, Any]]:
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        job = self._repo.get_account_verification_job_for_user(
            job_id=int(job_id),
            user_id=int(user["id"]),
        )
        if not job or not _verification_job_stale_for_retry(job):
            return job

        canceller = getattr(self._repo, "cancel_account_verification_job", None)
        if not callable(canceller):
            return job
        outcome = canceller(
            job_id=int(job["id"]),
            user_id=int(user["id"]),
            reason="verification_callback_timeout",
        )
        if str((outcome or {}).get("status") or "").strip().lower() != "cancelled":
            return job
        recovered = dict((outcome or {}).get("job") or job)
        recovered["stale_recovered"] = True
        recovered["retryable"] = True
        recovered["verification_state"] = "FAILED"
        recovered["verification_ui_state"] = "FAILED"
        return recovered

    async def cancel_all_account_verifications(
        self,
        *,
        telegram_id: str,
        username: Optional[str],
        account_id: int,
        reason: Optional[str] = None,
    ) -> dict[str, Any]:
        """Bulk cancel moi verification job dang pending/dispatched cho 1 account.

        Tra ve aggregated result + ghi audit log.
        """
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        # Validate ownership cua account TRUOC khi bulk cancel.
        account = self._repo.get_account(account_id=int(account_id), user_id=int(user["id"]))
        if not account:
            raise ValueError("account_not_found")
        result = await self._verification_manager.cancel_all_verifications_for_account(
            user_id=int(user["id"]),
            account_id=int(account_id),
            reason=reason,
        )
        self._store.add_audit(
            telegram_id=telegram_id,
            action="account.verify.cancel_all",
            payload={
                "account_id": int(account_id),
                "scanned_count": result.get("scanned_count"),
                "cancelled_count": result.get("cancelled_count"),
                "signal_emitted_count": result.get("signal_emitted_count"),
                "skipped_count": len(result.get("skipped") or []),
                "reason": reason or "cancelled_by_user",
            },
            result="cancelled" if (result.get("cancelled_count") or 0) > 0 else "noop",
        )
        self._invalidate_dashboard_cache(user_id=int(user["id"]))
        return result

    async def cancel_account_verification(
        self,
        *,
        telegram_id: str,
        username: Optional[str],
        job_id: int,
        reason: Optional[str] = None,
    ) -> dict[str, Any]:
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        result = await self._verification_manager.cancel_verification(
            user_id=int(user["id"]),
            job_id=int(job_id),
            reason=reason,
        )
        self._store.add_audit(
            telegram_id=telegram_id,
            action="account.verify.cancel",
            payload={
                "verification_job_id": result.get("id"),
                "account_id": result.get("account_id"),
                "previous_status": result.get("cancel_outcome"),
                "reason": reason or "cancelled_by_user",
                "cancel_signal_emitted": bool(result.get("cancel_signal_emitted")),
            },
            result=str(result.get("status") or "cancelled"),
        )
        self._invalidate_dashboard_cache(user_id=int(user["id"]))
        return result

    def get_account_state(self, *, telegram_id: str, username: Optional[str], account_id: int) -> Optional[dict[str, Any]]:
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        return self._repo.get_account_state(account_id=account_id, user_id=int(user["id"]))

    def list_account_positions(
        self,
        *,
        telegram_id: str,
        username: Optional[str],
        account_id: int,
        deployment_id: Optional[int] = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        return self._repo.list_position_snapshots(
            account_id=account_id,
            user_id=int(user["id"]),
            deployment_id=deployment_id,
            limit=limit,
        )

    def list_bots(self, *, force_sync: bool = False) -> list[dict[str, Any]]:
        bots = self._loader.sync_catalog(force=force_sync)
        return [bot for bot in bots if _is_user_visible_catalog_bot(bot)]

    def get_bot(self, *, bot_name: str, force_sync: bool = False) -> Optional[dict[str, Any]]:
        bot = self._loader.get_bot(bot_name, force_sync=force_sync)
        if not _is_user_visible_catalog_bot(bot):
            return None
        return bot

    def list_mini_bots(self, *, force_sync: bool = False) -> list[dict[str, Any]]:
        return [_mini_bot_item(bot) for bot in self.list_bots(force_sync=force_sync)]

    def _assert_user_can_access_bot(self, *, bot_name: str) -> dict[str, Any]:
        bot = self._loader.get_bot(bot_name, force_sync=False)
        if not bot:
            raise ValueError("bot_not_found")
        if _is_backend_ctrader_reserved_bot(bot):
            raise ValueError("bot_reserved_for_backend_ctrader")
        if not _is_user_visible_catalog_bot(bot):
            raise ValueError("bot_not_found")
        return bot

    def scheduler_preview(
        self,
        *,
        telegram_id: str,
        username: Optional[str],
        account_id: int,
        bot_name: str,
    ) -> dict[str, Any]:
        """Read-only scheduler preview. Khong tao deployment/command/job."""
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        account = self._repo.get_account(account_id=int(account_id), user_id=int(user["id"]))
        if not account:
            raise ValueError("account_not_found")
        bot = self._loader.get_bot(bot_name, force_sync=False)
        if not bot:
            raise ValueError("bot_not_found")
        if _is_backend_ctrader_reserved_bot(bot):
            raise ValueError("bot_reserved_for_backend_ctrader")
        if not _is_user_visible_catalog_bot(bot):
            raise ValueError("bot_not_found")

        active = self._repo.get_active_deployment_for_account(account_id=int(account_id))
        if not active:
            self._repo.prepare_sticky_slot_for_reuse(account_id=int(account_id))
        slots = _inject_runner_queue_depths(self._repo.list_slots())
        sticky = self._repo.get_current_binding(account_id=int(account_id))
        preview = preview_slots_for_account(
            account_id=int(account_id),
            bot=bot,
            slots=slots,
            sticky_binding=sticky,
        )
        selected = dict(preview.get("selected") or {})
        ok = bool(preview.get("ok"))
        reason = str(selected.get("reason") or "")
        blocked_reasons = list(preview.get("blocked_reasons") or [])
        if active:
            ok = False
            reason = "account_has_active_deployment"
            if reason not in blocked_reasons:
                blocked_reasons.insert(0, reason)
        return {
            "account_id": int(account_id),
            "bot": {
                "bot_code": bot.get("bot_code"),
                "bot_name": bot.get("bot_name"),
                "profile_class": bot.get("profile_class"),
            },
            "ok": ok,
            "reason": reason,
            "would_select_runner": selected.get("runner_id") if ok else None,
            "would_select_slot": selected.get("slot_id") if ok else None,
            "sticky_reused": bool(selected.get("sticky_reused")) if ok else False,
            "active_deployment": bool(active),
            "candidates": list(preview.get("candidates") or []),
            "blocked_reasons": blocked_reasons,
            "blocked_slots": list(preview.get("blocked_slots") or []),
        }

    def select_bot(self, *, telegram_id: str, username: Optional[str], account_id: int, bot_name: str, bot_config_overrides: dict[str, Any]) -> dict[str, Any]:
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        bot = self._assert_user_can_access_bot(bot_name=bot_name)
        effective_config = normalize_deployment_config(bot=bot, config=bot_config_overrides)
        draft = self._deployment_manager.select_bot(
            user_id=int(user["id"]),
            account_id=account_id,
            bot_name=bot_name,
            bot_config_overrides=effective_config,
        )
        self._store.add_audit(
            telegram_id=telegram_id,
            action="bot.select",
            payload={"account_id": account_id, "bot_name": bot_name},
            result="draft_created",
        )
        audit_patch = build_trading_config_audit_patch(
            bot=bot,
            original_config=bot_config_overrides,
            effective_config=effective_config,
        )
        if audit_patch:
            self._store.add_audit(
                telegram_id=telegram_id,
                action="deployment.config.update",
                payload={
                    "account_id": account_id,
                    "bot_name": bot_name,
                    "deployment_id": draft.get("id"),
                    **audit_patch,
                },
                result="draft_config_saved",
            )
        return draft

    async def start_deployment(
        self,
        *,
        telegram_id: str,
        username: Optional[str],
        account_id: int,
        bot_name: str,
        bot_config_overrides: dict[str, Any],
        mode: str = "live",
    ) -> dict[str, Any]:
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        bot = self._assert_user_can_access_bot(bot_name=bot_name)
        effective_config = normalize_deployment_config(bot=bot, config=bot_config_overrides)
        account = self._repo.get_account(account_id=account_id, user_id=int(user["id"]))
        if not account:
            raise OrchestrationPolicyError("account_not_found")
        is_admin = _is_admin_telegram_id(telegram_id)
        active_for_account = self._repo.get_active_deployment_for_account(account_id=int(account_id))
        active_status = str((active_for_account or {}).get("status") or "").strip().lower()
        if (
            active_for_account
            and active_status == "running"
            and bot_is_gsalgo_trading_config_bot(bot)
            and is_dca_only_config_update(bot_config_overrides)
        ):
            config_result = await self.update_deployment_config(
                telegram_id=telegram_id,
                username=username,
                deployment_id=int(active_for_account["id"]),
                bot_config_overrides=bot_config_overrides,
            )
            deployment = config_result.get("deployment") or active_for_account
            command = (config_result.get("hot_update") or {}).get("command")
            return {
                "deployment": deployment,
                "command": command,
                "bot": bot,
                "scheduler": {
                    "runner_id": deployment.get("runner_id"),
                    "slot_id": deployment.get("slot_id"),
                    "reason": "dca_hot_update",
                    "sticky_reused": True,
                },
                "hot_update": config_result.get("hot_update"),
                "hot_update_required": True,
                "restart_required": False,
            }

        # Go-live guard: 1 Telegram user may occupy only 1 active/transition bot
        # at a time across all MT5 accounts. This is separate from plan quota and
        # includes paper mode because it still uses control-plane/runner state.
        # Admins may run multiple accounts for ops/testing, but one account still
        # cannot own more than one active deployment.
        active_for_user = self._repo.count_user_active_deployments(
            user_id=int(user["id"]),
            include_paper=True,
        )
        if active_for_user > 0 and not active_for_account and not is_admin:
            raise OrchestrationPolicyError("telegram_user_has_active_bot")

        # Quota check truoc khi consume runner slot.
        # Paper mode KHONG count vao live quota (de user thu nghiem thoai mai).
        normalized_mode = "paper" if str(mode or "").strip().lower() == "paper" else "live"
        if normalized_mode == "live" and not is_admin and not active_for_account:
            subscription = self._repo.get_user_active_subscription(user_id=int(user["id"]))
            active_count = self._repo.count_user_active_deployments(user_id=int(user["id"]))
            validate_can_start_new_deployment(
                subscription=subscription,
                active_deployment_count=active_count,
            )
        self._raise_if_bot_control_cooldown_active(user_id=int(user["id"]), telegram_id=telegram_id)
        result = await self._deployment_manager.start_deployment(
            user_id=int(user["id"]),
            account=account,
            bot_name=bot_name,
            bot_config_overrides=effective_config,
            mode=normalized_mode,
        )
        self._store.add_audit(
            telegram_id=telegram_id,
            action="deployment.start",
            payload={"account_id": account_id, "bot_name": bot_name, "deployment_id": result["deployment"]["id"]},
            result="replacement_stop_queued" if result.get("queued_start") else "start_requested",
        )
        audit_patch = build_trading_config_audit_patch(
            bot=bot,
            original_config=bot_config_overrides,
            effective_config=effective_config,
        )
        if audit_patch:
            self._store.add_audit(
                telegram_id=telegram_id,
                action="deployment.config.update",
                payload={
                    "account_id": account_id,
                    "bot_name": bot_name,
                    "deployment_id": result["deployment"]["id"],
                    **audit_patch,
                },
                result="start_config_saved",
            )
        self._invalidate_dashboard_cache(user_id=int(user["id"]))
        return result

    async def update_deployment_config(
        self,
        *,
        telegram_id: str,
        username: Optional[str],
        deployment_id: int,
        bot_config_overrides: dict[str, Any],
    ) -> dict[str, Any]:
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        deployment = self._repo.get_deployment(deployment_id=deployment_id, user_id=int(user["id"]))
        if not deployment:
            raise ValueError("deployment_not_found")
        status = str(deployment.get("status") or "").strip().lower()
        bot_name = str(deployment.get("bot_code") or deployment.get("bot_name") or "").strip()
        bot = self.get_bot(bot_name=bot_name, force_sync=False) or {
            "bot_code": deployment.get("bot_code"),
            "bot_name": deployment.get("bot_name"),
            "profile_class": deployment.get("profile_class"),
        }
        active_deployment = bool(deployment.get("is_active")) or status in ACTIVE_DEPLOYMENT_STATUSES
        dca_hot_update_requested = (
            active_deployment
            and status == "running"
            and bot_is_gsalgo_trading_config_bot(bot)
            and is_dca_only_config_update(bot_config_overrides)
        )
        restart_required = bot_requires_restart_on_config_update(bot) and not dca_hot_update_requested
        if active_deployment and not (restart_required or dca_hot_update_requested):
            raise ValueError("deployment_config_locked_while_active")

        merged_config = _merge_deployment_config_update(deployment.get("config_json"), bot_config_overrides)
        effective_config = normalize_deployment_config(bot=bot, config=merged_config)
        config_update_trace_id = uuid.uuid4().hex
        if active_deployment and (restart_required or dca_hot_update_requested):
            pending = self._repo.get_pending_account_start_stop_command(account_id=int(deployment["account_id"]))
            existing_restart = (
                self._repo.get_open_config_restart_command(deployment_id=int(deployment["id"]))
                if restart_required
                else None
            )
            if pending and not existing_restart:
                raise OrchestrationPolicyError("start_transition_in_progress")

        updated = self._repo.update_deployment_config(
            deployment_id=deployment_id,
            user_id=int(user["id"]),
            bot_config=effective_config,
            allow_active=active_deployment and (restart_required or dca_hot_update_requested),
        )
        if not updated:
            raise ValueError("deployment_not_found")

        audit_patch = build_trading_config_audit_patch(
            bot=bot,
            original_config=bot_config_overrides,
            effective_config=effective_config,
        )
        self._store.add_audit(
            telegram_id=telegram_id,
            action="deployment.config.update",
            payload={
                "account_id": updated.get("account_id"),
                "bot_name": updated.get("bot_name"),
                "deployment_id": updated.get("id"),
                "trace_id": config_update_trace_id,
                "changed_fields": audit_patch.get("changed_fields") or sorted((bot_config_overrides or {}).keys()),
                **({"trading": audit_patch["trading"], "schema_version": audit_patch["schema_version"]} if audit_patch else {}),
            },
            result="config_saved",
        )
        restart_result: dict[str, Any] | None = None
        hot_update_result: dict[str, Any] | None = None
        if active_deployment and dca_hot_update_requested:
            trading_config = effective_config.get(TRADING_CONFIG_KEY) if isinstance(effective_config, dict) else {}
            if not isinstance(trading_config, dict):
                trading_config = {}
            dca_enabled = bool(trading_config.get("dca_enabled"))
            try:
                hot_update_result = await self._deployment_manager.request_config_hot_update(
                    deployment=updated,
                    config={TRADING_CONFIG_KEY: {"dca_enabled": dca_enabled}},
                    trace_id=config_update_trace_id,
                )
            except Exception as exc:
                self._store.add_audit(
                    telegram_id=telegram_id,
                    action="deployment.config.hot_update_failed",
                    payload={
                        "account_id": updated.get("account_id"),
                        "bot_name": updated.get("bot_name"),
                        "deployment_id": updated.get("id"),
                        "trace_id": config_update_trace_id,
                        "reason": str(exc)[:200],
                    },
                    result="enqueue_failed",
                )
                raise
            command = hot_update_result.get("command") or {}
            self._store.add_audit(
                telegram_id=telegram_id,
                action="deployment.config.hot_update_requested",
                payload={
                    "account_id": updated.get("account_id"),
                    "bot_name": updated.get("bot_name"),
                    "deployment_id": updated.get("id"),
                    "command_id": command.get("command_id"),
                    "trace_id": command.get("trace_id") or config_update_trace_id,
                    "changed_fields": ["dca_enabled"],
                },
                result="update_queued",
            )
        elif active_deployment and restart_required:
            try:
                restart_result = await self._deployment_manager.request_config_restart(
                    deployment=updated,
                    trace_id=config_update_trace_id,
                )
            except Exception as exc:
                self._store.add_audit(
                    telegram_id=telegram_id,
                    action="deployment.config.restart_failed",
                    payload={
                        "account_id": updated.get("account_id"),
                        "bot_name": updated.get("bot_name"),
                        "deployment_id": updated.get("id"),
                        "trace_id": config_update_trace_id,
                        "reason": str(exc)[:200],
                    },
                    result="enqueue_failed",
                )
                raise
            command = restart_result.get("command") or {}
            self._store.add_audit(
                telegram_id=telegram_id,
                action="deployment.config.restart_requested",
                payload={
                    "account_id": updated.get("account_id"),
                    "bot_name": updated.get("bot_name"),
                    "deployment_id": updated.get("id"),
                    "command_id": command.get("command_id"),
                    "trace_id": command.get("trace_id") or config_update_trace_id,
                    "coalesced": bool(restart_result.get("coalesced")),
                },
                result="coalesced" if restart_result.get("coalesced") else "stop_queued",
            )
            updated = restart_result.get("deployment") or updated
        self._invalidate_dashboard_cache(user_id=int(user["id"]))
        return {
            "deployment": updated,
            "config": effective_config,
            "restart": restart_result,
            "hot_update": hot_update_result,
            "hot_update_required": bool(active_deployment and dca_hot_update_requested),
            "restart_required": bool(active_deployment and restart_required),
        }

    async def stop_deployment(self, *, telegram_id: str, username: Optional[str], deployment_id: int, reason: Optional[str]) -> dict[str, Any]:
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        deployment = self._repo.get_deployment(deployment_id=deployment_id, user_id=int(user["id"]))
        if not deployment:
            raise OrchestrationPolicyError("deployment_not_found")
        result = await self._deployment_manager.stop_deployment(deployment=deployment or {}, reason=reason)
        self._store.add_audit(
            telegram_id=telegram_id,
            action="deployment.stop",
            payload={"deployment_id": deployment_id},
            result="stop_requested",
        )
        self._invalidate_dashboard_cache(user_id=int(user["id"]))
        return result

    async def cancel_deployment(
        self,
        *,
        telegram_id: str,
        username: Optional[str],
        deployment_id: int,
        reason: Optional[str] = None,
    ) -> dict[str, Any]:
        """Huy deployment kep o trang thai start_requested/starting.

        Service-layer wrapper: load deployment + check ownership + invoke manager + audit.
        """
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        deployment = self._repo.get_deployment(deployment_id=deployment_id, user_id=int(user["id"]))
        if not deployment:
            raise ValueError("deployment_not_found")
        result = await self._deployment_manager.cancel_pending_deployment(
            deployment=deployment,
            reason=reason,
        )
        deployment_after = result.get("deployment") or {}
        self._store.add_audit(
            telegram_id=telegram_id,
            action="deployment.cancel",
            payload={
                "deployment_id": deployment_id,
                "previous_status": result.get("cancelled_from_status"),
                "command_dispatched": bool(result.get("command_dispatched")),
                "reason": reason or "cancelled_by_user",
            },
            result=str(deployment_after.get("status") or "stopped"),
        )
        self._invalidate_dashboard_cache(user_id=int(user["id"]))
        return result

    async def send_deployment_command(
        self,
        *,
        telegram_id: str,
        username: Optional[str],
        deployment_id: int,
        command_type: CommandType,
        payload: dict[str, Any],
        priority: int,
        trace_id: Optional[str],
        command_id: Optional[str],
    ) -> dict[str, Any]:
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        deployment = self._repo.get_deployment(deployment_id=deployment_id, user_id=int(user["id"]))
        result = await self._deployment_manager.dispatch_runtime_command(
            deployment=deployment or {},
            command_type=command_type,
            payload=payload,
            priority=priority,
            trace_id=trace_id,
            command_id=command_id,
        )
        self._store.add_audit(
            telegram_id=telegram_id,
            action="deployment.command",
            payload={
                "deployment_id": deployment_id,
                "command_type": command_type.value,
                "command_id": (result.get("command") or {}).get("command_id"),
            },
            result="command_queued",
        )
        return result

    def list_deployments(self, *, telegram_id: str, username: Optional[str]) -> list[dict[str, Any]]:
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        return self._repo.list_deployments(user_id=int(user["id"]))

    def get_deployment(self, *, telegram_id: str, username: Optional[str], deployment_id: int) -> Optional[dict[str, Any]]:
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        return self._repo.get_deployment(deployment_id=deployment_id, user_id=int(user["id"]))

    def list_deployment_commands(
        self,
        *,
        telegram_id: str,
        username: Optional[str],
        deployment_id: int,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        return self._repo.list_execution_commands(
            deployment_id=deployment_id,
            user_id=int(user["id"]),
            limit=limit,
        )

    def list_deployment_events(
        self,
        *,
        telegram_id: str,
        username: Optional[str],
        deployment_id: int,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        return self._repo.list_execution_events(
            deployment_id=deployment_id,
            user_id=int(user["id"]),
            limit=limit,
        )

    def list_deployment_audit(
        self,
        *,
        telegram_id: str,
        username: Optional[str],
        deployment_id: int,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        return self._repo.list_execution_audit(
            deployment_id=deployment_id,
            user_id=int(user["id"]),
            limit=limit,
        )

    def list_deployment_logs(
        self,
        *,
        telegram_id: str,
        username: Optional[str],
        deployment_id: int,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        return self._repo.list_runtime_logs(
            deployment_id=deployment_id,
            user_id=int(user["id"]),
            limit=limit,
        )

    def _sync_runner_bot_catalog(
        self,
        *,
        runner_id: str,
        available_bots: Any = None,
        available_bot_names: Any = None,
        bot_catalog: Any = None,
        force: bool = False,
    ) -> dict[str, Any]:
        runner_id_s = _norm_text(runner_id)
        if not runner_id_s or not hasattr(self._repo, "upsert_bot_catalog_entry"):
            return {"count": 0, "bot_codes": []}

        source, items = _runner_catalog_items(
            available_bots=available_bots,
            available_bot_names=available_bot_names,
            bot_catalog=bot_catalog,
        )
        authoritative = _runner_catalog_is_authoritative(bot_catalog)
        if not authoritative:
            stored_catalog = self._stored_runner_bot_catalog(runner_id=runner_id_s)
            items = self._merge_catalog_details(items, self._catalog_detail_index(stored_catalog))
        checksum = _runner_catalog_checksum(
            source=source,
            items=items,
            authoritative=authoritative,
        )
        now = time.monotonic()
        with self._runner_catalog_sync_lock:
            cached = self._runner_catalog_sync_cache.get(runner_id_s)
            if (
                not force
                and cached
                and cached.get("checksum") == checksum
                and now - float(cached.get("synced_at") or 0.0) < self._runner_catalog_sync_ttl_sec
            ):
                log.debug(
                    "catalog_sync_skipped_unchanged runner_id=%s checksum=%s",
                    runner_id_s,
                    checksum[:12],
                )
                return {
                    "count": int(cached.get("count") or 0),
                    "bot_codes": list(cached.get("bot_codes") or []),
                    "checksum": checksum,
                    "skipped": True,
                    "reason": "unchanged",
                    "metric": "catalog_sync_skipped_unchanged",
                }
        definitions = [
            definition
            for item in items
            if (definition := _runner_bot_definition(runner_id=runner_id_s, source=source, raw=item)) is not None
            and not is_disabled_mt5_bot_catalog_entry(definition)
        ]
        try:
            if hasattr(self._repo, "retire_bot_catalog_entries"):
                self._repo.retire_bot_catalog_entries(bot_identities=disabled_mt5_bot_identities())
            if authoritative and hasattr(self._repo, "retire_stale_runner_bot_catalog_entries"):
                self._repo.retire_stale_runner_bot_catalog_entries(
                    runner_id=runner_id_s,
                    active_bot_ids=[str(item.get("bot_id") or item.get("bot_code") or item.get("bot_name") or "") for item in definitions],
                )
            for definition in definitions:
                self._repo.upsert_bot_catalog_entry(definition)
                if hasattr(self._repo, "upsert_bot_version"):
                    self._repo.upsert_bot_version(
                        bot_id=str(definition.get("bot_id") or ""),
                        version=str(definition.get("version") or "0.1.0"),
                        checksum=str(definition.get("checksum") or ""),
                        source_path=str(definition.get("source_path") or ""),
                        metadata=definition,
                    )
        except Exception:
            log.warning("catalog_sync_failed runner_id=%s checksum=%s", runner_id_s, checksum[:12])
            raise
        bot_codes = [str(item.get("bot_id") or "") for item in definitions if str(item.get("bot_id") or "")]
        with self._runner_catalog_sync_lock:
            self._runner_catalog_sync_cache[runner_id_s] = {
                "checksum": checksum,
                "synced_at": now,
                "count": len(definitions),
                "bot_codes": bot_codes,
            }
        log.debug(
            "catalog_sync_applied runner_id=%s count=%s checksum=%s",
            runner_id_s,
            len(definitions),
            checksum[:12],
        )
        return {
            "count": len(definitions),
            "bot_codes": bot_codes,
            "checksum": checksum,
            "skipped": False,
            "metric": "catalog_sync_applied",
        }

    def register_runner(self, **payload: Any) -> dict[str, Any]:
        repo_payload = dict(payload)
        available_bots = repo_payload.pop("available_bots", [])
        available_bot_names = repo_payload.pop("available_bot_names", [])
        bot_catalog = repo_payload.pop("bot_catalog", {})
        sync_result = self._sync_runner_bot_catalog(
            runner_id=str(repo_payload.get("runner_id") or ""),
            available_bots=available_bots,
            available_bot_names=available_bot_names,
            bot_catalog=bot_catalog,
            force=True,
        )
        capabilities = dict(repo_payload.get("capabilities") or {})
        filtered_available_bots = _filter_enabled_runner_bot_strings(available_bots)
        filtered_available_bot_names = _filter_enabled_runner_bot_strings(available_bot_names)
        if filtered_available_bots:
            capabilities["available_bots"] = filtered_available_bots
        elif "available_bots" in capabilities:
            capabilities.pop("available_bots", None)
        if filtered_available_bot_names:
            capabilities["available_bot_names"] = filtered_available_bot_names
        elif "available_bot_names" in capabilities:
            capabilities.pop("available_bot_names", None)
        if isinstance(bot_catalog, dict) and bot_catalog:
            capabilities["bot_catalog"] = {
                "source": _norm_text(bot_catalog.get("source")) or "runner",
                "count": int(sync_result.get("count") or 0),
                "bot_codes": list(sync_result.get("bot_codes") or []),
            }
        repo_payload["capabilities"] = capabilities
        result = self._repo.register_runner(**repo_payload)
        if sync_result.get("count"):
            result["bot_catalog"] = sync_result
        return result

    def get_execution_command(self, *, command_id: str) -> Optional[dict[str, Any]]:
        return self._repo.get_execution_command(command_id=command_id)

    def record_account_verification_result(
        self,
        *,
        job_id: int,
        ok: bool,
        error_text: Optional[str],
        runner_id: Optional[str],
        slot_id: Optional[str],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        current_job = self._repo.get_account_verification_job_by_id(job_id=job_id)
        current_status = _norm_text((current_job or {}).get("status")).lower()
        if current_job and current_status not in {"verified", "failed", "cancelled"}:
            expected_runner_id = _norm_text(current_job.get("runner_id"))
            incoming_runner_id = _norm_text(runner_id)
            if expected_runner_id and incoming_runner_id and expected_runner_id != incoming_runner_id:
                raise ValueError("verification_result_runner_mismatch")

            expected_slot_id = _canonical_slot_id(current_job.get("slot_id"))
            incoming_slot_id = _canonical_slot_id(slot_id)
            if not _verification_result_slot_matches(expected_slot_id, incoming_slot_id, current_job):
                raise ValueError("verification_result_slot_mismatch")

            expected_trace_id = _norm_text(current_job.get("trace_id"))
            incoming_trace_id = _norm_text((payload or {}).get("trace_id"))
            if expected_trace_id and incoming_trace_id and expected_trace_id != incoming_trace_id:
                raise ValueError("verification_result_trace_mismatch")

        result = self._verification_manager.complete_verification(
            job_id=job_id,
            ok=ok,
            error_text=error_text,
            runner_id=runner_id,
            slot_id=slot_id,
            payload=payload,
        )
        normalized_error = _norm_text(error_text or (payload or {}).get("reason")).lower()
        normalized_slot_id = _canonical_slot_id(slot_id) or _canonical_slot_id((current_job or {}).get("slot_id"))
        normalized_runner_id = _norm_text(runner_id) or _norm_text((current_job or {}).get("runner_id"))
        suppress_slot_health_mark = _verification_failure_is_auth_only_with_healthy_mt5(
            normalized_error,
            dict(payload or {}),
        )
        if (
            not ok
            and not suppress_slot_health_mark
            and normalized_runner_id
            and normalized_slot_id
            and hasattr(self._repo, "mark_slot_health")
        ):
            if "slot_unhealthy:broken" in normalized_error or normalized_error.endswith(":broken"):
                self._repo.mark_slot_health(runner_id=normalized_runner_id, slot_id=normalized_slot_id, status="broken")
            elif "slot_unhealthy:degraded" in normalized_error or normalized_error.endswith(":degraded"):
                self._repo.mark_slot_health(runner_id=normalized_runner_id, slot_id=normalized_slot_id, status="degraded")
        self._store.add_audit(
            telegram_id="internal_runner",
            action="runner.account_verification.result",
            payload={
                "verification_job_id": job_id,
                "account_id": (result.get("account") or {}).get("id"),
                "ok": ok,
                "runner_id": runner_id or result.get("runner_id"),
                "slot_id": slot_id or result.get("slot_id"),
                "trace_id": (payload or {}).get("trace_id") or result.get("trace_id") or (current_job or {}).get("trace_id"),
                "job_status_before": current_job.get("status") if current_job else None,
                "job_status_after": result.get("status"),
                "verification_state": result.get("verification_state"),
                "error_text": error_text,
                "error_code": (payload or {}).get("error_code") or result.get("error_code"),
                "retryable": (payload or {}).get("retryable") if "retryable" in (payload or {}) else result.get("retryable"),
                "failure_kind": (payload or {}).get("failure_kind") or result.get("failure_kind"),
                "failure_category": (payload or {}).get("failure_category") or result.get("failure_category"),
                "user_message_key": (payload or {}).get("user_message_key") or result.get("user_message_key"),
                "callback_payload": dict(payload or {}),
            },
            result=str(result.get("status") or ("verified" if ok else "failed")),
        )
        result_user_id = result.get("user_id") or (current_job or {}).get("user_id")
        if result_user_id is not None:
            self._invalidate_dashboard_cache(user_id=int(result_user_id))
        return result

    def update_execution_command_delivery(
        self,
        *,
        command_id: str,
        delivery_status: str,
        error_text: Optional[str],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        result = self._repo.update_execution_command_delivery(
            command_id=command_id,
            status=delivery_status,
            error_text=error_text,
            payload=payload,
        )
        if not result:
            raise ValueError("command_not_found")
        return result

    def runner_bootstrap(
        self,
        *,
        runner_id: Optional[str] = None,
        request_base_url: Optional[str] = None,
    ) -> dict[str, Any]:
        lease_sec = max(30, int(getattr(settings, "COMMAND_DELIVERY_PROCESSING_REQUEUE_TIMEOUT_SEC", 180) or 180))
        heartbeat_sec = max(3.0, float(getattr(settings, "RUNNER_HEARTBEAT_WRITE_THROTTLE_SEC", 5.0) or 5.0))
        configured_base_url = (
            str(getattr(settings, "RUNNER_CONTROL_PLANE_URL", "") or "").strip()
            or str(getattr(settings, "BACKEND_URL", "") or "").strip()
            or settings.resolved_backend_url()
        ).rstrip("/")
        request_base_url_s = str(request_base_url or "").strip().rstrip("/")
        if request_base_url_s and ("0.0.0.0" in configured_base_url or configured_base_url.endswith("://:")):
            base_url = request_base_url_s
            try:
                configured_parsed = urlparse(configured_base_url)
                request_parsed = urlparse(request_base_url_s)
                if configured_parsed.port and request_parsed.hostname and not request_parsed.port:
                    netloc = f"{request_parsed.hostname}:{configured_parsed.port}"
                    base_url = urlunparse((request_parsed.scheme, netloc, "", "", "", "")).rstrip("/")
            except Exception:
                pass
        else:
            base_url = configured_base_url
        runner_id_s = str(runner_id or "").strip()
        command_types = [
            CommandType.STOP_BOT.value,
            CommandType.START_BOT.value,
            CommandType.UPDATE_BOT_CONFIG.value,
        ]
        return {
            "server_time": datetime.now(timezone.utc).isoformat(),
            "runner_id": runner_id_s or None,
            "control_plane": {
                "base_url": base_url,
                "api_base": f"{base_url}/api/v2",
                "auth_header": "X-Backend-Api-Key",
            },
            "transport": {
                "recommended": "http_poll",
                "supported": ["http_poll", "redis_queue"],
                "http_poll": {
                    "claim_path": "/api/v2/runner/commands/claim",
                    "wait_timeout_sec": 10,
                    "idle_poll_sec": 1,
                    "claim_lease_sec": lease_sec,
                    "command_types": command_types,
                },
                "redis_queue": {
                    "commands": f"mt5:runner:{runner_id_s or '<runner_id>'}:commands",
                    "commands_processing": f"mt5:runner:{runner_id_s or '<runner_id>'}:commands:processing",
                    "verification": f"mt5:runner:{runner_id_s or '<runner_id>'}:verification",
                    "verification_processing": f"mt5:runner:{runner_id_s or '<runner_id>'}:verification:processing",
                },
            },
            "endpoints": {
                "register": "/api/v2/runner/register",
                "heartbeat": "/api/v2/runner/heartbeat",
                "events": "/api/v2/runner/events",
                "claim_command": "/api/v2/runner/commands/claim",
                "command_delivery": "/api/v2/runner/commands/{command_id}/delivery",
                "deployment_package": "/api/v2/runner/deployments/{deployment_id}/package",
                "account_bundle": "/api/v2/runner/accounts/{account_id}/bundle",
                "verification_result": "/api/v2/runner/account-verifications/result",
            },
            "timing": {
                "heartbeat_interval_sec": heartbeat_sec,
                "runner_stale_sec": max(30, int(getattr(settings, "CONTROL_PLANE_RUNNER_STALE_SEC", 180) or 180)),
                "claim_lease_sec": lease_sec,
            },
            "contract": {
                "start_bot": {
                    "runtime_login_required": True,
                    "credential_check_policy": "login_before_start",
                    "mt5_recovery_policy": "recover_or_launch",
                },
                "stop_bot": {
                    "stop_policy": "end_task",
                    "end_task": True,
                    "kill_worker": True,
                    "kill_mt5": True,
                    "terminate_mt5": True,
                    "release_terminal": True,
                },
                "credential_error_codes": [
                    "INVALID_CREDENTIALS",
                    "INVALID_PASSWORD",
                    "INVALID_SERVER",
                    "ACCOUNT_NOT_FOUND",
                ],
            },
        }

    async def claim_runner_command(
        self,
        *,
        runner_id: str,
        slot_id: Optional[str] = None,
        command_types: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        lease_sec = max(30, int(getattr(settings, "COMMAND_DELIVERY_PROCESSING_REQUEUE_TIMEOUT_SEC", 180) or 180))
        requeued_expired_claims = self._repo.requeue_stale_http_claimed_execution_commands(
            runner_id=runner_id,
            older_than_sec=lease_sec,
            command_types=command_types,
            limit=50,
        )
        row = self._repo.claim_next_execution_command_for_runner(
            runner_id=runner_id,
            slot_id=slot_id,
            command_types=command_types,
        )
        if not row:
            return {
                "empty": True,
                "command": None,
                "runner_id": str(runner_id or "").strip(),
                "slot_id": str(slot_id or "").strip() or None,
                "delivery_transport": "http_poll",
                "next_poll_sec": 1,
                "claim_lease_sec": lease_sec,
                "requeued_expired_claims": requeued_expired_claims,
            }

        envelope = build_runner_command_from_row(row)
        redis_cleanup: dict[str, Any] = {"removed": 0}
        try:
            redis_cleanup = await RedisStreamPublisher().remove_runner_command(
                runner_id=envelope.runner_id,
                command_id=envelope.command_id,
            )
        except Exception as exc:
            redis_cleanup = {"removed": 0, "error": exc.__class__.__name__}
            log.warning(
                "runner_http_claim_redis_cleanup_failed command_id=%s runner_id=%s error=%s",
                envelope.command_id,
                envelope.runner_id,
                exc.__class__.__name__,
            )

        return {
            "empty": False,
            "runner_id": envelope.runner_id,
            "slot_id": envelope.slot_id,
            "command_id": envelope.command_id,
            "delivery_status": "dispatched",
            "delivery_transport": "http_poll",
            "claim_lease_sec": lease_sec,
            "lease_expires_at_epoch": int(time.time()) + lease_sec,
            "requeued_expired_claims": requeued_expired_claims,
            "next_poll_sec": 0,
            "redis_cleanup": redis_cleanup,
            "command": envelope.model_dump(mode="json"),
        }

    async def ingest_runner_heartbeat(self, **payload: Any) -> dict[str, Any]:
        heartbeat_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        self._sync_runner_bot_catalog(
            runner_id=str(payload.get("runner_id") or ""),
            available_bots=heartbeat_payload.get("available_bots"),
            available_bot_names=heartbeat_payload.get("available_bot_names"),
            bot_catalog=heartbeat_payload.get("bot_catalog"),
        )
        if isinstance(heartbeat_payload, dict):
            filtered_payload = dict(heartbeat_payload)
            if "available_bots" in filtered_payload:
                filtered_payload["available_bots"] = _filter_enabled_runner_bot_strings(filtered_payload.get("available_bots"))
            if "available_bot_names" in filtered_payload:
                filtered_payload["available_bot_names"] = _filter_enabled_runner_bot_strings(filtered_payload.get("available_bot_names"))
            if "bot_catalog" in filtered_payload:
                filtered_payload["bot_catalog"] = _filter_runner_bot_catalog_payload(filtered_payload.get("bot_catalog"))
            payload = {**payload, "payload": filtered_payload}
        return await self._event_ingest.ingest_heartbeat(**payload)

    async def ingest_runner_event(self, **payload: Any) -> dict[str, Any]:
        return await self._event_ingest.ingest_event(**payload)

    def handle_gsalgo_bot_state(
        self,
        *,
        operation: str,
        context: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self._gsalgo_state.handle(operation=operation, context=context, payload=payload)

    def list_runners(self) -> list[dict[str, Any]]:
        return self._repo.list_runners_health(stale_sec=self._runner_stale_sec())

    def get_runner(self, *, runner_id: str) -> Optional[dict[str, Any]]:
        return self._repo.get_runner_health(runner_id=runner_id, stale_sec=self._runner_stale_sec())

    def enter_runner_maintenance(
        self,
        *,
        runner_id: str,
        reason: Optional[str],
        actor: Optional[str],
        disable_ready_slots: bool,
    ) -> dict[str, Any]:
        result = self._repo.set_runner_maintenance(
            runner_id=runner_id,
            draining=True,
            reason=reason,
            actor=actor,
            disable_ready_slots=disable_ready_slots,
        )
        if not result:
            raise ValueError("runner_not_found")
        health = self.get_runner_health(runner_id=runner_id)
        if not health:
            raise ValueError("runner_not_found")
        return {
            "runner_id": runner_id,
            "action": "drain",
            "maintenance": result,
            **health,
        }

    def exit_runner_maintenance(
        self,
        *,
        runner_id: str,
        reason: Optional[str],
        actor: Optional[str],
        enable_disabled_slots: bool,
    ) -> dict[str, Any]:
        result = self._repo.set_runner_maintenance(
            runner_id=runner_id,
            draining=False,
            reason=reason,
            actor=actor,
            enable_disabled_slots=enable_disabled_slots,
        )
        if not result:
            raise ValueError("runner_not_found")
        health = self.get_runner_health(runner_id=runner_id)
        if not health:
            raise ValueError("runner_not_found")
        return {
            "runner_id": runner_id,
            "action": "resume",
            "maintenance": result,
            **health,
        }

    def prepare_orphaned_slot_handoff(
        self,
        *,
        runner_id: str,
        slot_id: str,
        reason: Optional[str],
        actor: Optional[str],
        confirmed_runtime_dead: bool,
    ) -> dict[str, Any]:
        if not confirmed_runtime_dead:
            raise ValueError("runtime_death_confirmation_required")
        result = self._repo.prepare_orphaned_slot_handoff(
            runner_id=runner_id,
            slot_id=slot_id,
            reason=reason,
            actor=actor,
        )
        if not result:
            raise ValueError("slot_not_found")
        health = self.get_runner_health(runner_id=runner_id)
        if not health:
            raise ValueError("runner_not_found")
        return {
            "runner_id": runner_id,
            "slot_id": slot_id,
            "action": "prepare_orphaned_handoff",
            "handoff": result,
            **health,
        }

    def get_runner_account_bundle(self, *, account_id: int) -> dict[str, Any]:
        bundle = self._repo.get_runner_account_bundle(account_id=account_id)
        if not bundle:
            raise ValueError("account_not_found")
        encrypted = str(bundle.get("password_encrypted") or "").strip()
        if not encrypted:
            raise ValueError("account_credentials_unavailable")
        secret_payload = self._crypto.decrypt_json(encrypted)
        password = str((secret_payload or {}).get("password") or "").strip()
        if not password:
            raise ValueError("account_credentials_unavailable")
        self._store.add_audit(
            telegram_id="internal_runner",
            action="runner.account_bundle.fetch",
            payload={
                "account_id": int(bundle["account_id"]),
                "verification_job_id": bundle.get("verification_job_id"),
                "verification_status": bundle.get("verification_job_status"),
                "verification_state": bundle.get("verification_state"),
                "verification_runner_id": bundle.get("verification_runner_id"),
                "verification_slot_id": bundle.get("verification_slot_id"),
                "verification_trace_id": bundle.get("verification_trace_id"),
            },
            result=str(bundle.get("account_status") or "unknown"),
        )
        return {
            "account_id": int(bundle["account_id"]),
            "user_id": int(bundle["user_id"]),
            "broker": bundle.get("broker"),
            "server": bundle.get("server"),
            "login": bundle.get("login"),
            "password": password,
            "account_status": bundle.get("account_status"),
            "label": bundle.get("label"),
            "last_error": bundle.get("last_error"),
            "sticky_binding": {
                "runner_id": bundle.get("sticky_runner_id"),
                "slot_id": bundle.get("sticky_slot_id"),
                "binding_state": bundle.get("binding_state"),
            },
            "deployment": {
                "deployment_id": bundle.get("deployment_id"),
                "bot_code": bundle.get("bot_code"),
                "bot_name": bundle.get("bot_name"),
                "profile_class": bundle.get("profile_class"),
                "deployment_status": bundle.get("deployment_status"),
                "desired_state": bundle.get("desired_state"),
                "runner_id": bundle.get("deployment_runner_id"),
                "slot_id": bundle.get("deployment_slot_id"),
                "config_contract_version": TRADING_CONFIG_SCHEMA_VERSION,
                "config": bundle.get("config_json") or {},
                "trace_id": bundle.get("trace_id"),
                "health_status": bundle.get("health_status"),
                "last_heartbeat_at": bundle.get("last_heartbeat_at"),
            },
        }

    def get_runner_deployment_package(self, *, deployment_id: int) -> dict[str, Any]:
        package = self._repo.get_runner_deployment_package(deployment_id=deployment_id)
        if not package:
            raise ValueError("deployment_not_found")
        encrypted = str(package.get("password_encrypted") or "").strip()
        if not encrypted:
            raise ValueError("account_credentials_unavailable")
        secret_payload = self._crypto.decrypt_json(encrypted)
        password = str((secret_payload or {}).get("password") or "").strip()
        if not password:
            raise ValueError("account_credentials_unavailable")
        runner_id = str(package.get("deployment_runner_id") or package.get("binding_runner_id") or "").strip()
        slot_id = str(package.get("deployment_slot_id") or package.get("binding_slot_id") or "").strip()
        resource_hints = normalize_runner_payload_identity(
            {"resource_hints": package.get("resource_hints") or {}},
            runner_id=runner_id,
            slot_id=slot_id,
        ).get("resource_hints") or {}
        bot_contract = {
            "bot_id": package.get("catalog_bot_code") or package.get("bot_code"),
            "bot_name": package.get("catalog_bot_name") or package.get("deployment_bot_name"),
            "display_name": package.get("display_name") or package.get("deployment_bot_name"),
            "language": package.get("language") or "other",
            "version": package.get("version") or "",
            "profile_class": package.get("catalog_profile_class") or package.get("deployment_profile_class"),
            "runtime_entry": package.get("runtime_entry") or "",
            "required_params": package.get("required_params") or [],
            "risk_profile": package.get("risk_profile") or {},
            "indicator_requirements": package.get("indicator_requirements") or [],
            "strategy_tags": package.get("strategy_tags") or [],
            "resource_hints": resource_hints,
            "supports_demo": bool(package.get("supports_demo", True)),
            "supports_live": bool(package.get("supports_live", True)),
            "default_config_path": package.get("default_config_path"),
            "runtime_env": package.get("runtime_env") or {},
            "checksum": package.get("checksum") or "",
            "source_path": package.get("source_path") or "",
        }
        deployment_config = normalize_deployment_config(
            bot=bot_contract,
            config=package.get("config_json") or {},
        )
        return {
            "deployment_id": int(package["deployment_id"]),
            "account_id": int(package["account_id"]),
            "trace_id": package.get("trace_id"),
            "account": {
                "account_id": int(package["account_id"]),
                "user_id": int(package["user_id"]),
                "broker": package.get("broker"),
                "server": package.get("server"),
                "login": package.get("login"),
                "password": password,
                "status": package.get("account_status"),
                "label": package.get("label"),
                "last_error": package.get("account_last_error"),
            },
            "binding": {
                "binding_id": package.get("binding_id"),
                "runner_id": package.get("binding_runner_id"),
                "slot_id": package.get("binding_slot_id"),
                "binding_state": package.get("binding_state"),
                "is_sticky": package.get("is_sticky"),
                "is_current": package.get("is_current"),
                "last_used_at": package.get("last_used_at"),
            },
            "deployment": {
                "deployment_id": int(package["deployment_id"]),
                "bot_code": package.get("bot_code"),
                "bot_name": package.get("deployment_bot_name"),
                "profile_class": package.get("deployment_profile_class"),
                "status": package.get("deployment_status"),
                "desired_state": package.get("desired_state"),
                "is_active": package.get("is_active"),
                "runner_id": package.get("deployment_runner_id"),
                "slot_id": package.get("deployment_slot_id"),
                "config_contract_version": TRADING_CONFIG_SCHEMA_VERSION,
                "config": deployment_config,
                "trace_id": package.get("trace_id"),
                "health_status": package.get("health_status"),
                "last_error": package.get("deployment_last_error"),
                "last_heartbeat_at": package.get("last_heartbeat_at"),
                "started_at": package.get("started_at"),
                "stopped_at": package.get("stopped_at"),
            },
            "bot": bot_contract,
        }

    def miniapp_dashboard(self, *, telegram_id: str, username: Optional[str]) -> dict[str, Any]:
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        user_id = int(user["id"])
        cached = self._dashboard_cache_get(user_id=user_id)
        if cached is not None:
            cached["cache"] = {"hit": True, "ttl_sec": self._dashboard_cache_ttl_sec}
            return cached
        base = self._metrics.user_dashboard(user_id=user_id)
        base["accounts"] = self._repo.list_accounts_for_user(user_id=user_id)
        base["deployments"] = self._repo.list_deployments(user_id=user_id)
        base["cache"] = {"hit": False, "ttl_sec": self._dashboard_cache_ttl_sec}
        return self._dashboard_cache_set(user_id=user_id, payload=base)

    def runtime_health_summary(self) -> dict[str, Any]:
        return self._metrics.runtime_health_summary()

    def ops_summary_snapshot(self) -> dict[str, Any]:
        return self._repo.get_ops_summary_snapshot(
            runner_stale_sec=self._runner_stale_sec(),
            deployment_stale_sec=self._deployment_stale_sec(),
        )

    def runner_readiness_snapshot(self, *, runner_id: str) -> dict[str, Any]:
        return self._repo.get_runner_readiness_snapshot(
            runner_id=runner_id,
            runner_stale_sec=self._runner_stale_sec(),
        )

    def runner_health_dashboard(self) -> dict[str, Any]:
        stale_sec = self._runner_stale_sec()
        runners = self._repo.list_runners_health(stale_sec=stale_sec)
        queue_depths = _runner_queue_depths([str(item.get("runner_id") or "") for item in runners])
        for runner in runners:
            runner_id = str(runner.get("runner_id") or "").strip()
            depths = queue_depths.get(runner_id) or {
                "verification": 0,
                "verification_processing": 0,
                "commands": 0,
                "commands_processing": 0,
            }
            runner["queue_depth"] = depths
        summary = {
            "total_runners": len(runners),
            "online_runners": sum(1 for item in runners if str(item.get("status") or "").strip().lower() == "online"),
            "stale_runners": sum(1 for item in runners if bool(item.get("is_stale"))),
            "ready_runners": sum(1 for item in runners if bool(item.get("accepts_new_work"))),
            "maintenance_runners": sum(1 for item in runners if str(item.get("operational_status") or "") == "MAINTENANCE"),
            "full_runners": sum(1 for item in runners if str(item.get("operational_status") or "") == "FULL"),
            "degraded_runners": sum(1 for item in runners if str(item.get("operational_status") or "") == "DEGRADED"),
            "total_slots": sum(int(item.get("total_slots") or 0) for item in runners),
            "healthy_slots": sum(int(item.get("healthy_slots") or 0) for item in runners),
            "available_slots": sum(int(item.get("available_slots") or 0) for item in runners),
            "allocated_slots": sum(int(item.get("allocated_slots") or 0) for item in runners),
            "verifying_slots": sum(int(item.get("verifying_slots") or 0) for item in runners),
            "degraded_slots": sum(int(item.get("degraded_slots") or 0) for item in runners),
            "broken_slots": sum(int(item.get("broken_slots") or 0) for item in runners),
            "stale_slots": sum(int(item.get("stale_slots") or 0) for item in runners),
            "running_deployments": sum(int(item.get("running_deployments") or 0) for item in runners),
            "failed_deployments": sum(int(item.get("failed_deployments") or 0) for item in runners),
            "verification_queue_depth": sum(int((item.get("queue_depth") or {}).get("verification") or 0) for item in runners),
            "verification_processing_depth": sum(int((item.get("queue_depth") or {}).get("verification_processing") or 0) for item in runners),
            "command_queue_depth": sum(int((item.get("queue_depth") or {}).get("commands") or 0) for item in runners),
            "command_processing_depth": sum(int((item.get("queue_depth") or {}).get("commands_processing") or 0) for item in runners),
        }
        summary["capacity_available"] = bool(summary["ready_runners"] > 0)
        return {
            "generated_at": int(time.time()),
            "thresholds": {
                "runner_stale_sec": stale_sec,
                "slot_stale_sec": stale_sec,
            },
            "summary": summary,
            "runners": runners,
        }

    def get_runner_health(self, *, runner_id: str) -> Optional[dict[str, Any]]:
        stale_sec = self._runner_stale_sec()
        runner = self._repo.get_runner_health(runner_id=runner_id, stale_sec=stale_sec)
        if not runner:
            return None
        queue_depth = _runner_queue_depths([runner_id]).get(runner_id) or {
            "verification": 0,
            "verification_processing": 0,
            "commands": 0,
            "commands_processing": 0,
        }
        runner["queue_depth"] = queue_depth
        return {
            "generated_at": int(time.time()),
            "thresholds": {
                "runner_stale_sec": stale_sec,
                "slot_stale_sec": stale_sec,
            },
            "runner": runner,
        }

    def reconcile_runtime_health(self) -> dict[str, int]:
        return self._reconciler.reconcile_once()

    def _runner_stale_sec(self) -> int:
        return max(30, int(getattr(settings, "CONTROL_PLANE_RUNNER_STALE_SEC", 180) or 180))

    def _deployment_stale_sec(self) -> int:
        return max(30, int(getattr(settings, "CONTROL_PLANE_DEPLOYMENT_STALE_SEC", 180) or 180))

    # ------------------------------------------------------------------
    # Performance metrics (Sprint 5)
    # ------------------------------------------------------------------
    def get_deployment_performance(
        self,
        *,
        telegram_id: str,
        username: Optional[str],
        deployment_id: int,
        days_window: int = 30,
        tz_offset_min: int = 0,
    ) -> dict[str, Any]:
        from app.services.deployment_performance import compute_performance_metrics

        user = self.ensure_user(telegram_id=telegram_id, username=username)
        try:
            events = self._repo.list_deployment_order_filled_events(
                deployment_id=int(deployment_id),
                user_id=int(user["id"]),
                limit=10000,
            )
        except ValueError:
            raise
        metrics = compute_performance_metrics(
            events,
            days_window=int(days_window),
            tz_offset_min=int(tz_offset_min),
        )
        metrics["deployment_id"] = int(deployment_id)
        return metrics

    # ------------------------------------------------------------------
    # Notification preferences (Sprint 5)
    # ------------------------------------------------------------------
    def get_notification_preferences(
        self,
        *,
        telegram_id: str,
        username: Optional[str],
    ) -> dict[str, Any]:
        from app.services.notification_preferences import (
            default_preferences,
            known_channels,
            known_events,
            normalize_preferences,
        )

        user = self.ensure_user(telegram_id=telegram_id, username=username)
        try:
            metadata = self._repo.get_user_metadata(user_id=int(user["id"])) or {}
        except Exception:
            metadata = {}
        stored = metadata.get("notification_preferences")
        prefs = normalize_preferences(stored) if stored is not None else default_preferences()
        return {
            "preferences": prefs,
            "available_channels": known_channels(),
            "available_events": known_events(),
        }

    def update_notification_preferences(
        self,
        *,
        telegram_id: str,
        username: Optional[str],
        preferences: dict[str, Any],
    ) -> dict[str, Any]:
        from app.services.notification_preferences import (
            known_channels,
            known_events,
            normalize_preferences,
        )

        if not isinstance(preferences, dict):
            raise ValueError("invalid_request")
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        normalized = normalize_preferences(preferences)
        self._repo.update_user_metadata(
            user_id=int(user["id"]),
            metadata_patch={"notification_preferences": normalized},
        )
        self._store.add_audit(
            telegram_id=telegram_id,
            action="user.notification_preferences.update",
            payload={"channels": normalized.get("channels")},
            result="updated",
        )
        return {
            "preferences": normalized,
            "available_channels": known_channels(),
            "available_events": known_events(),
        }

    # ------------------------------------------------------------------
    # Onboarding (Sprint 4)
    # ------------------------------------------------------------------
    def get_user_onboarding(self, *, telegram_id: str, username: Optional[str]) -> dict[str, Any]:
        """Tra trang thai onboarding tour cho user.

        5 steps mac dinh: telegram_login, connect_account, select_bot,
        start_bot, set_risk_policy.
        """
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        user_id = int(user["id"])
        accounts = self._repo.list_accounts_for_user(user_id=user_id) or []
        deployments = self._repo.list_deployments(user_id=user_id) or []
        try:
            risk_count = int(self._repo.count_user_accounts_with_risk_policy(user_id=user_id) or 0)
        except Exception:
            risk_count = 0
        try:
            metadata = self._repo.get_user_metadata(user_id=user_id) or {}
        except Exception:
            metadata = {}
        # Suy luan completion
        has_account = len(accounts) > 0
        has_deployment_non_draft = any(
            str(d.get("status") or "").lower() != "draft" for d in deployments
        )
        has_running = any(
            str(d.get("status") or "").lower() in {"start_requested", "starting", "running"}
            for d in deployments
        )
        has_risk = risk_count > 0

        steps_def = [
            ("telegram_login", "Đăng nhập Telegram", "Telegram login", True),
            ("connect_account", "Kết nối tài khoản MT5", "Connect MT5 account", has_account),
            ("select_bot", "Chọn bot", "Select a bot", has_deployment_non_draft),
            ("start_bot", "Bật bot đầu tiên", "Start your first bot", has_running),
            ("set_risk_policy", "Đặt giới hạn rủi ro", "Set risk limits", has_risk),
        ]
        available = [
            {"key": k, "label_vi": vi, "label_en": en, "completed": bool(c)}
            for (k, vi, en, c) in steps_def
        ]
        completed_steps = [s["key"] for s in available if s["completed"]]
        next_step = next((s["key"] for s in available if not s["completed"]), None)

        return {
            "user_id": user_id,
            "completed_steps": completed_steps,
            "next_step": next_step,
            "available_steps": available,
            "dismissed": bool(metadata.get("onboarding_dismissed")),
        }

    def dismiss_user_onboarding(self, *, telegram_id: str, username: Optional[str]) -> dict[str, Any]:
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        user_id = int(user["id"])
        self._repo.update_user_metadata(
            user_id=user_id,
            metadata_patch={"onboarding_dismissed": True},
        )
        body = self.get_user_onboarding(telegram_id=telegram_id, username=username)
        body["dismissed"] = True
        return body

    # ------------------------------------------------------------------
    # User webhooks (Sprint 4)
    # ------------------------------------------------------------------
    def list_user_webhooks(self, *, telegram_id: str, username: Optional[str]) -> list[dict[str, Any]]:
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        rows = self._repo.list_user_webhooks(user_id=int(user["id"]), include_secret=False)
        return rows or []

    def create_user_webhook(
        self,
        *,
        telegram_id: str,
        username: Optional[str],
        url: str,
        event_filter: list[str],
    ) -> dict[str, Any]:
        """Tao webhook cho user. Tra ve secret_hex MOT LAN; sau khong bao gio expose lai.

        - URL phai bat dau https?:// (basic check) -> raise invalid_request neu sai.
        - event_filter normalize len upper-case + de-dup.
        - secret_hex 32 byte (64 hex chars), HMAC-SHA256 sau dung.
        """
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise ValueError("invalid_request")
        normalized_filter = []
        seen: set[str] = set()
        for evt in (event_filter or []):
            v = str(evt or "").strip().upper()
            if v and v not in seen:
                normalized_filter.append(v)
                seen.add(v)
        import secrets as _secrets

        secret_hex = _secrets.token_hex(32)
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        row = self._repo.create_user_webhook(
            user_id=int(user["id"]),
            url=str(url).strip(),
            secret_hex=secret_hex,
            event_filter=normalized_filter,
        )
        self._store.add_audit(
            telegram_id=telegram_id,
            action="user.webhook.create",
            payload={"webhook_id": row.get("id"), "url": row.get("url"), "event_filter": normalized_filter},
            result="created",
        )
        return row

    def delete_user_webhook(
        self,
        *,
        telegram_id: str,
        username: Optional[str],
        webhook_id: int,
    ) -> dict[str, Any]:
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        deleted = self._repo.delete_user_webhook(
            user_id=int(user["id"]),
            webhook_id=int(webhook_id),
        )
        if deleted:
            self._store.add_audit(
                telegram_id=telegram_id,
                action="user.webhook.delete",
                payload={"webhook_id": int(webhook_id)},
                result="deleted",
            )
        return {"id": int(webhook_id), "deleted": bool(deleted)}

    # ------------------------------------------------------------------
    # Quota / billing (Sprint 3)
    # ------------------------------------------------------------------
    def get_user_quota(self, *, telegram_id: str, username: Optional[str]) -> dict[str, Any]:
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        subscription = self._repo.get_user_active_subscription(user_id=int(user["id"]))
        # Go-live policy: moi Telegram ID chi duoc dung 1 bot, tinh ca paper mode.
        active_count = self._repo.count_user_active_deployments(
            user_id=int(user["id"]),
            include_paper=True,
        )
        account_count = self._repo.count_user_accounts(user_id=int(user["id"]))
        limits_override = _ADMIN_QUOTA_LIMITS if _is_admin_telegram_id(telegram_id) else _SINGLE_TELEGRAM_BOT_LIMITS
        return describe_quota(
            subscription=subscription,
            active_deployment_count=active_count,
            account_count=account_count,
            limits_override=limits_override,
        )

    # ------------------------------------------------------------------
    # User self / GDPR (Sprint 3)
    # ------------------------------------------------------------------
    def list_user_activity(self, *, telegram_id: str, limit: int = 50) -> list[dict[str, Any]]:
        from app.services.audit_formatter import format_audit_rows

        rows = self._store.list_audit(telegram_id=telegram_id, limit=int(limit))
        return format_audit_rows(rows)

    def export_user_data(self, *, telegram_id: str, username: Optional[str]) -> dict[str, Any]:
        """GDPR data export: snapshot toan bo data thuoc user (KHONG kem credential).

        Tra dict gon, FE co the JSON.stringify roi cho user download.
        """
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        user_id = int(user["id"])
        accounts = self._repo.list_accounts_for_user(user_id=user_id)
        deployments = self._repo.list_deployments(user_id=user_id)
        subscription = self._repo.get_user_active_subscription(user_id=user_id)
        # Risk policies per account
        risk_policies: list[dict[str, Any]] = []
        verifications: list[dict[str, Any]] = []
        for account in accounts:
            account_id = int(account.get("id") or 0)
            if account_id <= 0:
                continue
            try:
                policy = self._repo.get_account_risk_policy(account_id=account_id, user_id=user_id) or {}
            except Exception:
                policy = {}
            risk_policies.append({"account_id": account_id, "policy": policy})
            try:
                jobs = self._repo.list_account_verification_jobs(account_id=account_id, user_id=user_id, limit=200)
            except Exception:
                jobs = []
            verifications.extend(jobs)
        try:
            audit = self._store.list_audit(telegram_id=telegram_id, limit=200)
        except Exception:
            audit = []
        from app.services.audit_formatter import format_audit_rows

        return {
            "user": {
                "id": user_id,
                "telegram_id": telegram_id,
                "username": user.get("username"),
                "created_at": str(user.get("created_at") or ""),
            },
            "subscription": subscription,
            "accounts": accounts,
            "deployments": deployments,
            "risk_policies": risk_policies,
            "verifications": verifications,
            "audit_recent": format_audit_rows(audit),
            "exported_at": int(time.time()),
            "notes": (
                "Plaintext credentials are never exported. "
                "Audit/financial trails older than 200 entries are intentionally excluded "
                "from this export but kept in the system for legal compliance."
            ),
        }

    async def soft_delete_user(
        self,
        *,
        telegram_id: str,
        username: Optional[str],
        reason: Optional[str],
    ) -> dict[str, Any]:
        """GDPR right-to-erasure (soft).

        Steps:
          1. Stop moi running deployment cua user.
          2. Cancel all pending verification jobs cua user (per account).
          3. Mark accounts.status='disconnected', clear credential blob.
          4. Audit log 'user.delete'.

        KHONG xoa hard cac event/audit financial -> giu compliance trail.
        """
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        user_id = int(user["id"])
        cancel_reason = (reason or "user_self_delete").strip()[:200]

        deployments = self._repo.list_deployments(user_id=user_id)
        deployments_stopped: list[int] = []
        deployments_failed: list[dict[str, Any]] = []
        for deployment in deployments:
            status = str(deployment.get("status") or "").strip().lower()
            if status not in {"start_requested", "starting", "running", "stop_requested"}:
                continue
            full = self._repo.get_deployment(deployment_id=int(deployment["id"]), user_id=user_id)
            if not full:
                continue
            try:
                # Neu start_requested/starting -> dung cancel_pending; con lai dung stop_deployment
                if status in {"start_requested", "starting"}:
                    await self._deployment_manager.cancel_pending_deployment(
                        deployment=full,
                        reason=f"user_delete:{cancel_reason}",
                    )
                else:
                    await self._deployment_manager.stop_deployment(
                        deployment=full,
                        reason=f"user_delete:{cancel_reason}",
                    )
                deployments_stopped.append(int(deployment["id"]))
            except Exception as exc:
                deployments_failed.append({"deployment_id": int(deployment["id"]), "error": str(exc)[:200]})

        # Cancel pending verification jobs per account
        accounts = self._repo.list_accounts_for_user(user_id=user_id)
        verification_cancelled_total = 0
        for account in accounts:
            account_id = int(account.get("id") or 0)
            if account_id <= 0:
                continue
            try:
                bulk = await self._verification_manager.cancel_all_verifications_for_account(
                    user_id=user_id,
                    account_id=account_id,
                    reason=f"user_delete:{cancel_reason}",
                )
                verification_cancelled_total += int(bulk.get("cancelled_count") or 0)
            except Exception:
                continue

        # Soft-delete accounts (mark disconnected + clear credentials)
        accounts_soft_deleted = self._repo.soft_delete_user_accounts(
            user_id=user_id,
            reason=cancel_reason,
        )

        self._store.add_audit(
            telegram_id=telegram_id,
            action="user.delete",
            payload={
                "user_id": user_id,
                "deployments_stopped_count": len(deployments_stopped),
                "deployments_failed_count": len(deployments_failed),
                "verification_cancelled_total": verification_cancelled_total,
                "accounts_soft_deleted_count": accounts_soft_deleted,
                "reason": cancel_reason,
            },
            result="soft_deleted",
        )
        return {
            "user_id": user_id,
            "deployments_stopped": deployments_stopped,
            "deployments_failed": deployments_failed,
            "verification_cancelled_total": verification_cancelled_total,
            "accounts_soft_deleted_count": accounts_soft_deleted,
            "status": "soft_deleted",
            "notes": (
                "Account data has been soft-deleted. Audit logs and execution history "
                "are retained for legal compliance per directive."
            ),
        }

    # ------------------------------------------------------------------
    # Risk policy + circuit breaker (Sprint 2)
    # ------------------------------------------------------------------
    def get_account_risk_policy(self, *, telegram_id: str, username: Optional[str], account_id: int) -> dict[str, Any]:
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        return self._risk_policy.get_policy(user_id=int(user["id"]), account_id=int(account_id))

    def update_account_risk_policy(
        self,
        *,
        telegram_id: str,
        username: Optional[str],
        account_id: int,
        policy: dict[str, Any],
    ) -> dict[str, Any]:
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        stored = self._risk_policy.update_policy(
            user_id=int(user["id"]),
            account_id=int(account_id),
            policy=policy,
            actor=str(telegram_id),
        )
        self._store.add_audit(
            telegram_id=telegram_id,
            action="account.risk_policy.update",
            payload={"account_id": int(account_id), "policy": stored},
            result="updated",
        )
        return stored

    async def evaluate_account_circuit_breaker(
        self,
        *,
        telegram_id: str,
        username: Optional[str],
        account_id: int,
    ) -> dict[str, Any]:
        user = self.ensure_user(telegram_id=telegram_id, username=username)
        result = await self._risk_policy.evaluate_circuit_breaker(
            user_id=int(user["id"]),
            account_id=int(account_id),
            actor=str(telegram_id),
        )
        if result.get("auto_stop_triggered"):
            self._store.add_audit(
                telegram_id=telegram_id,
                action="account.circuit_breaker.trigger",
                payload={
                    "account_id": int(account_id),
                    "realized_pnl_today": result.get("realized_pnl_today"),
                    "policy": result.get("policy"),
                    "deployments_stopped_count": len(result.get("deployments_stopped") or []),
                },
                result="auto_stopped",
            )
        return result


@lru_cache(maxsize=1)
def get_control_plane_service() -> MT5ControlPlaneService:
    return MT5ControlPlaneService()


def reset_control_plane_service() -> None:
    get_control_plane_service.cache_clear()
