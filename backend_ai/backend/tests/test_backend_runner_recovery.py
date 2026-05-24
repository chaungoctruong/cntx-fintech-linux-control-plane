from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.events import runner_event_ingest as runner_ingest_module
from app.events.runner_event_ingest import RunnerEventIngestService
from app.models.control_plane import CommandType, EventType


runner_ingest_module.schedule_error_alert = lambda **kwargs: None


class FakePublisher:
    async def publish_event(self, payload: dict[str, Any]) -> str:
        return "event-stream-id"


class FakeCommandRouter:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def dispatch(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        runner_id = kwargs["runner_id"]
        return {
            "command_id": f"cmd-{len(self.calls)}",
            "trace_id": kwargs["trace_id"],
            "runner_id": runner_id,
            "slot_id": kwargs["slot_id"],
            "delivery_status": "queued",
            "queue_name": f"mt5:runner:{runner_id}:commands",
        }


class FakeRepo:
    def __init__(self) -> None:
        self.deployment: dict[str, Any] = {
            "id": 889,
            "user_id": 10,
            "account_id": 132,
            "bot_code": "gsalgovip",
            "bot_name": "GsAlgo",
            "profile_class": "normal",
            "mode": "live",
            "status": "running",
            "desired_state": "running",
            "is_active": True,
            "runner_id": "runner-win-01",
            "slot_id": "slot-02",
            "broker": "exness",
            "server": "Exness-MT5Trial17",
            "login": "463422165",
            "config_json": {"trading": {"lot_size": 0.01}},
            "intent_seq": 7,
            "health_status": "running",
        }
        self.claim_action = "claim"
        self.claims: list[dict[str, Any]] = []
        self.status_updates: list[dict[str, Any]] = []
        self.slot_updates: list[dict[str, Any]] = []
        self.commands_marked: list[dict[str, Any]] = []
        self.dispatch_failures: list[dict[str, Any]] = []
        self.clears: list[dict[str, Any]] = []
        self.audits: list[dict[str, Any]] = []
        self.runtime_logs: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.account_login_results: list[dict[str, Any]] = []
        self.command_deliveries: list[dict[str, Any]] = []
        self.execution_commands: dict[str, dict[str, Any]] = {}
        self.slots: list[dict[str, Any]] = [
            {
                "runner_id": "runner-win-01",
                "slot_id": "slot-02",
                "status": "allocated",
                "runner_status": "online",
                "current_account_id": 132,
                "active_deployment_id": 889,
                "metadata_json": {"current_runner_state": "active", "mt5_liveness_state": "healthy"},
            }
        ]
        self.allocated_slots: list[dict[str, Any]] = []
        self.claim_actions: list[str] = []
        self.fail_allocate_slots: set[str] = set()

    def touch_runner_heartbeat(self, **kwargs: Any) -> None:
        return None

    def touch_deployment_heartbeat(self, **kwargs: Any) -> None:
        return None

    def insert_execution_event(self, **kwargs: Any) -> dict[str, Any]:
        self.events.append(kwargs)
        return kwargs

    def upsert_execution_audit(self, **kwargs: Any) -> None:
        return None

    def insert_runtime_log(self, **kwargs: Any) -> None:
        self.runtime_logs.append(kwargs)

    def update_runner_slot_state(self, **kwargs: Any) -> None:
        self.slot_updates.append(kwargs)

    def update_deployment_status(self, **kwargs: Any) -> dict[str, Any]:
        self.status_updates.append(kwargs)
        self.deployment.update(
            {
                key: value
                for key, value in kwargs.items()
                if key in {"status", "desired_state", "is_active", "health_status", "last_error", "runner_id", "slot_id"}
            }
        )
        return dict(self.deployment)

    def get_deployment(self, *, deployment_id: int, **kwargs: Any) -> dict[str, Any] | None:
        return dict(self.deployment) if int(deployment_id) == int(self.deployment["id"]) else None

    def get_bot_by_name(self, *, bot_name: str) -> dict[str, Any]:
        return {
            "bot_code": "gsalgovip",
            "bot_name": "GsAlgo",
            "profile_class": "normal",
            "resource_hints": {},
        }

    def claim_backend_runner_recovery(self, **kwargs: Any) -> dict[str, Any]:
        self.claims.append(kwargs)
        claim_action = self.claim_actions.pop(0) if self.claim_actions else self.claim_action
        return {
            "action": claim_action,
            "deployment_id": kwargs["deployment_id"],
            "account_id": kwargs["account_id"],
            "status": self.deployment["status"],
            "desired_state": self.deployment["desired_state"],
            "health_status": self.deployment.get("health_status"),
            "attempt_count": 1,
            "cooldown_until": "2026-05-22T15:02:00+07:00",
        }

    def mark_backend_runner_recovery_command(self, **kwargs: Any) -> None:
        self.commands_marked.append(kwargs)

    def mark_backend_runner_recovery_dispatch_failed(self, **kwargs: Any) -> None:
        self.dispatch_failures.append(kwargs)

    def clear_backend_runner_recovery(self, **kwargs: Any) -> dict[str, Any] | None:
        self.clears.append(kwargs)
        return {"id": kwargs["deployment_id"], "cleared": True}

    def insert_deployment_audit(self, **kwargs: Any) -> None:
        self.audits.append(kwargs)

    def mark_account_runtime_login_result(self, **kwargs: Any) -> None:
        self.account_login_results.append(kwargs)

    def update_execution_command_delivery(self, **kwargs: Any) -> None:
        self.command_deliveries.append(kwargs)

    def get_execution_command(self, **kwargs: Any) -> dict[str, Any] | None:
        command_id = str(kwargs.get("command_id") or "")
        return self.execution_commands.get(command_id)

    def list_slots(self) -> list[dict[str, Any]]:
        return [dict(slot) for slot in self.slots]

    def allocate_slot_binding(self, **kwargs: Any) -> dict[str, Any]:
        if str(kwargs.get("slot_id") or "") in self.fail_allocate_slots:
            raise ValueError("no_available_unreserved_slot")
        self.allocated_slots.append(kwargs)
        return {
            "id": len(self.allocated_slots),
            "account_id": kwargs["account_id"],
            "runner_id": kwargs["runner_id"],
            "slot_id": kwargs["slot_id"],
            "binding_state": "active",
            "is_current": True,
        }


def _service(repo: FakeRepo) -> tuple[RunnerEventIngestService, FakeCommandRouter]:
    router = FakeCommandRouter()
    service = RunnerEventIngestService(repo)  # type: ignore[arg-type]
    service._publisher = FakePublisher()  # type: ignore[assignment]
    service._command_router = router  # type: ignore[assignment]
    service._heartbeat_write_throttle_sec = 0
    return service, router


def _recovery_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "status": "mt5_recovery_deferred_to_backend",
        "reason": "mt5_terminal_not_running",
        "auto_recovery_enabled": False,
        "backend_restart_required": True,
        "report": {
            "state": "broken",
            "reason": "mt5_terminal_not_running",
            "slot_id": "slot-02",
            "storage_slot_id": "slot_02",
            "account_id": 132,
            "deployment_id": 889,
            "worker_alive": False,
            "terminal_running": False,
            "recycle": {"required": True, "scope": "deployment"},
        },
    }
    payload.update(overrides)
    return payload


async def _ingest_recovery_event(service: RunnerEventIngestService, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return await service.ingest_event(
        event_id="evt-recovery",
        event_type=EventType.RUNTIME_LOG.value,
        account_id=132,
        deployment_id=889,
        bot_id="gsalgovip",
        runner_id="runner-win-01",
        slot_id="slot_02",
        severity="warning",
        payload=payload or _recovery_payload(),
        trace_id="trace-recovery",
    )


async def _ingest_recovery_event_with_id(
    service: RunnerEventIngestService,
    *,
    event_id: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await service.ingest_event(
        event_id=event_id,
        event_type=EventType.RUNTIME_LOG.value,
        account_id=132,
        deployment_id=889,
        bot_id="gsalgovip",
        runner_id="runner-win-01",
        slot_id="slot_02",
        severity="warning",
        payload=payload or _recovery_payload(),
        trace_id="trace-recovery",
    )


class BackendRunnerRecoveryTests(unittest.TestCase):
    def test_recovery_event_for_running_deployment_enqueues_start_bot(self) -> None:
        repo = FakeRepo()
        service, router = _service(repo)

        asyncio.run(_ingest_recovery_event(service))

        self.assertEqual(len(router.calls), 1)
        call = router.calls[0]
        self.assertEqual(call["command_type"], CommandType.START_BOT)
        self.assertEqual(call["runner_id"], "runner-win-01")
        self.assertEqual(call["slot_id"], "slot-02")
        self.assertEqual(call["payload"]["control_flow"], "backend_runner_recovery")
        self.assertEqual(call["payload"]["intent_seq"], 7)
        self.assertEqual(repo.commands_marked[0]["command_id"], "cmd-1")
        self.assertIn("mt5:runner:runner-win-01:commands", f"mt5:runner:{call['runner_id']}:commands")

    def test_slot_broken_recovery_reason_enqueues_before_failing_deployment(self) -> None:
        repo = FakeRepo()
        service, router = _service(repo)

        asyncio.run(
            service.ingest_event(
                event_id="evt-slot-broken",
                event_type=EventType.SLOT_BROKEN.value,
                account_id=132,
                deployment_id=889,
                bot_id="gsalgovip",
                runner_id="runner-win-01",
                slot_id="slot_02",
                severity="warning",
                payload={
                    "reason": "mt5_terminal_not_running",
                    "current_state": "BROKEN",
                    "previous_state": "ACTIVE",
                    "current_runner_state": "BROKEN",
                    "previous_runner_state": "ACTIVE",
                    "current_control_plane_state": "broken",
                    "previous_control_plane_state": "allocated",
                },
                trace_id="trace-slot-broken",
            )
        )

        self.assertEqual(len(router.calls), 1)
        self.assertEqual(router.calls[0]["command_type"], CommandType.START_BOT)
        self.assertEqual(router.calls[0]["payload"]["backend_recovery_reason"], "mt5_terminal_not_running")
        self.assertFalse(
            any(update.get("desired_state") == "stopped" for update in repo.status_updates)
        )

    def test_recovery_reassigns_broken_slot_to_ready_slot_before_start(self) -> None:
        repo = FakeRepo()
        repo.slots = [
            {
                "runner_id": "runner-win-01",
                "slot_id": "slot-02",
                "status": "broken",
                "runner_status": "online",
                "current_account_id": None,
                "active_deployment_id": 889,
                "metadata_json": {"current_runner_state": "broken", "mt5_liveness_state": "broken"},
            },
            {
                "runner_id": "runner-win-01",
                "slot_id": "slot-03",
                "status": "ready",
                "runner_status": "online",
                "current_account_id": None,
                "active_deployment_id": None,
                "metadata_json": {"current_runner_state": "ready"},
            },
        ]
        service, router = _service(repo)

        asyncio.run(_ingest_recovery_event(service))

        self.assertEqual(len(router.calls), 1)
        self.assertEqual(router.calls[0]["slot_id"], "slot-03")
        self.assertEqual(repo.allocated_slots[0]["slot_id"], "slot-03")
        self.assertEqual(repo.deployment["slot_id"], "slot-03")

    def test_slot_broken_event_reassigns_before_slot_inventory_catches_up(self) -> None:
        repo = FakeRepo()
        repo.slots = [
            {
                "runner_id": "runner-win-01",
                "slot_id": "slot-02",
                "status": "allocated",
                "runner_status": "online",
                "current_account_id": 132,
                "active_deployment_id": 889,
                "metadata_json": {"current_runner_state": "active", "mt5_liveness_state": "healthy"},
            },
            {
                "runner_id": "runner-win-01",
                "slot_id": "slot-03",
                "status": "ready",
                "runner_status": "online",
                "current_account_id": None,
                "active_deployment_id": None,
                "metadata_json": {"current_runner_state": "ready"},
            },
        ]
        service, router = _service(repo)

        asyncio.run(
            service.ingest_event(
                event_id="evt-slot-broken-race",
                event_type=EventType.SLOT_BROKEN.value,
                account_id=132,
                deployment_id=889,
                bot_id="gsalgovip",
                runner_id="runner-win-01",
                slot_id="slot_02",
                severity="warning",
                payload={
                    "reason": "worker_process_missing",
                    "current_state": "BROKEN",
                    "previous_state": "ACTIVE",
                    "current_runner_state": "BROKEN",
                    "previous_runner_state": "ACTIVE",
                    "current_control_plane_state": "broken",
                    "previous_control_plane_state": "allocated",
                },
                trace_id="trace-slot-broken-race",
            )
        )

        self.assertEqual(len(router.calls), 1)
        self.assertEqual(router.calls[0]["slot_id"], "slot-03")
        self.assertEqual(repo.allocated_slots[0]["slot_id"], "slot-03")
        self.assertEqual(repo.deployment["slot_id"], "slot-03")

    def test_budget_exhausted_can_reassign_to_ready_slot_and_retry_claim(self) -> None:
        repo = FakeRepo()
        repo.claim_actions = ["budget_exhausted", "claim"]
        repo.deployment["health_status"] = "recovery_failed"
        repo.slots = [
            {
                "runner_id": "runner-win-01",
                "slot_id": "slot-02",
                "status": "broken",
                "runner_status": "online",
                "current_account_id": 132,
                "active_deployment_id": 889,
                "metadata_json": {"current_runner_state": "broken", "mt5_liveness_state": "broken"},
            },
            {
                "runner_id": "runner-win-01",
                "slot_id": "slot-03",
                "status": "ready",
                "runner_status": "online",
                "current_account_id": None,
                "active_deployment_id": None,
                "metadata_json": {"current_runner_state": "ready"},
            },
        ]
        service, router = _service(repo)

        asyncio.run(_ingest_recovery_event(service))

        self.assertEqual(len(repo.claims), 2)
        self.assertEqual(repo.claims[1]["slot_id"], "slot-03")
        self.assertEqual(len(router.calls), 1)
        self.assertEqual(router.calls[0]["slot_id"], "slot-03")
        self.assertEqual(repo.clears[0]["reason"], "slot_reassigned_after_budget_exhausted")
        self.assertEqual(repo.deployment["slot_id"], "slot-03")

    def test_recovery_reassign_tries_next_ready_slot_when_first_candidate_fails(self) -> None:
        repo = FakeRepo()
        repo.fail_allocate_slots = {"slot-01"}
        repo.slots = [
            {
                "runner_id": "runner-win-01",
                "slot_id": "slot-02",
                "status": "broken",
                "runner_status": "online",
                "current_account_id": 132,
                "active_deployment_id": 889,
                "metadata_json": {"current_runner_state": "broken", "mt5_liveness_state": "broken"},
            },
            {
                "runner_id": "runner-win-01",
                "slot_id": "slot-01",
                "status": "ready",
                "runner_status": "online",
                "current_account_id": None,
                "active_deployment_id": None,
                "metadata_json": {"current_runner_state": "ready"},
            },
            {
                "runner_id": "runner-win-01",
                "slot_id": "slot-03",
                "status": "ready",
                "runner_status": "online",
                "current_account_id": None,
                "active_deployment_id": None,
                "metadata_json": {"current_runner_state": "ready"},
            },
        ]
        service, router = _service(repo)

        asyncio.run(_ingest_recovery_event(service))

        self.assertEqual(len(router.calls), 1)
        self.assertEqual(router.calls[0]["slot_id"], "slot-03")
        self.assertEqual([item["slot_id"] for item in repo.allocated_slots], ["slot-03"])
        self.assertEqual(repo.deployment["slot_id"], "slot-03")

    def test_backend_recovery_start_rejection_keeps_deployment_running(self) -> None:
        repo = FakeRepo()
        repo.execution_commands["cmd-recovery"] = {
            "command_id": "cmd-recovery",
            "command_type": "START_BOT",
            "account_id": 132,
            "deployment_id": 889,
            "payload_json": {
                "control_flow": "backend_runner_recovery",
                "backend_runner_recovery": True,
            },
        }
        service, router = _service(repo)

        asyncio.run(
            service.ingest_event(
                event_id="evt-recovery-rejected",
                event_type=EventType.COMMAND_REJECTED.value,
                account_id=132,
                deployment_id=889,
                bot_id="gsalgovip",
                runner_id="runner-win-01",
                slot_id="slot_02",
                command_id="cmd-recovery",
                severity="warning",
                payload={"reason": "slot_not_available:BROKEN"},
                trace_id="trace-recovery-rejected",
            )
        )

        self.assertEqual(router.calls, [])
        self.assertEqual(repo.dispatch_failures[0]["reason"], "command_rejected:slot_not_available:BROKEN")
        self.assertEqual(repo.deployment["desired_state"], "running")
        self.assertEqual(repo.deployment["status"], "running")
        self.assertTrue(repo.deployment["is_active"])
        self.assertEqual(repo.deployment["health_status"], "runner_recovery_pending")
        self.assertFalse(
            any(update.get("desired_state") == "stopped" for update in repo.status_updates)
        )

    def test_repeated_event_in_cooldown_does_not_enqueue_again(self) -> None:
        repo = FakeRepo()
        repo.claim_action = "cooldown"
        service, router = _service(repo)

        asyncio.run(_ingest_recovery_event(service))

        self.assertEqual(router.calls, [])
        self.assertEqual(repo.claims[0]["reason"], "mt5_terminal_not_running")

    def test_stopped_deployment_does_not_enqueue(self) -> None:
        repo = FakeRepo()
        repo.claim_action = "deployment_not_running"
        repo.deployment["status"] = "stopped"
        repo.deployment["desired_state"] = "stopped"
        service, router = _service(repo)

        asyncio.run(_ingest_recovery_event(service))

        self.assertEqual(router.calls, [])
        self.assertEqual(repo.commands_marked, [])

    def test_newer_active_deployment_does_not_enqueue_old_deployment(self) -> None:
        repo = FakeRepo()
        repo.claim_action = "newer_active_deployment"
        service, router = _service(repo)

        asyncio.run(_ingest_recovery_event(service))

        self.assertEqual(router.calls, [])
        self.assertEqual(repo.commands_marked, [])

    def test_recovery_in_flight_does_not_enqueue_duplicate(self) -> None:
        repo = FakeRepo()
        repo.claim_action = "recovery_in_flight"
        service, router = _service(repo)

        asyncio.run(_ingest_recovery_event(service))

        self.assertEqual(router.calls, [])

    def test_budget_exhausted_marks_failed_without_enqueue(self) -> None:
        repo = FakeRepo()
        repo.claim_action = "budget_exhausted"
        service, router = _service(repo)

        asyncio.run(_ingest_recovery_event(service))

        self.assertEqual(router.calls, [])
        self.assertEqual(repo.commands_marked, [])

    def test_heartbeat_healthy_clears_recovery_state(self) -> None:
        repo = FakeRepo()
        service, router = _service(repo)

        asyncio.run(
            service.ingest_heartbeat(
                runner_id="runner-win-01",
                slot_id="slot_02",
                account_id=132,
                deployment_id=889,
                payload={
                    "status": "running",
                    "slot_state": "allocated",
                    "terminal_running": True,
                    "worker_alive": True,
                },
                trace_id="trace-heartbeat",
            )
        )

        self.assertEqual(router.calls, [])
        self.assertEqual(repo.clears[0]["deployment_id"], 889)
        self.assertEqual(repo.clears[0]["slot_id"], "slot-02")

    def test_heartbeat_healthy_with_worker_pid_clears_recovery_state(self) -> None:
        repo = FakeRepo()
        service, router = _service(repo)

        asyncio.run(
            service.ingest_heartbeat(
                runner_id="runner-win-01",
                slot_id="slot_02",
                account_id=132,
                deployment_id=889,
                payload={
                    "state_sync": "ok",
                    "slot_state": "allocated",
                    "terminal_running": True,
                    "worker_pid": 8308,
                },
                trace_id="trace-heartbeat-worker-pid",
            )
        )

        self.assertEqual(router.calls, [])
        self.assertEqual(repo.clears[0]["deployment_id"], 889)
        self.assertEqual(repo.clears[0]["slot_id"], "slot-02")

    def test_slot_state_active_clears_recovery_state(self) -> None:
        repo = FakeRepo()
        service, router = _service(repo)

        asyncio.run(
            service.ingest_event(
                event_id="evt-slot-active",
                event_type=EventType.SLOT_STATE_CHANGED.value,
                account_id=132,
                deployment_id=889,
                bot_id="gsalgovip",
                runner_id="runner-win-01",
                slot_id="slot_02",
                severity="info",
                payload={
                    "current_state": "ACTIVE",
                    "current_runner_state": "ACTIVE",
                    "worker_pid": 8308,
                    "terminal_path": "C:\\cntx-labs\\engineer\\slots\\slot_02\\terminal64.exe",
                },
                trace_id="trace-slot-active",
            )
        )

        self.assertEqual(router.calls, [])
        self.assertEqual(repo.slot_updates[0]["status"], "allocated")
        self.assertEqual(repo.clears[0]["deployment_id"], 889)
        self.assertEqual(repo.clears[0]["slot_id"], "slot-02")

    def test_slot_state_preparing_does_not_clear_recovery_state(self) -> None:
        repo = FakeRepo()
        service, router = _service(repo)

        asyncio.run(
            service.ingest_event(
                event_id="evt-slot-preparing",
                event_type=EventType.SLOT_STATE_CHANGED.value,
                account_id=132,
                deployment_id=889,
                bot_id="gsalgovip",
                runner_id="runner-win-01",
                slot_id="slot_02",
                severity="info",
                payload={
                    "current_state": "PREPARING",
                    "current_control_plane_state": "allocated",
                    "worker_pid": 8308,
                    "terminal_path": "C:\\cntx-labs\\engineer\\slots\\slot_02\\terminal64.exe",
                },
                trace_id="trace-slot-preparing",
            )
        )

        self.assertEqual(router.calls, [])
        self.assertEqual(repo.slot_updates[0]["status"], "allocated")
        self.assertEqual(repo.clears, [])

    def test_heartbeat_without_explicit_worker_and_terminal_does_not_clear_recovery(self) -> None:
        repo = FakeRepo()
        service, router = _service(repo)

        asyncio.run(
            service.ingest_heartbeat(
                runner_id="runner-win-01",
                slot_id="slot_02",
                account_id=132,
                deployment_id=889,
                payload={
                    "status": "running",
                    "slot_state": "allocated",
                },
                trace_id="trace-heartbeat-ambiguous",
            )
        )

        self.assertEqual(router.calls, [])
        self.assertEqual(repo.clears, [])

    def test_heartbeat_broken_backend_controlled_enqueues_start_bot(self) -> None:
        repo = FakeRepo()
        service, router = _service(repo)

        asyncio.run(
            service.ingest_heartbeat(
                runner_id="runner-win-01",
                slot_id=None,
                account_id=None,
                deployment_id=None,
                payload={
                    "slot_inventory": [
                        {
                            "slot_id": "slot_02",
                            "state": "broken",
                            "account_id": 132,
                            "deployment_id": 889,
                            "metadata": {
                                "mt5_recovery_status": "backend_controlled",
                                "last_mt5_recovery_error": "auto_recovery_disabled",
                                "mt5_recovery_backend_required_reason": "worker_process_missing",
                            },
                            "backend_restart_required": True,
                        }
                    ]
                },
                trace_id="trace-heartbeat-broken",
            )
        )

        self.assertEqual(len(router.calls), 1)
        self.assertEqual(router.calls[0]["payload"]["backend_recovery_reason"], "worker_process_missing")

    def test_missing_identity_is_ignored_without_crash(self) -> None:
        repo = FakeRepo()
        service, router = _service(repo)
        payload = _recovery_payload(report={"state": "broken", "reason": "worker_process_missing"})

        asyncio.run(
            service.ingest_event(
                event_id="evt-missing",
                event_type=EventType.RUNTIME_LOG.value,
                account_id=None,
                deployment_id=None,
                bot_id="gsalgovip",
                runner_id="runner-win-01",
                slot_id="slot_02",
                severity="warning",
                payload=payload,
                trace_id="trace-missing",
            )
        )

        self.assertEqual(router.calls, [])
        self.assertEqual(repo.claims, [])

    def test_repeated_noop_recovery_event_is_suppressed_without_db_spam(self) -> None:
        repo = FakeRepo()
        repo.claim_action = "deployment_not_running"
        service, router = _service(repo)

        first = asyncio.run(_ingest_recovery_event_with_id(service, event_id="evt-noop-1"))
        second = asyncio.run(_ingest_recovery_event_with_id(service, event_id="evt-noop-2"))

        self.assertEqual(router.calls, [])
        self.assertEqual(len(repo.claims), 1)
        self.assertEqual(len(repo.events), 1)
        self.assertFalse(first.get("skipped_db_write"))
        self.assertTrue(second["skipped_db_write"])
        self.assertEqual(second["backend_recovery"]["action"], "suppressed_repeated_noop_event")


if __name__ == "__main__":
    unittest.main()
