from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    token: str = Field(..., min_length=20, description="JWT cấp bởi đối tác")


class PartnerUserContext(BaseModel):
    """Snapshot của khách sau khi verify JWT + Redis state."""

    jti: str
    partner_id: str
    account_id: int | None  # None nếu khách chưa link MT5 account
    bot_id: str
    end_user_label: str | None
    issued_at: datetime
    expires_at: datetime
    state: str  # valid | revoked | locked
    remaining_seconds: int


class LinkAccountRequest(BaseModel):
    account_id: int = Field(..., gt=0, description="MT5 account_id khách muốn link")


class LinkAccountResponse(BaseModel):
    ok: bool
    jti: str
    account_id: int
    mt5_login: str | None = None
    broker: str | None = None
    server: str | None = None
    linked_at: datetime
    note: str | None = None


class LoginResponse(BaseModel):
    ok: bool
    me: PartnerUserContext


class BotStatus(BaseModel):
    account_id: int
    bot_id: str
    deployment_id: int | None
    status: str | None  # running | stopped | failed | …
    runner_id: str | None = None
    started_at: datetime | None = None
    last_event_at: datetime | None = None


class BotInfoResponse(BaseModel):
    me: PartnerUserContext
    bot: BotStatus


class BotActionRequest(BaseModel):
    lot_size: float | None = Field(default=None, ge=0.01, le=100.0)
    config_overrides: dict[str, Any] | None = None


class BotActionResponse(BaseModel):
    ok: bool
    action: str  # "start" | "stop" | "noop"
    deployment: dict[str, Any] | None = None
    bot: BotStatus
    note: str | None = None
