from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel

from app.orchestration.runner_payload_identity import normalize_runner_command_payload
from runner.schemas.commands import RunnerCommand


class CommandQueueItem(RunnerCommand):
    pass


class QueueEnvelope(BaseModel):
    queue_kind: str
    queue_name: str
    raw: str
    processing_queue_name: Optional[str] = None
    command: Optional[CommandQueueItem] = None


def decode_queue_payload(queue_name: str, raw: str) -> QueueEnvelope:
    text = str(raw or "")
    parsed = json.loads(text.strip() or "{}")
    if queue_name.endswith(":commands"):
        return QueueEnvelope(
            queue_kind="command",
            queue_name=queue_name,
            raw=text,
            command=CommandQueueItem.model_validate(parsed),
        )
    return QueueEnvelope(queue_kind="unknown", queue_name=queue_name, raw=text)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_optional_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _normalize_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _iso_timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    raw = str(value or "").strip()
    return raw or datetime.now(timezone.utc).isoformat()


def build_runner_command_from_row(row: dict[str, Any]) -> RunnerCommand:
    command_type = str(row.get("command_type") or "").strip()
    account_id = _safe_int(row.get("account_id"))
    deployment_id = _safe_optional_int(row.get("deployment_id"))
    runner_id = str(row.get("runner_id") or "").strip()
    slot_id = str(row.get("slot_id") or "").strip()
    payload = normalize_runner_command_payload(
        _normalize_payload(row.get("payload_json")),
        command_type=command_type,
        account_id=account_id,
        deployment_id=deployment_id,
        runner_id=runner_id,
        slot_id=slot_id,
    )
    payload["command_id"] = str(row.get("command_id") or "").strip()
    payload["bot_id"] = str(row.get("bot_id") or "").strip()
    payload["trace_id"] = str(row.get("trace_id") or row.get("command_id") or "").strip()
    payload["priority"] = _safe_int(row.get("priority"))
    return RunnerCommand.model_validate(
        {
            "command_id": str(row.get("command_id") or "").strip(),
            "command_type": command_type,
            "account_id": account_id,
            "profile_id": account_id,
            "deployment_id": deployment_id,
            "bot_id": str(row.get("bot_id") or "").strip(),
            "runner_id": runner_id,
            "slot_id": slot_id,
            "priority": _safe_int(row.get("priority")),
            "payload": payload,
            "created_at": _iso_timestamp(row.get("created_at")),
            "trace_id": str(row.get("trace_id") or row.get("command_id") or "").strip(),
        }
    )
