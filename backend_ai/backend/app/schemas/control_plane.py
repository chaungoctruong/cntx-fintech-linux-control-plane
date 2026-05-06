from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.control_plane import (
    BrokerAccountStatus,
    CommandType,
    EventType,
    ProfileClass,
    RunnerStatus,
    Severity,
    SlotStatus,
)


def _merge_trading_fields(
    config: dict[str, Any],
    *,
    lot_size: Optional[float],
    stop_loss: Optional[float],
    take_profit: Optional[float],
    trading_unit: Optional[str],
    dca_enabled: Any = None,
    dca_enabled_provided: bool = False,
) -> dict[str, Any]:
    merged = dict(config or {})
    trading = dict(merged.get("trading") or {}) if isinstance(merged.get("trading"), dict) else {}
    if lot_size is not None:
        trading["lot_size"] = lot_size
    if stop_loss is not None:
        trading["stop_loss"] = stop_loss
    if take_profit is not None:
        trading["take_profit"] = take_profit
    if trading_unit is not None:
        trading["trading_unit"] = trading_unit
    if dca_enabled_provided:
        trading["dca_enabled"] = dca_enabled
    if trading:
        merged["trading"] = trading
    return merged


def _field_was_set(model: BaseModel, field_name: str) -> bool:
    return field_name in getattr(model, "model_fields_set", set())


class AccountConnectRequest(BaseModel):
    broker: str = Field(min_length=1)
    server: str = Field(min_length=1)
    login: str = Field(min_length=1)
    password: str = Field(min_length=1)
    label: Optional[str] = None


class AccountVerifyRequest(BaseModel):
    account_id: int


class AccountVerificationResultRequest(BaseModel):
    job_id: int
    ok: bool
    error_text: Optional[str] = None
    runner_id: Optional[str] = None
    slot_id: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)


class CommandDeliveryUpdateRequest(BaseModel):
    runner_id: Optional[str] = None
    slot_id: Optional[str] = None
    delivery_status: str = Field(min_length=1)
    error_text: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)


class RunnerCommandClaimRequest(BaseModel):
    runner_id: str = Field(min_length=1)
    slot_id: Optional[str] = None
    command_types: list[CommandType] = Field(
        default_factory=lambda: [
            CommandType.STOP_BOT,
            CommandType.START_BOT,
            CommandType.UPDATE_BOT_CONFIG,
        ]
    )
    wait_timeout_sec: int = Field(default=0, ge=0, le=30)


class BotSelectRequest(BaseModel):
    account_id: int
    bot_name: str = Field(min_length=1)
    bot_config_overrides: dict[str, Any] = Field(default_factory=dict)
    lot_size: Optional[float] = Field(default=None, gt=0)
    stop_loss: Optional[float] = Field(default=None, gt=0)
    take_profit: Optional[float] = Field(default=None, gt=0)
    trading_unit: Optional[Literal["price_distance", "points"]] = None
    dca_enabled: Any = None

    def merged_bot_config_overrides(self) -> dict[str, Any]:
        return _merge_trading_fields(
            self.bot_config_overrides,
            lot_size=self.lot_size,
            stop_loss=self.stop_loss,
            take_profit=self.take_profit,
            trading_unit=self.trading_unit,
            dca_enabled=self.dca_enabled,
            dca_enabled_provided=_field_was_set(self, "dca_enabled"),
        )


class DeploymentStartRequest(BaseModel):
    account_id: int
    bot_name: str = Field(min_length=1)
    bot_config_overrides: dict[str, Any] = Field(default_factory=dict)
    mode: Literal["live", "paper"] = "live"
    entitlement_id: Optional[str] = None
    lot_size: Optional[float] = Field(default=None, gt=0)
    stop_loss: Optional[float] = Field(default=None, gt=0)
    take_profit: Optional[float] = Field(default=None, gt=0)
    trading_unit: Optional[Literal["price_distance", "points"]] = None
    dca_enabled: Any = None

    def merged_bot_config_overrides(self) -> dict[str, Any]:
        return _merge_trading_fields(
            self.bot_config_overrides,
            lot_size=self.lot_size,
            stop_loss=self.stop_loss,
            take_profit=self.take_profit,
            trading_unit=self.trading_unit,
            dca_enabled=self.dca_enabled,
            dca_enabled_provided=_field_was_set(self, "dca_enabled"),
        )


class DeploymentConfigUpdateRequest(BaseModel):
    bot_config_overrides: dict[str, Any] = Field(default_factory=dict)
    lot_size: Optional[float] = Field(default=None, gt=0)
    stop_loss: Optional[float] = Field(default=None, gt=0)
    take_profit: Optional[float] = Field(default=None, gt=0)
    trading_unit: Optional[Literal["price_distance", "points"]] = None
    dca_enabled: Any = None

    def merged_bot_config_overrides(self) -> dict[str, Any]:
        return _merge_trading_fields(
            self.bot_config_overrides,
            lot_size=self.lot_size,
            stop_loss=self.stop_loss,
            take_profit=self.take_profit,
            trading_unit=self.trading_unit,
            dca_enabled=self.dca_enabled,
            dca_enabled_provided=_field_was_set(self, "dca_enabled"),
        )


class DeploymentStopRequest(BaseModel):
    deployment_id: int
    reason: Optional[str] = None


class DeploymentCommandRequest(BaseModel):
    command_type: CommandType
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(default=50, ge=0, le=1000)
    trace_id: Optional[str] = None
    command_id: Optional[str] = None


class RunnerSlotRegistration(BaseModel):
    slot_id: str = Field(min_length=1)
    status: SlotStatus = SlotStatus.READY
    allowed_profile_classes: list[ProfileClass] = Field(default_factory=lambda: [ProfileClass.LIGHT, ProfileClass.NORMAL, ProfileClass.HEAVY])
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunnerRegisterRequest(BaseModel):
    runner_id: str = Field(min_length=1)
    label: Optional[str] = None
    host: Optional[str] = None
    status: RunnerStatus = RunnerStatus.ONLINE
    supported_profiles: list[ProfileClass] = Field(default_factory=lambda: [ProfileClass.LIGHT, ProfileClass.NORMAL, ProfileClass.HEAVY])
    capability_tags: list[str] = Field(default_factory=list)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    available_bots: list[str] = Field(default_factory=list)
    available_bot_names: list[str] = Field(default_factory=list)
    bot_catalog: dict[str, Any] = Field(default_factory=dict)
    max_slots: int = Field(default=1, ge=1, le=512)
    slots: list[RunnerSlotRegistration] = Field(default_factory=list)


class RunnerHeartbeatRequest(BaseModel):
    runner_id: str = Field(min_length=1)
    slot_id: Optional[str] = None
    account_id: Optional[int] = None
    deployment_id: Optional[int] = None
    payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: Optional[str] = None


class RunnerDrainRequest(BaseModel):
    reason: Optional[str] = None
    actor: Optional[str] = None
    disable_ready_slots: bool = True


class RunnerResumeRequest(BaseModel):
    reason: Optional[str] = None
    actor: Optional[str] = None
    enable_disabled_slots: bool = True


class RunnerOrphanedHandoffRequest(BaseModel):
    reason: Optional[str] = None
    actor: Optional[str] = None
    confirmed_runtime_dead: bool = False


class RunnerEventRequest(BaseModel):
    event_id: Optional[str] = None
    event_type: EventType
    account_id: Optional[int] = None
    deployment_id: Optional[int] = None
    bot_id: Optional[str] = None
    runner_id: str = Field(min_length=1)
    slot_id: Optional[str] = None
    severity: Severity = Severity.INFO
    payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: Optional[str] = None
    command_id: Optional[str] = None
    created_at: Optional[str] = None
    message: Optional[str] = None
    log_message: Optional[str] = None
    phase: Optional[str] = None
    event_at: Optional[str] = None
    timestamp: Optional[str] = None
    callback_http_ms: Optional[float] = None
    callback_elapsed_ms: Optional[float] = None
    http_elapsed_ms: Optional[float] = None
    elapsed_ms: Optional[float] = None

    @field_validator("event_type", mode="before")
    @classmethod
    def normalize_event_type(cls, value: Any) -> Any:
        raw = str(getattr(value, "value", value) or "").strip()
        normalized = raw.replace("-", "_").upper()
        return normalized if normalized in EventType._value2member_map_ else value


class GsAlgoBotStateContext(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    runner_id: str = Field(min_length=1)
    slot_id: str = Field(min_length=1)
    account_id: Union[str, int]
    deployment_id: Union[str, int]
    bot_id: str = Field(default="gsalgo_mt5_bot", min_length=1)
    schema_name: Optional[str] = Field(default=None, alias="schema")


class GsAlgoBotStateRequest(BaseModel):
    operation: str = Field(min_length=1)
    context: GsAlgoBotStateContext
    payload: dict[str, Any] = Field(default_factory=dict)
