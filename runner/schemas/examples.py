from __future__ import annotations

from runner.schemas.commands import RunnerCommand
from runner.schemas.events import RunnerEvent


START_BOT_COMMAND = RunnerCommand.model_validate(
    {
        "command_id": "cmd-start-001",
        "command_type": "START_BOT",
        "account_id": 101,
        "deployment_id": 9001,
        "bot_id": "ema_smc",
        "runner_id": "runner-win-01",
        "slot_id": "slot-01",
        "priority": 50,
        "payload": {
            "bot_name": "ema_smc",
            "bot_version": "1.0.0",
            "runtime_entry": "main.py",
            "profile_class": "normal",
            "config": {"risk": "low"},
        },
        "created_at": "2026-04-22T10:00:00Z",
        "trace_id": "trace-start-001",
    }
).model_dump(mode="json")

STOP_BOT_COMMAND = RunnerCommand.model_validate(
    {
        "command_id": "cmd-stop-001",
        "command_type": "STOP_BOT",
        "account_id": 101,
        "deployment_id": 9001,
        "bot_id": "ema_smc",
        "runner_id": "runner-win-01",
        "slot_id": "slot-01",
        "priority": 100,
        "payload": {"reason": "user_stop_request"},
        "created_at": "2026-04-22T10:05:00Z",
        "trace_id": "trace-stop-001",
    }
).model_dump(mode="json")

PLACE_ORDER_COMMAND = RunnerCommand.model_validate(
    {
        "command_id": "cmd-place-001",
        "command_type": "PLACE_ORDER",
        "account_id": 101,
        "deployment_id": 9001,
        "bot_id": "ema_smc",
        "runner_id": "runner-win-01",
        "slot_id": "slot-01",
        "priority": 80,
        "payload": {
            "symbol": "XAUUSD",
            "side": "buy",
            "volume": 0.1,
            "entry_type": "market",
            "take_profit": 2450.0,
            "stop_loss": 2380.0,
        },
        "created_at": "2026-04-22T10:06:00Z",
        "trace_id": "trace-place-001",
    }
).model_dump(mode="json")

PLACE_LIMIT_ORDER_COMMAND = RunnerCommand.model_validate(
    {
        "command_id": "cmd-place-limit-001",
        "command_type": "PLACE_ORDER",
        "account_id": 101,
        "deployment_id": 9001,
        "bot_id": "gsalgovip",
        "runner_id": "runner-win-01",
        "slot_id": "slot-01",
        "priority": 80,
        "payload": {
            "request": {
                "symbol": "XAUUSD",
                "side": "buy",
                "volume": 0.1,
                "entry_type": "limit",
                "order_type": "BUY_LIMIT",
                "pending_order": True,
                "limit_price": 2397.5,
                "entry_price": 2397.5,
                "take_profit": 2410.0,
                "stop_loss": 2395.0,
            },
            "signal_role": "DCA",
        },
        "created_at": "2026-04-22T10:06:10Z",
        "trace_id": "trace-place-limit-001",
    }
).model_dump(mode="json")

MODIFY_ORDER_COMMAND = RunnerCommand.model_validate(
    {
        "command_id": "cmd-modify-001",
        "command_type": "MODIFY_ORDER",
        "account_id": 101,
        "deployment_id": 9001,
        "bot_id": "ema_smc",
        "runner_id": "runner-win-01",
        "slot_id": "slot-01",
        "priority": 70,
        "payload": {"position_key": "pos-1", "take_profit": 2460.0, "stop_loss": 2390.0},
        "created_at": "2026-04-22T10:07:00Z",
        "trace_id": "trace-modify-001",
    }
).model_dump(mode="json")

CLOSE_ORDER_COMMAND = RunnerCommand.model_validate(
    {
        "command_id": "cmd-close-001",
        "command_type": "CLOSE_ORDER",
        "account_id": 101,
        "deployment_id": 9001,
        "bot_id": "ema_smc",
        "runner_id": "runner-win-01",
        "slot_id": "slot-01",
        "priority": 85,
        "payload": {"position_key": "pos-1", "reason": "risk_reduce"},
        "created_at": "2026-04-22T10:08:00Z",
        "trace_id": "trace-close-001",
    }
).model_dump(mode="json")

SYNC_STATE_COMMAND = RunnerCommand.model_validate(
    {
        "command_id": "cmd-sync-001",
        "command_type": "SYNC_STATE",
        "account_id": 101,
        "deployment_id": 9001,
        "bot_id": "ema_smc",
        "runner_id": "runner-win-01",
        "slot_id": "slot-01",
        "priority": 40,
        "payload": {"reason": "manual_resync"},
        "created_at": "2026-04-22T10:09:00Z",
        "trace_id": "trace-sync-001",
    }
).model_dump(mode="json")

HEARTBEAT_EVENT = RunnerEvent.model_validate(
    {
        "event_id": "evt-heartbeat-001",
        "event_type": "HEARTBEAT",
        "account_id": 101,
        "deployment_id": 9001,
        "bot_id": "ema_smc",
        "runner_id": "runner-win-01",
        "slot_id": "slot-01",
        "severity": "info",
        "payload": {"connection_status": "connected", "pnl": 12.5, "balance": 1000.0},
        "trace_id": "trace-start-001",
        "created_at": "2026-04-22T10:00:05Z",
    }
).model_dump(mode="json")

BOT_STARTED_EVENT = RunnerEvent.model_validate(
    {
        "event_id": "evt-started-001",
        "event_type": "BOT_STARTED",
        "account_id": 101,
        "deployment_id": 9001,
        "bot_id": "ema_smc",
        "runner_id": "runner-win-01",
        "slot_id": "slot-01",
        "severity": "info",
        "payload": {"message": "worker_started"},
        "trace_id": "trace-start-001",
        "command_id": "cmd-start-001",
        "created_at": "2026-04-22T10:00:06Z",
    }
).model_dump(mode="json")

BOT_STOPPED_EVENT = RunnerEvent.model_validate(
    {
        "event_id": "evt-stopped-001",
        "event_type": "BOT_STOPPED",
        "account_id": 101,
        "deployment_id": 9001,
        "bot_id": "ema_smc",
        "runner_id": "runner-win-01",
        "slot_id": "slot-01",
        "severity": "info",
        "payload": {"message": "worker_stopped"},
        "trace_id": "trace-stop-001",
        "command_id": "cmd-stop-001",
        "created_at": "2026-04-22T10:05:04Z",
    }
).model_dump(mode="json")

ORDER_SENT_EVENT = RunnerEvent.model_validate(
    {
        "event_id": "evt-order-sent-001",
        "event_type": "ORDER_SENT",
        "account_id": 101,
        "deployment_id": 9001,
        "bot_id": "ema_smc",
        "runner_id": "runner-win-01",
        "slot_id": "slot-01",
        "severity": "info",
        "payload": {"position_key": "pos-1", "symbol": "XAUUSD", "side": "buy", "volume": 0.1},
        "trace_id": "trace-place-001",
        "command_id": "cmd-place-001",
        "created_at": "2026-04-22T10:06:01Z",
    }
).model_dump(mode="json")

ORDER_FILLED_EVENT = RunnerEvent.model_validate(
    {
        "event_id": "evt-order-filled-001",
        "event_type": "ORDER_FILLED",
        "account_id": 101,
        "deployment_id": 9001,
        "bot_id": "ema_smc",
        "runner_id": "runner-win-01",
        "slot_id": "slot-01",
        "severity": "info",
        "payload": {
            "positions": [
                {
                    "position_key": "pos-1",
                    "symbol": "XAUUSD",
                    "side": "buy",
                    "volume": 0.1,
                    "entry_price": 2400.0,
                    "mark_price": 2402.0,
                    "pnl": 2.0,
                }
            ]
        },
        "trace_id": "trace-place-001",
        "command_id": "cmd-place-001",
        "created_at": "2026-04-22T10:06:03Z",
    }
).model_dump(mode="json")

ORDER_REJECTED_EVENT = RunnerEvent.model_validate(
    {
        "event_id": "evt-order-rejected-001",
        "event_type": "ORDER_REJECTED",
        "account_id": 101,
        "deployment_id": 9001,
        "bot_id": "ema_smc",
        "runner_id": "runner-win-01",
        "slot_id": "slot-01",
        "severity": "error",
        "payload": {"reason": "volume_invalid"},
        "trace_id": "trace-place-001",
        "command_id": "cmd-place-001",
        "created_at": "2026-04-22T10:06:04Z",
    }
).model_dump(mode="json")

POSITION_UPDATED_EVENT = RunnerEvent.model_validate(
    {
        "event_id": "evt-position-001",
        "event_type": "POSITION_UPDATED",
        "account_id": 101,
        "deployment_id": 9001,
        "bot_id": "ema_smc",
        "runner_id": "runner-win-01",
        "slot_id": "slot-01",
        "severity": "info",
        "payload": {
            "positions": [
                {
                    "position_key": "pos-1",
                    "symbol": "XAUUSD",
                    "side": "buy",
                    "volume": 0.1,
                    "entry_price": 2400.0,
                    "mark_price": 2410.0,
                    "pnl": 10.0,
                }
            ]
        },
        "trace_id": "trace-sync-001",
        "created_at": "2026-04-22T10:09:03Z",
    }
).model_dump(mode="json")

SLOT_DEGRADED_EVENT = RunnerEvent.model_validate(
    {
        "event_id": "evt-slot-degraded-001",
        "event_type": "SLOT_DEGRADED",
        "account_id": 101,
        "deployment_id": 9001,
        "bot_id": "ema_smc",
        "runner_id": "runner-win-01",
        "slot_id": "slot-01",
        "severity": "warning",
        "payload": {"reason": "cpu_pressure"},
        "trace_id": "trace-start-001",
        "created_at": "2026-04-22T10:10:00Z",
    }
).model_dump(mode="json")

SLOT_BROKEN_EVENT = RunnerEvent.model_validate(
    {
        "event_id": "evt-slot-broken-001",
        "event_type": "SLOT_BROKEN",
        "account_id": 101,
        "deployment_id": 9001,
        "bot_id": "ema_smc",
        "runner_id": "runner-win-01",
        "slot_id": "slot-01",
        "severity": "error",
        "payload": {"reason": "mt5_adapter_crashed"},
        "trace_id": "trace-start-001",
        "created_at": "2026-04-22T10:10:10Z",
    }
).model_dump(mode="json")

RUNTIME_LOG_EVENT = RunnerEvent.model_validate(
    {
        "event_id": "evt-log-001",
        "event_type": "RUNTIME_LOG",
        "account_id": 101,
        "deployment_id": 9001,
        "bot_id": "ema_smc",
        "runner_id": "runner-win-01",
        "slot_id": "slot-01",
        "severity": "warning",
        "payload": {"level": "warning", "message": "margin pressure"},
        "trace_id": "trace-start-001",
        "created_at": "2026-04-22T10:11:00Z",
    }
).model_dump(mode="json")

SLOT_STATE_CHANGED_EVENT = RunnerEvent.model_validate(
    {
        "event_id": "evt-slot-state-001",
        "event_type": "SLOT_STATE_CHANGED",
        "account_id": 101,
        "deployment_id": 9001,
        "bot_id": "ema_smc",
        "runner_id": "runner-win-01",
        "slot_id": "slot-01",
        "severity": "info",
        "payload": {"new_state": "allocated", "previous_state": "ready"},
        "trace_id": "trace-start-001",
        "created_at": "2026-04-22T10:00:04Z",
    }
).model_dump(mode="json")

COMMAND_REJECTED_EVENT = RunnerEvent.model_validate(
    {
        "event_id": "evt-command-rejected-001",
        "event_type": "COMMAND_REJECTED",
        "account_id": 101,
        "deployment_id": 9001,
        "bot_id": "ema_smc",
        "runner_id": "runner-win-01",
        "slot_id": "slot-01",
        "severity": "error",
        "payload": {"reason": "command_validation_failed"},
        "trace_id": "trace-place-001",
        "command_id": "cmd-place-001",
        "created_at": "2026-04-22T10:06:02Z",
    }
).model_dump(mode="json")

EXAMPLE_COMMANDS = {
    "START_BOT": START_BOT_COMMAND,
    "STOP_BOT": STOP_BOT_COMMAND,
    "PLACE_ORDER": PLACE_ORDER_COMMAND,
    "PLACE_LIMIT_ORDER": PLACE_LIMIT_ORDER_COMMAND,
    "MODIFY_ORDER": MODIFY_ORDER_COMMAND,
    "CLOSE_ORDER": CLOSE_ORDER_COMMAND,
    "SYNC_STATE": SYNC_STATE_COMMAND,
}

EXAMPLE_EVENTS = {
    "HEARTBEAT": HEARTBEAT_EVENT,
    "BOT_STARTED": BOT_STARTED_EVENT,
    "BOT_STOPPED": BOT_STOPPED_EVENT,
    "ORDER_SENT": ORDER_SENT_EVENT,
    "ORDER_FILLED": ORDER_FILLED_EVENT,
    "ORDER_REJECTED": ORDER_REJECTED_EVENT,
    "POSITION_UPDATED": POSITION_UPDATED_EVENT,
    "SLOT_DEGRADED": SLOT_DEGRADED_EVENT,
    "SLOT_BROKEN": SLOT_BROKEN_EVENT,
    "RUNTIME_LOG": RUNTIME_LOG_EVENT,
    "SLOT_STATE_CHANGED": SLOT_STATE_CHANGED_EVENT,
    "COMMAND_REJECTED": COMMAND_REJECTED_EVENT,
}
