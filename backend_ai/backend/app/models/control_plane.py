from __future__ import annotations

from enum import Enum


class ProfileClass(str, Enum):
    LIGHT = "light"
    NORMAL = "normal"
    HEAVY = "heavy"


class BrokerAccountStatus(str, Enum):
    PENDING_VERIFICATION = "pending_verification"
    CONNECTED = "connected"
    VERIFICATION_FAILED = "verification_failed"
    DISCONNECTED = "disconnected"


class DeploymentStatus(str, Enum):
    DRAFT = "draft"
    START_REQUESTED = "start_requested"
    STARTING = "starting"
    RUNNING = "running"
    STOP_REQUESTED = "stop_requested"
    STOPPED = "stopped"
    FAILED = "failed"
    BLOCKED = "blocked"
    QUEUED = "queued"


class RunnerStatus(str, Enum):
    ONLINE = "online"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    DRAINING = "draining"
    MAINTENANCE = "maintenance"


class SlotStatus(str, Enum):
    READY = "ready"
    ALLOCATED = "allocated"
    DEGRADED = "degraded"
    BROKEN = "broken"
    DISABLED = "disabled"


class CommandType(str, Enum):
    START_BOT = "START_BOT"
    STOP_BOT = "STOP_BOT"
    UPDATE_BOT_CONFIG = "UPDATE_BOT_CONFIG"
    PLACE_ORDER = "PLACE_ORDER"
    MODIFY_ORDER = "MODIFY_ORDER"
    CLOSE_ORDER = "CLOSE_ORDER"
    SYNC_STATE = "SYNC_STATE"


class EventType(str, Enum):
    HEARTBEAT = "HEARTBEAT"
    BOT_STARTED = "BOT_STARTED"
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


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


ACTIVE_DEPLOYMENT_STATUSES = {
    DeploymentStatus.START_REQUESTED.value,
    DeploymentStatus.STARTING.value,
    DeploymentStatus.RUNNING.value,
    DeploymentStatus.STOP_REQUESTED.value,
}

RUNNER_HEALTHY_STATUSES = {
    RunnerStatus.ONLINE.value,
}

SLOT_USABLE_STATUSES = {
    SlotStatus.READY.value,
    SlotStatus.ALLOCATED.value,
}
