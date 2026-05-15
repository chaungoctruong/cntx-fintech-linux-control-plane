from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class RunnerEventType(str, Enum):
    HEARTBEAT = "HEARTBEAT"
    LOGIN_SLOT_VERIFIED = "LOGIN_SLOT_VERIFIED"
    LOGIN_SLOT_FAILED = "LOGIN_SLOT_FAILED"
    LOGIN_SLOT_RELEASED = "LOGIN_SLOT_RELEASED"
    BOT_STARTED = "BOT_STARTED"
    BOT_STOP_REQUESTED = "BOT_STOP_REQUESTED"
    BOT_WORKER_STOPPED = "BOT_WORKER_STOPPED"
    BOT_STOPPED = "BOT_STOPPED"
    SIGNAL_EXECUTOR_PREPARING = "SIGNAL_EXECUTOR_PREPARING"
    SIGNAL_EXECUTOR_READY = "SIGNAL_EXECUTOR_READY"
    SIGNAL_EXECUTOR_STOPPING = "SIGNAL_EXECUTOR_STOPPING"
    SIGNAL_EXECUTOR_STOPPED = "SIGNAL_EXECUTOR_STOPPED"
    BOT_LISTENING = "BOT_LISTENING"
    ORDER_SENT = "ORDER_SENT"
    ORDER_FILLED = "ORDER_FILLED"
    ORDER_REJECTED = "ORDER_REJECTED"
    POSITION_UPDATED = "POSITION_UPDATED"
    SLOT_DEGRADED = "SLOT_DEGRADED"
    SLOT_BROKEN = "SLOT_BROKEN"
    RUNTIME_LOG = "RUNTIME_LOG"
    SLOT_STATE_CHANGED = "SLOT_STATE_CHANGED"
    SLOT_TERMINAL_KILL_BEGIN = "SLOT_TERMINAL_KILL_BEGIN"
    SLOT_TERMINAL_KILL_DONE = "SLOT_TERMINAL_KILL_DONE"
    COMMAND_REJECTED = "COMMAND_REJECTED"


class RunnerSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class RunnerEvent(BaseModel):
    event_id: str = Field(min_length=1)
    event_type: RunnerEventType
    account_id: Optional[int] = Field(default=None, ge=1)
    deployment_id: Optional[int] = Field(default=None, ge=1)
    bot_id: Optional[str] = None
    runner_id: str = Field(min_length=1)
    slot_id: Optional[str] = None
    severity: RunnerSeverity = RunnerSeverity.INFO
    payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: Optional[str] = None
    command_id: Optional[str] = None
    created_at: Optional[str] = None

    @field_validator("event_type", mode="before")
    @classmethod
    def normalize_event_type(cls, value: Any) -> Any:
        raw = str(getattr(value, "value", value) or "").strip()
        normalized = raw.replace("-", "_").upper()
        return normalized if normalized in RunnerEventType._value2member_map_ else value
