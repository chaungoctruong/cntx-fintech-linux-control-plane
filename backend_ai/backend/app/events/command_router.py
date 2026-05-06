from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from app.infra.redis_streams import RedisStreamPublisher
from app.models.control_plane import CommandType
from app.orchestration.runner_payload_identity import (
    normalize_runner_command_payload,
    runner_command_request_type,
)
from app.repositories.control_plane_repository import ControlPlaneRepository
from runner.schemas.commands import RunnerCommand


def _canonical_slot_id(value: Any) -> str:
    raw = str(value or "").strip()
    lowered = raw.lower()
    if lowered.startswith("slot_") or lowered.startswith("slot-"):
        return f"slot-{raw[5:]}"
    return raw


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


class CommandRouterService:
    def __init__(self, repo: ControlPlaneRepository) -> None:
        self._repo = repo
        self._publisher = RedisStreamPublisher()

    def _slot_runtime_hints(self, *, runner_id: str, slot_id: str) -> dict[str, Any]:
        runner_id_s = str(runner_id or "").strip()
        slot_id_s = _canonical_slot_id(slot_id)
        if not runner_id_s or not slot_id_s:
            return {}
        try:
            slots = self._repo.list_slots()
        except Exception:
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

    async def dispatch(
        self,
        *,
        command_type: CommandType,
        account_id: int,
        deployment_id: int,
        bot_id: str,
        runner_id: str,
        slot_id: str,
        priority: int,
        payload: dict[str, Any],
        trace_id: str,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        command_id_value = str(command_id or uuid.uuid4().hex).strip()
        requested_cmd_type = runner_command_request_type(command_type)
        routed_payload = normalize_runner_command_payload(
            payload or {},
            command_type=command_type,
            account_id=account_id,
            deployment_id=deployment_id,
            runner_id=runner_id,
            slot_id=slot_id,
            slot_runtime_hints=self._slot_runtime_hints(runner_id=runner_id, slot_id=slot_id),
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
                "runner_id": runner_id,
                "slot_id": slot_id,
                "priority": priority,
                "payload": routed_payload,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "trace_id": trace_id,
            }
        )
        existing = self._repo.get_execution_command_by_trace_identity(
            account_id=account_id,
            deployment_id=deployment_id,
            command_type=command_type.value,
            trace_id=trace_id,
        )
        if existing:
            return existing
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
            queue_name=f"mt5:account:{account_id}:commands",
        )
        if str(command_record.get("command_id") or "").strip() != envelope_model.command_id:
            return command_record
        stream_id = await self._publisher.publish_command(envelope_model.model_dump(mode="json"))
        self._repo.mark_command_delivery(command_id=envelope_model.command_id, status="queued", redis_stream_id=stream_id)
        command_record["delivery_status"] = "queued"
        command_record["redis_stream_id"] = stream_id
        return command_record
