from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


class RunnerCommandType(str, Enum):
    START_BOT = "START_BOT"
    STOP_BOT = "STOP_BOT"
    UPDATE_BOT_CONFIG = "UPDATE_BOT_CONFIG"
    PLACE_ORDER = "PLACE_ORDER"
    MODIFY_ORDER = "MODIFY_ORDER"
    CLOSE_ORDER = "CLOSE_ORDER"
    SYNC_STATE = "SYNC_STATE"


class RunnerCommand(BaseModel):
    command_id: str = Field(min_length=1)
    command_type: RunnerCommandType
    cmd_type: str = Field(default="", min_length=0)
    requested_cmd_type: str = Field(default="", min_length=0)
    account_id: int = Field(ge=1)
    profile_id: Optional[int] = Field(default=None, ge=1)
    deployment_id: int = Field(ge=1)
    bot_id: str = Field(min_length=1)
    runner_id: str = Field(min_length=1)
    slot_id: str = Field(min_length=1)
    priority: int = Field(default=50, ge=0, le=1000)
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def fill_windows_runner_aliases(self) -> "RunnerCommand":
        requested = self.command_type.value.lower()
        self.cmd_type = requested
        self.requested_cmd_type = requested
        if self.profile_id is None:
            self.profile_id = self.account_id
        return self
