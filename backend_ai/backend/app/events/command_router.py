from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from app.core.error_log import log_agent_event, log_agent_failure, log_agent_warning
from app.core.log_context import bind_log_context
from app.infra.redis_streams import RedisStreamPublisher
from app.models.control_plane import CommandType
from app.orchestration.runner_payload_identity import (
    normalize_runner_command_payload,
    runner_command_request_type,
)
from app.repositories.control_plane_repository import ControlPlaneRepository
from app.services import login_lease
from runner.schemas.commands import RunnerCommand


_log = logging.getLogger("runner.command.dispatch")


def _canonical_slot_id(value: Any) -> str:
    raw = str(value or "").strip()
    lowered = raw.lower()
    if lowered.startswith("slot_") or lowered.startswith("slot-"):
        return f"slot-{raw[5:]}"
    return raw


def _coerce_command_type(value: Any) -> CommandType:
    if isinstance(value, CommandType):
        return value
    return CommandType(str(value))


def _runner_queue_name(runner_id: Any) -> str:
    runner_id_s = str(runner_id or "").strip()
    if not runner_id_s:
        raise ValueError("runner_id_required")
    return f"mt5:runner:{runner_id_s}:commands"


def _dict_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _slot_inventory_entry(slot: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    direct_entry = metadata.get("slot_inventory_entry")
    if isinstance(direct_entry, dict):
        return direct_entry
    inventory = metadata.get("slot_inventory")
    if not isinstance(inventory, list):
        return {}
    slot_id = _canonical_slot_id(slot.get("slot_id"))
    storage_slot_id = str(metadata.get("storage_slot_id") or "").strip()
    for item in inventory:
        if not isinstance(item, dict):
            continue
        item_slot_id = _canonical_slot_id(item.get("slot_id") or item.get("storage_slot_id"))
        item_storage_slot_id = str(item.get("storage_slot_id") or "").strip()
        if item_slot_id == slot_id or (storage_slot_id and item_storage_slot_id == storage_slot_id):
            return dict(item)
    return {}


def _slot_runtime_hints_from_slots(slots: list[dict[str, Any]], *, runner_id: str, slot_id: str) -> dict[str, Any]:
    runner_id_s = str(runner_id or "").strip()
    slot_id_s = _canonical_slot_id(slot_id)
    if not runner_id_s or not slot_id_s:
        return {}
    for slot in slots:
        if str(slot.get("runner_id") or "").strip() != runner_id_s:
            continue
        if _canonical_slot_id(slot.get("slot_id")) != slot_id_s:
            continue
        metadata = _dict_payload(slot.get("metadata_json") or slot.get("metadata"))
        inventory_entry = _slot_inventory_entry(slot, metadata)
        hints: dict[str, Any] = {}
        for source in (metadata, inventory_entry):
            for key, value in source.items():
                if value not in (None, ""):
                    hints[str(key)] = value
        return hints
    return {}


class CommandRouterService:
    def __init__(self, repo: ControlPlaneRepository) -> None:
        self._repo = repo
        self._publisher = RedisStreamPublisher()

    def _slot_runtime_hints(self, *, runner_id: str, slot_id: str) -> dict[str, Any]:
        try:
            slots = self._repo.list_slots()
        except Exception:
            return {}
        return _slot_runtime_hints_from_slots(slots, runner_id=runner_id, slot_id=slot_id)

    async def dispatch(
        self,
        *,
        command_type: CommandType,
        account_id: int,
        deployment_id: int | None,
        bot_id: str,
        runner_id: str,
        slot_id: str,
        priority: int,
        payload: dict[str, Any],
        trace_id: str,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        command_type = _coerce_command_type(command_type)
        command_id_value = str(command_id or uuid.uuid4().hex).strip()
        runner_id_s = str(runner_id or "").strip()
        runner_queue_name = _runner_queue_name(runner_id_s)
        slot_id_s = _canonical_slot_id(slot_id)
        requested_cmd_type = runner_command_request_type(command_type)
        routed_payload = normalize_runner_command_payload(
            payload or {},
            command_type=command_type,
            account_id=account_id,
            deployment_id=deployment_id,
            runner_id=runner_id_s,
            slot_id=slot_id_s,
            slot_runtime_hints=self._slot_runtime_hints(runner_id=runner_id_s, slot_id=slot_id_s),
        )
        routed_payload["command_id"] = command_id_value
        routed_payload["bot_id"] = bot_id
        routed_payload["trace_id"] = trace_id
        routed_payload["priority"] = priority
        envelope_model = RunnerCommand.model_validate(
            {
                "command_id": command_id_value,
                "command_type": command_type.value,
                "cmd_type": requested_cmd_type,
                "requested_cmd_type": requested_cmd_type,
                "account_id": account_id,
                "profile_id": account_id,
                "deployment_id": deployment_id,
                "bot_id": bot_id,
                "runner_id": runner_id_s,
                "slot_id": slot_id_s,
                "priority": priority,
                "payload": routed_payload,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "trace_id": trace_id,
            }
        )
        bind_log_context(
            account_id=account_id,
            deployment_id=deployment_id,
            runner_id=runner_id_s,
            trace_id=trace_id,
        )
        dispatch_ctx = {
            "command_id": envelope_model.command_id,
            "command_type": command_type.value,
            "runner_id": runner_id_s,
            "slot_id": slot_id_s,
            "account_id": account_id,
            "deployment_id": deployment_id,
            "bot_id": bot_id,
            "priority": priority,
        }

        existing = (
            self._repo.get_execution_command_by_trace_identity(
                account_id=account_id,
                deployment_id=deployment_id,
                command_type=command_type.value,
                trace_id=trace_id,
            )
            if deployment_id is not None
            else None
        )
        if existing:
            log_agent_event(
                _log,
                logging.INFO,
                "runner.command.dispatch.coalesced",
                hint="An identical command for this trace already exists; reused. No new dispatch.",
                operation="command_dispatch",
                outcome="coalesced",
                existing_command_id=str(existing.get("command_id") or "").strip(),
                **dispatch_ctx,
            )
            return existing
        command_record = self._repo.create_execution_command(
            command_id=envelope_model.command_id,
            command_type=command_type.value,
            account_id=account_id,
            deployment_id=deployment_id,
            bot_id=bot_id,
            runner_id=runner_id_s,
            slot_id=slot_id_s,
            priority=priority,
            payload=envelope_model.payload,
            trace_id=trace_id,
            queue_name=runner_queue_name,
        )
        if str(command_record.get("command_id") or "").strip() != envelope_model.command_id:
            log_agent_event(
                _log,
                logging.INFO,
                "runner.command.dispatch.recovered",
                hint="DB returned an existing row instead of creating new one. Likely race or duplicate trace_id.",
                operation="command_dispatch",
                outcome="recovered",
                returned_command_id=str(command_record.get("command_id") or "").strip(),
                **dispatch_ctx,
            )
            return command_record

        # ---------------------------------------------------------------
        # Latest-intent guard. For START_BOT we re-read the deployment row
        # right before publishing so a stale START (eg. queued replacement
        # whose user pressed OFF in the meantime) cannot escape to Redis.
        # `intent_seq` on the payload — set at command creation by the
        # orchestration layer — is compared against the deployment's current
        # intent_seq; payload value < current means a newer user action has
        # invalidated this command and we must drop.
        # ---------------------------------------------------------------
        if command_type == CommandType.START_BOT:
            intent_state = self._repo.get_deployment_intent_state(deployment_id=deployment_id)
            current_desired_state = str((intent_state or {}).get("desired_state") or "").strip().lower()
            current_status = str((intent_state or {}).get("status") or "").strip().lower()
            current_intent_seq = (intent_state or {}).get("intent_seq")
            payload_intent_seq = routed_payload.get("intent_seq")
            drop_reason: str | None = None
            if not intent_state:
                drop_reason = "deployment_missing"
            elif current_desired_state != "running":
                drop_reason = "desired_state_stopped"
            elif current_status in {"stopped", "failed", "blocked"}:
                drop_reason = "deployment_terminal"
            else:
                try:
                    if (
                        payload_intent_seq is not None
                        and current_intent_seq is not None
                        and int(payload_intent_seq) < int(current_intent_seq)
                    ):
                        drop_reason = "stale_intent"
                except Exception:
                    drop_reason = None
            if drop_reason:
                self._repo.mark_command_failed_pre_publish(
                    command_id=envelope_model.command_id,
                    reason=drop_reason,
                )
                log_agent_event(
                    _log,
                    logging.INFO,
                    "runner.command.dispatch.dropped",
                    hint=(
                        "START_BOT dropped before Redis publish — desired_state/intent_seq diverged from the user's latest intent. "
                        "Not a runner issue: backend refused to deliver a START the user already invalidated."
                    ),
                    operation="command_dispatch",
                    outcome="dropped",
                    dispatch_decision="dropped",
                    drop_reason=drop_reason,
                    desired_state=current_desired_state or None,
                    deployment_status=current_status or None,
                    intent_seq=int(payload_intent_seq) if payload_intent_seq is not None else None,
                    latest_seq=int(current_intent_seq) if current_intent_seq is not None else None,
                    **dispatch_ctx,
                )
                return {
                    **command_record,
                    "delivery_status": "failed",
                    "dispatch_decision": "dropped",
                    "drop_reason": drop_reason,
                }

        # ---------------------------------------------------------------
        # Distributed login lease (spec §2.2). For START_BOT only.
        # Idempotent — same runner re-acquiring just renews TTL. Conflicts
        # with another runner are blocked iff LOGIN_LEASE_ENFORCED=True;
        # otherwise we log a WARN and proceed (telemetry-only mode).
        # ---------------------------------------------------------------
        login_s = str((routed_payload or {}).get("login") or "").strip()
        if command_type == CommandType.START_BOT and login_s and login_lease.is_enabled():
            acquire_result = await login_lease.acquire(
                login=login_s,
                runner_id=runner_id_s,
                command_id=envelope_model.command_id,
                broker=str(routed_payload.get("broker") or "") or None,
                server=str(routed_payload.get("server") or "") or None,
                account_id=account_id,
            )
            if not acquire_result.ok:
                if login_lease.is_enforced():
                    if acquire_result.reason == "redis_unavailable":
                        # Fail-closed per spec §2.5
                        raise RuntimeError("login_lease_unavailable")
                    raise login_lease.LoginLeaseConflict(acquire_result)
                # Telemetry-only mode — log already emitted by login_lease module.
                log_agent_warning(
                    _log,
                    "runner.command.dispatch.lease_conflict_unenforced",
                    hint=(
                        "Lease conflict but LOGIN_LEASE_ENFORCED=False — dispatching anyway. "
                        "Flip the flag to start blocking. Owner: see prior login_lease.conflict log line."
                    ),
                    error_code="login_busy_unenforced",
                    operation="command_dispatch",
                    login=login_s,
                    lease_owner_runner_id=acquire_result.owner_runner_id,
                    **dispatch_ctx,
                )

        publish_started = time.monotonic()
        try:
            stream_id = await self._publisher.publish_command(envelope_model.model_dump(mode="json"))
        except Exception as exc:
            log_agent_failure(
                _log,
                "runner.command.dispatch.publish_failed",
                error=exc,
                error_code="redis_publish_failed",
                operation="command_dispatch",
                hint=(
                    "Backend created the command row in Postgres but failed to publish to Redis. "
                    "Reconciler will retry; check Redis health and `mt5:runner:{runner_id}:commands` queue. "
                    "If repeats, inspect command_delivery_reconciler logs for the same command_id."
                ),
                publish_elapsed_ms=round((time.monotonic() - publish_started) * 1000, 1),
                **dispatch_ctx,
            )
            # If publish failed AFTER acquiring the lease, release it so the
            # command can be retried (by reconciler or by user) without being
            # blocked by a phantom lease.
            if command_type == CommandType.START_BOT and login_s and login_lease.is_enabled():
                try:
                    await login_lease.release(login=login_s, runner_id=runner_id_s)
                except Exception:
                    pass
            raise

        self._repo.mark_command_delivery(command_id=envelope_model.command_id, status="queued", redis_stream_id=stream_id)
        command_record["delivery_status"] = "queued"
        command_record["redis_stream_id"] = stream_id

        log_agent_event(
            _log,
            logging.INFO,
            "runner.command.dispatch.queued",
            hint="Command queued to Redis; expect runner to BRPOP/dequeue within a few seconds.",
            operation="command_dispatch",
            outcome="queued",
            dispatch_decision="sent",
            redis_stream_id=stream_id,
            publish_elapsed_ms=round((time.monotonic() - publish_started) * 1000, 1),
            **dispatch_ctx,
        )
        return command_record

    async def dispatch_batch(
        self,
        *,
        items: list[dict[str, Any]],
        broadcast_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fan-out N commands in 1 Redis pipeline round-trip.

        Designed for TradingView signal broadcast where 1 alert maps to N
        accounts × M runners. Each item is a full envelope:
            {command_type: CommandType, account_id, deployment_id, bot_id,
             runner_id, slot_id, priority, payload, trace_id,
             command_id (optional)}

        Steps:
          1. Build RunnerCommand envelopes + create execution_commands rows
             (sequential — Postgres single inserts ~1-2ms each, dominated by
             Redis pipeline anyway).
          2. Pipeline-publish all envelopes to Redis (single round-trip).
          3. Mark delivery status per command.

        Returns one result dict per input item, in order:
            {ok: bool, command_record, stream_id, duplicate, error?}

        Whole-batch Redis failures raise. Per-item DB failures are captured in
        the result dict so partial success is reported back to caller.
        """
        if not items:
            return []
        broadcast_marker = str(broadcast_id or uuid.uuid4().hex[:12])
        slots_snapshot: list[dict[str, Any]] | None = None
        slot_hints_cache: dict[tuple[str, str], dict[str, Any]] = {}

        def _batch_slot_runtime_hints(*, runner_id: str, slot_id: str) -> dict[str, Any]:
            nonlocal slots_snapshot
            key = (str(runner_id or "").strip(), _canonical_slot_id(slot_id))
            if key in slot_hints_cache:
                return dict(slot_hints_cache[key])
            if slots_snapshot is None:
                try:
                    slots_snapshot = self._repo.list_slots()
                except Exception:
                    slots_snapshot = []
            hints = _slot_runtime_hints_from_slots(slots_snapshot, runner_id=key[0], slot_id=key[1])
            slot_hints_cache[key] = hints
            return dict(hints)

        # Phase 1: build envelopes + create DB rows (sequential, fast).
        envelopes: list[Any] = []
        records: list[dict[str, Any] | None] = []
        errors: list[str | None] = []
        for item in items:
            try:
                command_type = item["command_type"]
                if not isinstance(command_type, CommandType):
                    command_type = CommandType(str(command_type))
                account_id = int(item["account_id"])
                deployment_id = int(item["deployment_id"])
                runner_id = str(item["runner_id"]).strip()
                runner_queue_name = _runner_queue_name(runner_id)
                slot_id = str(item.get("slot_id") or "").strip()
                bot_id = str(item.get("bot_id") or "")
                priority = int(item.get("priority") or 50)
                trace_id = str(item.get("trace_id") or uuid.uuid4().hex)
                command_id_value = str(item.get("command_id") or uuid.uuid4().hex).strip()
                payload = item.get("payload") or {}

                requested_cmd_type = runner_command_request_type(command_type)
                routed_payload = normalize_runner_command_payload(
                    payload,
                    command_type=command_type,
                    account_id=account_id,
                    deployment_id=deployment_id,
                    runner_id=runner_id,
                    slot_id=slot_id,
                    slot_runtime_hints=_batch_slot_runtime_hints(runner_id=runner_id, slot_id=slot_id),
                )
                routed_payload["command_id"] = command_id_value
                routed_payload["bot_id"] = bot_id
                routed_payload["trace_id"] = trace_id
                routed_payload["priority"] = priority
                routed_payload["broadcast_id"] = broadcast_marker
                envelope_model = RunnerCommand.model_validate(
                    {
                        "command_id": command_id_value,
                        "command_type": command_type.value,
                        "cmd_type": requested_cmd_type,
                        "requested_cmd_type": requested_cmd_type,
                        "account_id": account_id,
                        "profile_id": account_id,
                        "deployment_id": deployment_id,
                        "bot_id": bot_id,
                        "runner_id": runner_id,
                        "slot_id": slot_id,
                        "priority": priority,
                        "payload": routed_payload,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "trace_id": trace_id,
                    }
                )

                # Idempotency guard at DB level — caller chooses unique trace_id
                # per (account, signal); duplicate (vd. TradingView retry) returns
                # the existing row instead of inserting twice.
                existing = self._repo.get_execution_command_by_trace_identity(
                    account_id=account_id,
                    deployment_id=deployment_id,
                    command_type=command_type.value,
                    trace_id=trace_id,
                )
                if existing:
                    envelopes.append(None)  # skip publish
                    records.append({**existing, "delivery_status": "deduped_existing"})
                    errors.append(None)
                    continue

                command_record = self._repo.create_execution_command(
                    command_id=envelope_model.command_id,
                    command_type=command_type.value,
                    account_id=account_id,
                    deployment_id=deployment_id,
                    bot_id=bot_id,
                    runner_id=runner_id,
                    slot_id=slot_id,
                    priority=priority,
                    payload=envelope_model.payload,
                    trace_id=trace_id,
                    queue_name=runner_queue_name,
                )
                envelopes.append(envelope_model)
                records.append(command_record)
                errors.append(None)
            except Exception as exc:
                envelopes.append(None)
                records.append(None)
                errors.append(f"{exc.__class__.__name__}:{str(exc)[:160]}")

        # Phase 2: pipeline publish only the freshly-created envelopes.
        publish_inputs: list[dict[str, Any]] = []
        publish_index_map: list[int] = []
        for idx, env in enumerate(envelopes):
            if env is None:
                continue
            publish_inputs.append(env.model_dump(mode="json"))
            publish_index_map.append(idx)
        publish_pos_by_input_index = {
            input_idx: pos
            for pos, input_idx in enumerate(publish_index_map)
        }

        publish_started = time.monotonic()
        pipeline_results: list[dict[str, Any]] = []
        if publish_inputs:
            try:
                pipeline_results = await self._publisher.publish_command_batch(publish_inputs)
            except Exception as exc:
                log_agent_failure(
                    _log,
                    "runner.command.dispatch_batch.publish_failed",
                    error=exc,
                    error_code="redis_pipeline_failed",
                    operation="command_dispatch_batch",
                    hint=(
                        "The whole Redis pipeline for fan-out batch failed. DB rows have "
                        "been created already → reconciler will replay them within ~15s. "
                        "If repeats, check Redis health + REDIS_WRITE_URL."
                    ),
                    broadcast_id=broadcast_marker,
                    batch_size=len(publish_inputs),
                )
                # Mark all freshly-created rows as failed-publish so caller can
                # decide; reconciler will pick them up regardless.
                for k, idx in enumerate(publish_index_map):
                    errors[idx] = f"redis_pipeline_failed:{exc.__class__.__name__}"
                pipeline_results = []

        # Phase 3: mark delivery + assemble results.
        out: list[dict[str, Any]] = []
        for idx in range(len(items)):
            err = errors[idx]
            record = records[idx]
            if err:
                out.append({"ok": False, "error": err, "command_record": record})
                continue
            if envelopes[idx] is None:
                # Was a dedupe hit — already populated in Phase 1.
                out.append({"ok": True, "command_record": record, "deduped": True})
                continue
            # Find this envelope's pipeline result
            pos = publish_pos_by_input_index.get(idx, -1)
            pub = pipeline_results[pos] if 0 <= pos < len(pipeline_results) else {}
            stream_id = str(pub.get("stream_id") or "")
            duplicate = bool(pub.get("duplicate"))
            if not stream_id:
                out.append({"ok": False, "error": pub.get("error") or "publish_no_stream_id", "command_record": record})
                continue
            try:
                self._repo.mark_command_delivery(
                    command_id=envelopes[idx].command_id,
                    status="queued",
                    redis_stream_id=stream_id,
                )
            except Exception as exc:
                # Delivery mark failure is non-fatal — Redis already has the
                # command; reconciler will sync DB later.
                log_agent_warning(
                    _log,
                    "runner.command.dispatch_batch.mark_delivery_failed",
                    hint="Redis publish OK but DB mark_command_delivery failed; reconciler will heal.",
                    error=exc,
                    error_code="db_mark_delivery_failed",
                    command_id=envelopes[idx].command_id,
                )
            record_out = {**(record or {}), "delivery_status": "queued", "redis_stream_id": stream_id}
            out.append({"ok": True, "command_record": record_out, "duplicate": duplicate})

        elapsed_ms = round((time.monotonic() - publish_started) * 1000, 1)
        log_agent_event(
            _log,
            logging.INFO,
            "runner.command.dispatch_batch.completed",
            hint="Fan-out batch dispatched. Inspect per-item results for failures.",
            operation="command_dispatch_batch",
            outcome="completed",
            broadcast_id=broadcast_marker,
            batch_size=len(items),
            published=sum(1 for r in out if r.get("ok") and not r.get("deduped")),
            deduped=sum(1 for r in out if r.get("deduped")),
            failed=sum(1 for r in out if not r.get("ok")),
            pipeline_elapsed_ms=elapsed_ms,
        )
        return out
