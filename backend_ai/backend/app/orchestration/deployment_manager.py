from __future__ import annotations

import logging
import uuid
from typing import Any

from app.bot_catalog.mt5_repository_loader import MT5BotCatalogLoader
from app.core.error_log import log_agent_event
from app.core.redis_client import get_resolved_redis_write_url
from app.events.command_router import CommandRouterService
from app.models.control_plane import CommandType
from app.orchestration.deployment_config import TRADING_CONFIG_SCHEMA_VERSION, normalize_deployment_config
from app.orchestration.scheduler import SchedulerDecision, choose_slot_for_account
from app.repositories.control_plane_repository import ControlPlaneRepository
from app.risk.orchestration_policy import (
    OrchestrationPolicyError,
    validate_account_ready,
    validate_bot_available,
    validate_runtime_command_request,
    validate_start_request,
)
from app.settings import settings

_intent_log = logging.getLogger("runner.command.dispatch")


def _normalize_deployment_mode(mode: str | None) -> str:
    normalized = str(mode or "live").strip().lower()
    if normalized not in {"live", "paper"}:
        raise ValueError("invalid_request")
    return normalized


def _is_stopped_like_deployment(deployment: dict[str, Any]) -> bool:
    status = str(deployment.get("status") or "").strip().lower()
    desired_state = str(deployment.get("desired_state") or "").strip().lower()
    is_active = bool(deployment.get("is_active"))
    if status in {"stopped", "failed", "blocked"}:
        return True
    return bool(desired_state == "stopped" and not is_active and status not in {"start_requested", "starting", "running"})


def _runtime_start_guard_stale_sec() -> int:
    return max(30, int(getattr(settings, "ACCOUNT_RUNTIME_START_GUARD_STALE_SEC", 180) or 180))


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


_BOT_EXECUTION_CONTRACT_TEXT_KEYS = (
    "bot_type",
    "execution_owner",
    "windows_role",
    "tradingview_webhook_owner",
)
_BOT_EXECUTION_CONTRACT_BOOL_KEYS = ("requires_executor_slot",)


def _contract_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _iter_contract_sources(*sources: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        out.append(source)
        for nested_key in ("execution_contract", "manifest_contract", "bot_contract"):
            nested = source.get(nested_key)
            if isinstance(nested, dict):
                out.append(nested)
        metadata = source.get("metadata") or source.get("metadata_json")
        if isinstance(metadata, dict):
            out.extend(_iter_contract_sources(metadata))
    return out


def _bot_execution_contract(*sources: Any) -> dict[str, Any]:
    contract: dict[str, Any] = {}
    for source in _iter_contract_sources(*sources):
        for key in _BOT_EXECUTION_CONTRACT_TEXT_KEYS:
            value = str(source.get(key) or "").strip()
            if value and key not in contract:
                contract[key] = value
        for key in _BOT_EXECUTION_CONTRACT_BOOL_KEYS:
            if key in source and key not in contract:
                contract[key] = _contract_bool(source.get(key))
    return contract


def _merge_bot_execution_contract(target: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    for key, value in contract.items():
        if value is not None:
            target[key] = value
    return target


def _runner_queue_depths(runner_ids: list[str]) -> dict[str, dict[str, int]]:
    ids = sorted({str(item or "").strip() for item in runner_ids if str(item or "").strip()})
    if not ids:
        return {}
    try:
        from redis import Redis

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
        out[runner_id][name] = _safe_int(value)
    return out


def _inject_runner_queue_depths(slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    depths_by_runner = _runner_queue_depths([str(slot.get("runner_id") or "") for slot in slots])
    if not depths_by_runner:
        return slots
    threshold = max(1, int(getattr(settings, "SCHEDULER_RUNNER_QUEUE_BACKLOG_THRESHOLD", 20) or 20))
    out: list[dict[str, Any]] = []
    for slot in slots:
        item = dict(slot)
        runner_id = str(item.get("runner_id") or "").strip()
        depths = depths_by_runner.get(runner_id)
        if depths:
            command_depth = _safe_int(depths.get("commands")) + _safe_int(depths.get("commands_processing"))
            verification_depth = _safe_int(depths.get("verification")) + _safe_int(depths.get("verification_processing"))
            item["runner_command_queue_depth"] = command_depth
            item["runner_verification_queue_depth"] = verification_depth
            item["runner_queue_backlog_threshold"] = threshold
            item["runner_queue_depth"] = dict(depths)
        out.append(item)
    return out


class DeploymentManagerService:
    def __init__(
        self,
        repo: ControlPlaneRepository,
        *,
        catalog_loader: MT5BotCatalogLoader | None = None,
        command_router: CommandRouterService | None = None,
    ) -> None:
        self._repo = repo
        self._catalog_loader = catalog_loader or MT5BotCatalogLoader(repo=repo)
        self._command_router = command_router or CommandRouterService(repo)

    def refresh_catalog(self, *, force: bool = False) -> list[dict[str, Any]]:
        return self._catalog_loader.sync_catalog(force=force)

    def select_bot(self, *, user_id: int, account_id: int, bot_name: str, bot_config_overrides: dict[str, Any]) -> dict[str, Any]:
        self._catalog_loader.sync_catalog(force=False)
        bot = self._repo.get_bot_by_name(bot_name=bot_name)
        if not bot:
            raise ValueError("bot_not_found")
        account = self._repo.get_account(account_id=account_id, user_id=user_id)
        if not account:
            raise ValueError("account_not_found")
        trace_id = uuid.uuid4().hex
        deployment_config = normalize_deployment_config(bot=bot, config=bot_config_overrides)
        draft = self._repo.create_deployment_draft(
            user_id=user_id,
            account_id=account_id,
            bot=bot,
            bot_config=deployment_config,
            trace_id=trace_id,
        )
        draft["bot"] = bot
        return draft

    def _pick_slot(self, *, account_id: int, bot: dict[str, Any]) -> SchedulerDecision:
        slots = _inject_runner_queue_depths(self._repo.list_slots())
        sticky = self._repo.get_current_binding(account_id=account_id)
        return choose_slot_for_account(
            account_id=account_id,
            bot=bot,
            slots=slots,
            sticky_binding=sticky,
        )

    def _start_payload(
        self,
        *,
        account: dict[str, Any],
        bot: dict[str, Any],
        deployment: dict[str, Any],
        deployment_config: dict[str, Any],
        mode: str,
        sticky_reused: bool,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resource_hints = dict(bot.get("resource_hints") or {})
        runtime_env = dict(bot.get("runtime_env") or {})
        risk_profile = dict(bot.get("risk_profile") or {})
        execution_contract = _bot_execution_contract(
            bot,
            resource_hints,
            runtime_env,
            risk_profile,
            bot.get("metadata"),
        )
        _merge_bot_execution_contract(resource_hints, execution_contract)
        _merge_bot_execution_contract(runtime_env, execution_contract)

        payload = {
            "account_id": int(account["id"]),
            "mode": mode,
            "broker": account.get("broker"),
            "server": account.get("server"),
            "login": account.get("login"),
            "bot_code": bot.get("bot_code") or bot.get("bot_id") or deployment.get("bot_code"),
            "bot_name": bot.get("bot_name") or deployment.get("bot_name"),
            "bot_version": bot.get("version") or "",
            "runtime_entry": bot.get("runtime_entry") or "",
            "profile_class": bot.get("profile_class") or deployment.get("profile_class") or "",
            "resource_hints": resource_hints,
            "config_contract_version": TRADING_CONFIG_SCHEMA_VERSION,
            "config": deployment_config,
            "sticky_reused": sticky_reused,
        }
        payload.update(execution_contract)
        required_params = list(bot.get("required_params") or [])
        if required_params:
            payload["required_params"] = required_params
        if runtime_env:
            payload["runtime_env"] = runtime_env
        if risk_profile:
            payload["risk_profile"] = risk_profile
        if extra:
            payload.update(dict(extra))
        return payload

    async def _request_replacement_start(
        self,
        *,
        user_id: int,
        account: dict[str, Any],
        bot: dict[str, Any],
        bot_config_overrides: dict[str, Any],
        mode: str,
        blocker: dict[str, Any],
    ) -> dict[str, Any]:
        previous_deployment_id = int(blocker.get("id") or blocker.get("blocker_deployment_id") or 0)
        if previous_deployment_id <= 0:
            raise OrchestrationPolicyError("account_runtime_orphan_requires_operator_cleanup")
        previous_record = self._repo.get_deployment(deployment_id=previous_deployment_id)
        if not previous_record:
            raise OrchestrationPolicyError("account_runtime_orphan_requires_operator_cleanup")
        blocker = {**blocker, **previous_record}

        runner_id = str(blocker.get("runner_id") or blocker.get("blocker_runner_id") or "").strip()
        slot_id = str(blocker.get("slot_id") or blocker.get("blocker_slot_id") or "").strip()
        if not runner_id or not slot_id:
            raise OrchestrationPolicyError("account_runtime_binding_missing")

        queued = self._repo.get_queued_replacement_deployment(account_id=int(account["id"]))
        if not queued:
            queued = self._repo.create_queued_replacement_deployment(
                user_id=user_id,
                account_id=int(account["id"]),
                bot=bot,
                bot_config=normalize_deployment_config(bot=bot, config=bot_config_overrides),
                trace_id=uuid.uuid4().hex,
                mode=mode,
                previous_deployment_id=previous_deployment_id,
            )

        existing_stop = self._repo.get_open_replacement_stop_command(
            previous_deployment_id=previous_deployment_id,
            replacement_deployment_id=int(queued["id"]),
        )
        if existing_stop:
            return {
                "deployment": queued,
                "command": existing_stop,
                "bot": bot,
                "queued_start": True,
                "previous_deployment": blocker,
                "scheduler": {
                    "runner_id": runner_id,
                    "slot_id": slot_id,
                    "reason": "replacement_stop_already_in_progress",
                    "sticky_reused": True,
                },
            }

        previous = self._repo.update_deployment_status(
            deployment_id=previous_deployment_id,
            status="stop_requested",
            desired_state="stopped",
            is_active=True,
            health_status="replacement_stop_requested",
            runner_id=runner_id,
            slot_id=slot_id,
        ) or blocker
        # Account-level intent bump so any prior in-flight START on this account
        # is recognized as stale by the dispatch guard. Snapshot the replacement
        # deployment's resulting seq + the previous deployment's seq onto the
        # STOP payload so subsequent transitions can correlate.
        seq_snapshot = {int(row["id"]): int(row.get("intent_seq") or 0) for row in self._repo.bump_account_intent_seq(account_id=int(account["id"]))}
        replacement_intent_seq = seq_snapshot.get(int(queued["id"]))
        previous_intent_seq = seq_snapshot.get(int(previous_deployment_id))
        trace_id = f"{queued.get('trace_id') or uuid.uuid4().hex}:stop_previous"
        try:
            command = await self._command_router.dispatch(
                command_type=CommandType.STOP_BOT,
                account_id=int(account["id"]),
                deployment_id=previous_deployment_id,
                bot_id=str(previous.get("bot_code") or bot.get("bot_code") or bot.get("bot_id") or ""),
                runner_id=runner_id,
                slot_id=slot_id,
                priority=115,
                payload={
                    "reason": "replacement_start_requires_previous_stop",
                    "control_flow": "deployment_replacement_start",
                    "replacement_deployment_id": int(queued["id"]),
                    "previous_deployment_id": previous_deployment_id,
                    "restart_policy": "stop_previous_then_start_replacement",
                    "replacement_intent_seq": replacement_intent_seq,
                    "intent_seq": previous_intent_seq,
                },
                trace_id=trace_id,
            )
        except Exception:
            self._repo.update_deployment_status(
                deployment_id=int(queued["id"]),
                status="failed",
                desired_state="stopped",
                is_active=False,
                health_status="replacement_stop_enqueue_failed",
                last_error="replacement_stop_enqueue_failed",
            )
            self._repo.update_deployment_status(
                deployment_id=previous_deployment_id,
                status=str(blocker.get("status") or "running"),
                desired_state=str(blocker.get("desired_state") or "running"),
                is_active=bool(blocker.get("is_active", True)),
                health_status="replacement_stop_enqueue_failed",
                last_error="replacement_stop_enqueue_failed",
                runner_id=runner_id,
                slot_id=slot_id,
            )
            raise

        self._repo.insert_deployment_audit(
            deployment_id=int(queued["id"]),
            action="deployment.replacement_start_waiting_previous_stop",
            payload={
                "deployment_id": int(queued["id"]),
                "account_id": int(account["id"]),
                "previous_deployment_id": previous_deployment_id,
                "stop_command_id": command.get("command_id"),
                "runner_id": runner_id,
                "slot_id": slot_id,
                "blocker_source": blocker.get("blocker_source"),
            },
            result="stop_queued",
            trace_id=command.get("trace_id") or trace_id,
        )
        return {
            "deployment": queued,
            "command": command,
            "bot": bot,
            "queued_start": True,
            "previous_deployment": previous,
            "scheduler": {
                "runner_id": runner_id,
                "slot_id": slot_id,
                "reason": "waiting_previous_runtime_stop",
                "sticky_reused": True,
            },
        }

    async def start_queued_replacement_deployment(
        self,
        *,
        replacement_deployment_id: int,
        previous_deployment: dict[str, Any],
        stop_command: dict[str, Any],
    ) -> dict[str, Any] | None:
        queued = self._repo.get_deployment(deployment_id=int(replacement_deployment_id))
        if not queued or str(queued.get("status") or "").strip().lower() != "queued":
            return None

        account = self._repo.get_account(account_id=int(queued["account_id"]), user_id=int(queued["user_id"]))
        bot_name = str(queued.get("bot_code") or queued.get("bot_name") or "").strip()
        bot = self._repo.get_bot_by_name(bot_name=bot_name)
        validate_start_request(
            account=account,
            bot=bot,
            active_deployment=self._repo.get_active_deployment_for_account(account_id=int(queued["account_id"])),
        )
        self._repo.reconcile_terminal_bot_control_commands(account_id=int(queued["account_id"]))
        pending_command = self._repo.get_pending_account_start_stop_command(account_id=int(queued["account_id"]))
        if pending_command:
            raise OrchestrationPolicyError("start_transition_in_progress")

        self._repo.prepare_sticky_slot_for_reuse(account_id=int(queued["account_id"]))
        decision = self._pick_slot(account_id=int(queued["account_id"]), bot=bot or {})
        if not decision.ok:
            raise OrchestrationPolicyError(decision.reason or "no_scheduler_candidate")

        binding = self._repo.allocate_slot_binding(
            account_id=int(queued["account_id"]),
            runner_id=decision.runner_id,
            slot_id=decision.slot_id,
            sticky=True,
        )
        # Latest-intent guard #1: the user may have pressed OFF while we were
        # waiting for the previous deployment to stop. Re-read state and bail
        # before we activate (which would flip desired_state back to running).
        latest = self._repo.get_deployment_intent_state(deployment_id=int(queued["id"]))
        latest_desired = str((latest or {}).get("desired_state") or "").strip().lower()
        latest_status = str((latest or {}).get("status") or "").strip().lower()
        if latest_desired != "running" or latest_status != "queued":
            log_agent_event(
                _intent_log,
                logging.INFO,
                "runner.command.dispatch.dropped",
                hint="Skipping start_replacement: user already flipped the queued deployment off (or it isn't queued anymore).",
                operation="start_replacement",
                outcome="dropped",
                dispatch_decision="dropped",
                drop_reason="desired_state_stopped" if latest_desired != "running" else "replacement_not_queued",
                command_type=CommandType.START_BOT.value,
                account_id=int(queued["account_id"]),
                deployment_id=int(queued["id"]),
                trace_id=stop_command.get("trace_id"),
                desired_state=latest_desired or None,
                deployment_status=latest_status or None,
                stop_command_id=stop_command.get("command_id"),
            )
            self._repo.insert_deployment_audit(
                deployment_id=int(queued["id"]),
                action="deployment.replacement_start_dropped",
                payload={
                    "deployment_id": int(queued["id"]),
                    "account_id": int(queued["account_id"]),
                    "stop_command_id": stop_command.get("command_id"),
                    "drop_reason": "desired_state_stopped" if latest_desired != "running" else "replacement_not_queued",
                    "desired_state": latest_desired or None,
                    "deployment_status": latest_status or None,
                },
                result="dropped",
                trace_id=stop_command.get("trace_id"),
            )
            return None

        # Latest-intent guard #2: the STOP that triggered us captured a snapshot
        # of intent_seq when the replacement was queued; if the account has been
        # touched again since (eg. OFF then ON), drop and let the newer intent
        # drive the flow.
        recorded_seq = (stop_command.get("payload_json") or {}).get("replacement_intent_seq")
        current_seq = (latest or {}).get("intent_seq")
        if recorded_seq is not None and current_seq is not None:
            try:
                if int(current_seq) > int(recorded_seq):
                    log_agent_event(
                        _intent_log,
                        logging.INFO,
                        "runner.command.dispatch.dropped",
                        hint="Skipping start_replacement: intent_seq advanced since the STOP was issued.",
                        operation="start_replacement",
                        outcome="dropped",
                        dispatch_decision="dropped",
                        drop_reason="stale_intent",
                        command_type=CommandType.START_BOT.value,
                        account_id=int(queued["account_id"]),
                        deployment_id=int(queued["id"]),
                        intent_seq=int(recorded_seq),
                        latest_seq=int(current_seq),
                        stop_command_id=stop_command.get("command_id"),
                    )
                    return None
            except Exception:
                pass

        start_trace_id = f"{stop_command.get('trace_id') or uuid.uuid4().hex}:start_replacement"
        updated = self._repo.activate_queued_deployment_start(
            deployment_id=int(queued["id"]),
            runner_id=decision.runner_id,
            slot_id=decision.slot_id,
            binding_id=int(binding["id"]),
            trace_id=start_trace_id,
        )
        if not updated:
            return None

        deployment_config = normalize_deployment_config(bot=bot, config=updated.get("config_json") or {})
        payload = self._start_payload(
            account=account or {},
            bot=bot or {},
            deployment=updated,
            deployment_config=deployment_config,
            mode=str(updated.get("mode") or "live").strip().lower() or "live",
            sticky_reused=bool(decision.sticky_reused),
            extra={
                "control_flow": "deployment_replacement_start",
                "restart_policy": "stop_previous_then_start_replacement",
                "previous_deployment_id": int(previous_deployment.get("id") or stop_command.get("deployment_id") or 0),
                "replacement_stop_command_id": stop_command.get("command_id"),
                "intent_seq": int(updated.get("intent_seq") or 0),
            },
        )
        command = await self._command_router.dispatch(
            command_type=CommandType.START_BOT,
            account_id=int(updated["account_id"]),
            deployment_id=int(updated["id"]),
            bot_id=str((bot or {}).get("bot_code") or (bot or {}).get("bot_id") or updated.get("bot_code") or ""),
            runner_id=decision.runner_id,
            slot_id=decision.slot_id,
            priority=90 if str((bot or {}).get("profile_class") or "") == "heavy" else 50,
            payload=payload,
            trace_id=start_trace_id,
        )
        self._repo.insert_deployment_audit(
            deployment_id=int(updated["id"]),
            action="deployment.replacement_start_requested",
            payload={
                "deployment_id": int(updated["id"]),
                "account_id": int(updated["account_id"]),
                "previous_deployment_id": int(previous_deployment.get("id") or stop_command.get("deployment_id") or 0),
                "stop_command_id": stop_command.get("command_id"),
                "start_command_id": command.get("command_id"),
                "runner_id": decision.runner_id,
                "slot_id": decision.slot_id,
                "binding_id": binding.get("id") if isinstance(binding, dict) else None,
            },
            result="start_queued",
            trace_id=command.get("trace_id") or start_trace_id,
        )
        return {
            "deployment": updated,
            "command": command,
            "bot": bot,
            "scheduler": {
                "runner_id": decision.runner_id,
                "slot_id": decision.slot_id,
                "reason": "previous_runtime_stopped",
                "sticky_reused": bool(decision.sticky_reused),
            },
        }

    async def start_deployment(
        self,
        *,
        user_id: int,
        account: dict[str, Any],
        bot_name: str,
        bot_config_overrides: dict[str, Any],
        mode: str = "live",
        start_payload_extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        deployment_mode = _normalize_deployment_mode(mode)
        self._catalog_loader.sync_catalog(force=False)
        bot = self._repo.get_bot_by_name(bot_name=bot_name)
        if deployment_mode == "paper" and bot and bot.get("supports_demo") is False:
            raise ValueError("paper_mode_unavailable")
        validate_account_ready(account)
        validate_bot_available(bot)
        self._repo.reconcile_terminal_bot_control_commands(account_id=int(account["id"]))
        blocker = self._repo.get_account_runtime_start_blocker(
            account_id=int(account["id"]),
            fresh_sec=_runtime_start_guard_stale_sec(),
        )
        if blocker:
            if str(blocker.get("blocker_source") or "") == "runtime_duplicate":
                raise OrchestrationPolicyError("account_runtime_duplicate_requires_operator_cleanup")
            return await self._request_replacement_start(
                user_id=user_id,
                account=account,
                bot=bot or {},
                bot_config_overrides=bot_config_overrides,
                mode=deployment_mode,
                blocker=blocker,
            )
        verification_job = self._repo.get_active_account_verification_job(account_id=int(account["id"]))
        if verification_job:
            raise OrchestrationPolicyError("account_verification_in_progress")
        pending_command = self._repo.get_pending_account_start_stop_command(account_id=int(account["id"]))
        if pending_command:
            raise OrchestrationPolicyError("start_transition_in_progress")
        self._repo.prepare_sticky_slot_for_reuse(account_id=int(account["id"]))

        decision = self._pick_slot(account_id=int(account["id"]), bot=bot or {})
        if not decision.ok and decision.reason == "sticky_slot_unavailable":
            # Public START can arrive in the small window after BOT_STOPPED
            # persisted but before slot projection has fully settled. Reconcile
            # the same-account sticky slot once more, then classify any real
            # in-flight START/STOP as a retryable transition instead of a hard
            # sticky-slot failure.
            pending_command = self._repo.get_pending_account_start_stop_command(account_id=int(account["id"]))
            if pending_command:
                raise OrchestrationPolicyError("start_transition_in_progress")
            self._repo.prepare_sticky_slot_for_reuse(account_id=int(account["id"]))
            decision = self._pick_slot(account_id=int(account["id"]), bot=bot or {})
            if not decision.ok and decision.reason == "sticky_slot_unavailable":
                pending_command = self._repo.get_pending_account_start_stop_command(account_id=int(account["id"]))
                if pending_command:
                    raise OrchestrationPolicyError("start_transition_in_progress")
        if not decision.ok:
            raise OrchestrationPolicyError(decision.reason or "no_scheduler_candidate")

        deployment_config = normalize_deployment_config(bot=bot, config=bot_config_overrides)
        binding = self._repo.allocate_slot_binding(
            account_id=int(account["id"]),
            runner_id=decision.runner_id,
            slot_id=decision.slot_id,
            sticky=True,
        )
        trace_id = uuid.uuid4().hex
        deployment = self._repo.create_started_deployment(
            user_id=user_id,
            account_id=int(account["id"]),
            bot=bot or {},
            bot_config=deployment_config,
            runner_id=decision.runner_id,
            slot_id=decision.slot_id,
            binding_id=int(binding["id"]),
            trace_id=trace_id,
            mode=deployment_mode,
        )
        # Capture the resulting intent_seq onto the START_BOT payload so the
        # dispatch guard and the runner can correlate against the user's
        # latest intent (see CommandRouterService.dispatch).
        new_seq = self._repo.bump_deployment_intent_seq(deployment_id=int(deployment["id"]))
        if new_seq is not None:
            deployment["intent_seq"] = new_seq

        start_extra = dict(start_payload_extra or {})
        if "intent_seq" not in start_extra and new_seq is not None:
            start_extra["intent_seq"] = int(new_seq)
        payload = self._start_payload(
            account=account,
            bot=bot or {},
            deployment=deployment,
            deployment_config=deployment_config,
            mode=deployment_mode,
            sticky_reused=bool(decision.sticky_reused),
            extra=start_extra,
        )
        command = await self._command_router.dispatch(
            command_type=CommandType.START_BOT,
            account_id=int(account["id"]),
            deployment_id=int(deployment["id"]),
            bot_id=str(bot.get("bot_code") or bot.get("bot_id") or ""),
            runner_id=decision.runner_id,
            slot_id=decision.slot_id,
            priority=90 if str(bot.get("profile_class") or "") == "heavy" else 50,
            payload=payload,
            trace_id=trace_id,
        )
        return {
            "deployment": deployment,
            "command": command,
            "bot": bot,
            "scheduler": {
                "runner_id": decision.runner_id,
                "slot_id": decision.slot_id,
                "reason": decision.reason,
                "sticky_reused": decision.sticky_reused,
            },
        }

    async def stop_deployment(self, *, deployment: dict[str, Any], reason: str | None = None) -> dict[str, Any]:
        if not deployment:
            raise ValueError("deployment_not_found")
        account_id = int(deployment["account_id"])
        deployment_id = int(deployment["id"])
        if _is_stopped_like_deployment(deployment):
            # Still honor the user OFF intent: cancel any queued replacement and
            # fail any in-flight START for the account so a stale START_BOT can
            # not race in after the no-op response.
            cancelled = self._repo.cancel_queued_replacements_for_account(
                account_id=account_id,
                reason=str(reason or "user_stop_request"),
            )
            failed_account_starts = self._repo.fail_pending_start_commands_for_account(
                account_id=account_id,
                reason="start_command_superseded_by_user_stop_account",
            )
            log_agent_event(
                _intent_log,
                logging.INFO,
                "deployment.stop.noop_with_intent_cleanup",
                hint="Deployment already stopped; still invalidated queued replacements and pending starts on the account.",
                operation="user_stop",
                outcome="noop",
                account_id=account_id,
                deployment_id=deployment_id,
                cancelled_replacement_ids=[row.get("id") for row in cancelled],
                failed_pending_start_commands=int(failed_account_starts),
                reason="deployment_already_stopped",
            )
            return {
                "deployment": deployment,
                "command": None,
                "noop": True,
                "reason": "deployment_already_stopped",
                "cancelled_replacement_ids": [row.get("id") for row in cancelled],
                "failed_pending_start_commands": int(failed_account_starts),
            }
        # OFF — flip desired_state and bump intent_seq before publishing the
        # STOP so any concurrent dispatch reads the latest intent.
        updated = self._repo.update_deployment_status(
            deployment_id=deployment_id,
            status="stop_requested",
            desired_state="stopped",
            is_active=True,
            health_status="stop_requested",
        )
        new_seq = self._repo.bump_deployment_intent_seq(deployment_id=deployment_id)
        # Cancel any queued replacement waiting for this deployment to stop —
        # this is the core fix for the start_replacement bug: without it, the
        # BOT_STOPPED ack for the previous deployment fires START_BOT on the
        # replacement even though the user just pressed OFF.
        cancelled = self._repo.cancel_queued_replacements_for_account(
            account_id=account_id,
            reason=str(reason or "user_stop_request"),
        )
        command = await self._command_router.dispatch(
            command_type=CommandType.STOP_BOT,
            account_id=account_id,
            deployment_id=deployment_id,
            bot_id=str(deployment.get("bot_code") or ""),
            runner_id=str(deployment.get("runner_id") or ""),
            slot_id=str(deployment.get("slot_id") or ""),
            priority=100,
            payload={
                "reason": reason or "user_stop_request",
                "intent_seq": int(new_seq) if new_seq is not None else None,
            },
            trace_id=str(deployment.get("trace_id") or uuid.uuid4().hex),
        )
        # Account-wide pending START failure must run AFTER the new STOP is
        # queued so the runner gets the OFF intent first and so any concurrent
        # dispatch sees the freshly-failed rows.
        self._repo.fail_pending_start_commands_for_deployment(
            deployment_id=deployment_id,
            reason="start_command_superseded_by_user_stop",
        )
        failed_account_starts = self._repo.fail_pending_start_commands_for_account(
            account_id=account_id,
            reason="start_command_superseded_by_user_stop_account",
        )
        log_agent_event(
            _intent_log,
            logging.INFO,
            "deployment.stop.intent_propagated",
            hint="User OFF propagated to queued replacements and pending START commands across the account.",
            operation="user_stop",
            outcome="propagated",
            account_id=account_id,
            deployment_id=deployment_id,
            intent_seq=int(new_seq) if new_seq is not None else None,
            cancelled_replacement_ids=[row.get("id") for row in cancelled],
            failed_pending_start_commands=int(failed_account_starts),
        )
        return {
            "deployment": updated,
            "command": command,
            "cancelled_replacement_ids": [row.get("id") for row in cancelled],
            "failed_pending_start_commands": int(failed_account_starts),
        }

    async def request_config_restart(
        self,
        *,
        deployment: dict[str, Any],
        trace_id: str,
    ) -> dict[str, Any]:
        if not deployment:
            raise ValueError("deployment_not_found")
        deployment_id = int(deployment["id"])
        account_id = int(deployment["account_id"])
        existing = self._repo.get_open_config_restart_command(deployment_id=deployment_id)
        if existing:
            return {
                "deployment": deployment,
                "command": existing,
                "coalesced": True,
                "reason": "config_restart_already_in_progress",
            }

        runner_id = str(deployment.get("runner_id") or "").strip()
        slot_id = str(deployment.get("slot_id") or "").strip()
        if not runner_id or not slot_id:
            raise OrchestrationPolicyError("deployment_runtime_binding_missing")

        updated = self._repo.update_deployment_status(
            deployment_id=deployment_id,
            status="stop_requested",
            desired_state="stopped",
            is_active=True,
            health_status="config_update_restart_requested",
        )
        try:
            command = await self._command_router.dispatch(
                command_type=CommandType.STOP_BOT,
                account_id=account_id,
                deployment_id=deployment_id,
                bot_id=str(deployment.get("bot_code") or ""),
                runner_id=runner_id,
                slot_id=slot_id,
                priority=110,
                payload={
                    "reason": "config_update_restart",
                    "control_flow": "deployment_config_restart",
                    "config_update_trace_id": trace_id,
                    "restart_policy": "stop_then_start_same_deployment",
                },
                trace_id=trace_id,
            )
        except Exception:
            self._repo.update_deployment_status(
                deployment_id=deployment_id,
                status=str(deployment.get("status") or "running"),
                desired_state=str(deployment.get("desired_state") or "running"),
                is_active=bool(deployment.get("is_active", True)),
                health_status="config_update_restart_enqueue_failed",
                last_error="config_update_restart_enqueue_failed",
                runner_id=runner_id,
                slot_id=slot_id,
            )
            raise
        return {"deployment": updated, "command": command, "coalesced": False}

    async def request_config_hot_update(
        self,
        *,
        deployment: dict[str, Any],
        config: dict[str, Any],
        trace_id: str,
    ) -> dict[str, Any]:
        validate_runtime_command_request(deployment=deployment, allowed_statuses={"running"})
        runner_id = str(deployment.get("runner_id") or "").strip()
        slot_id = str(deployment.get("slot_id") or "").strip()
        if not runner_id or not slot_id:
            raise OrchestrationPolicyError("deployment_runtime_binding_missing")

        command = await self._command_router.dispatch(
            command_type=CommandType.UPDATE_BOT_CONFIG,
            account_id=int(deployment["account_id"]),
            deployment_id=int(deployment["id"]),
            bot_id=str(deployment.get("bot_code") or ""),
            runner_id=runner_id,
            slot_id=slot_id,
            priority=105,
            payload={
                "config_contract_version": TRADING_CONFIG_SCHEMA_VERSION,
                "config": config or {},
            },
            trace_id=trace_id,
        )
        return {"deployment": deployment, "command": command}

    async def cancel_pending_deployment(
        self,
        *,
        deployment: dict[str, Any],
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Huy 1 deployment dang ket o status 'start_requested' hoac 'starting'.

        Khac voi stop_deployment (cho deployment dang chay):
          - Khong assume runner da nhan command START_BOT (co the chua).
          - Phat them STOP_BOT priority cao de runner kip skip neu vua bat dau.
          - Free slot binding ngay de slot duoc tai su dung.
          - Idempotent: deployment da o stopped/failed -> raise deployment_cannot_be_cancelled.
        """
        if not deployment:
            raise ValueError("deployment_not_found")

        current_status = str(deployment.get("status") or "").strip().lower()
        cancellable_statuses = {"start_requested", "starting"}
        if current_status not in cancellable_statuses:
            raise OrchestrationPolicyError("deployment_cannot_be_cancelled")

        cancel_reason = (reason or "cancelled_by_user").strip()[:200]

        updated = self._repo.update_deployment_status(
            deployment_id=int(deployment["id"]),
            status="stopped",
            desired_state="stopped",
            is_active=False,
            health_status="cancelled",
            last_error=cancel_reason,
            stopped=True,
        )
        self._repo.bump_deployment_intent_seq(deployment_id=int(deployment["id"]))
        # Cancel sibling queued replacements + fail any pending START for the
        # account so the runner never picks up a START_BOT that the user just
        # invalidated.
        cancelled_replacements = self._repo.cancel_queued_replacements_for_account(
            account_id=int(deployment["account_id"]),
            reason=cancel_reason,
        )
        self._repo.fail_pending_start_commands_for_account(
            account_id=int(deployment["account_id"]),
            reason="start_command_superseded_by_cancel_account",
        )

        runner_id = str(deployment.get("runner_id") or "").strip()
        slot_id = str(deployment.get("slot_id") or "").strip()
        if runner_id and slot_id:
            self._repo.release_account_slot_binding(
                account_id=int(deployment["account_id"]),
                runner_id=runner_id,
                slot_id=slot_id,
                keep_sticky=False,
            )

        command_payload = None
        if runner_id and slot_id:
            try:
                command_payload = await self._command_router.dispatch(
                    command_type=CommandType.STOP_BOT,
                    account_id=int(deployment["account_id"]),
                    deployment_id=int(deployment["id"]),
                    bot_id=str(deployment.get("bot_code") or ""),
                    runner_id=runner_id,
                    slot_id=slot_id,
                    priority=120,
                    payload={
                        "reason": cancel_reason,
                        "cancel_before_running": True,
                        "previous_status": current_status,
                    },
                    trace_id=str(deployment.get("trace_id") or uuid.uuid4().hex),
                )
                self._repo.fail_pending_start_commands_for_deployment(
                    deployment_id=int(deployment["id"]),
                    reason="start_command_superseded_by_cancel",
                )
            except Exception:
                command_payload = None

        return {
            "deployment": updated,
            "command": command_payload,
            "cancelled_from_status": current_status,
            "command_dispatched": command_payload is not None,
            "cancelled_replacement_ids": [row.get("id") for row in cancelled_replacements],
        }

    async def dispatch_runtime_command(
        self,
        *,
        deployment: dict[str, Any],
        command_type: CommandType,
        payload: dict[str, Any],
        priority: int = 50,
        trace_id: str | None = None,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        validate_runtime_command_request(deployment=deployment)
        if command_type not in {
            CommandType.PLACE_ORDER,
            CommandType.MODIFY_ORDER,
            CommandType.CLOSE_ORDER,
            CommandType.SYNC_STATE,
        }:
            raise OrchestrationPolicyError("unsupported_runtime_command")

        resolved_trace_id = str(trace_id or deployment.get("trace_id") or uuid.uuid4().hex).strip()
        command = await self._command_router.dispatch(
            command_type=command_type,
            account_id=int(deployment["account_id"]),
            deployment_id=int(deployment["id"]),
            bot_id=str(deployment.get("bot_code") or ""),
            runner_id=str(deployment.get("runner_id") or ""),
            slot_id=str(deployment.get("slot_id") or ""),
            priority=int(priority),
            payload=payload or {},
            trace_id=resolved_trace_id,
            command_id=command_id,
        )
        return {"deployment": deployment, "command": command}
