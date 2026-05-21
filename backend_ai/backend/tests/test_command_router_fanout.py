from __future__ import annotations

import asyncio
from typing import Any

from app.events.command_router import CommandRouterService
from app.models.control_plane import CommandType


class _FakeRepo:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.marked: list[dict[str, Any]] = []
        self.list_slots_calls = 0

    def list_slots(self) -> list[dict[str, Any]]:
        self.list_slots_calls += 1
        return [
            {
                "runner_id": "runner-win-01",
                "slot_id": "slot-01",
                "metadata_json": {"terminal_path": "C:/MT5/slot-01"},
            },
            {
                "runner_id": "runner-win-02",
                "slot_id": "slot-02",
                "metadata_json": {"terminal_path": "C:/MT5/slot-02"},
            },
        ]

    def get_execution_command_by_trace_identity(self, **kwargs: Any) -> None:
        return None

    def create_execution_command(self, **kwargs: Any) -> dict[str, Any]:
        self.created.append(kwargs)
        return {
            "command_id": kwargs["command_id"],
            "trace_id": kwargs["trace_id"],
            "runner_id": kwargs["runner_id"],
            "slot_id": kwargs["slot_id"],
            "queue_name": kwargs["queue_name"],
        }

    def mark_command_delivery(self, **kwargs: Any) -> None:
        self.marked.append(kwargs)


class _FakePublisher:
    def __init__(self) -> None:
        self.single_payloads: list[dict[str, Any]] = []
        self.batch_payloads: list[dict[str, Any]] = []

    async def publish_command(self, payload: dict[str, Any]) -> str:
        self.single_payloads.append(payload)
        return "stream-single"

    async def publish_command_batch(self, payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.batch_payloads.extend(payloads)
        return [
            {"stream_id": f"stream-{idx}", "duplicate": False}
            for idx, _ in enumerate(payloads, start=1)
        ]


def _router() -> tuple[CommandRouterService, _FakeRepo, _FakePublisher]:
    repo = _FakeRepo()
    publisher = _FakePublisher()
    router = CommandRouterService(repo)  # type: ignore[arg-type]
    router._publisher = publisher
    return router, repo, publisher


def test_dispatch_writes_db_queue_name_and_redis_payload_to_runner_queue_identity():
    router, repo, publisher = _router()

    result = asyncio.run(
        router.dispatch(
            command_type=CommandType.PLACE_ORDER,
            account_id=101,
            deployment_id=201,
            bot_id="GSAlgo",
            runner_id="runner-win-02",
            slot_id="slot_02",
            priority=70,
            payload={"request": {"symbol": "XAUUSD"}},
            trace_id="trace-1",
            command_id="cmd-1",
        )
    )

    assert result["delivery_status"] == "queued"
    assert repo.created[0]["queue_name"] == "mt5:runner:runner-win-02:commands"
    assert repo.created[0]["runner_id"] == "runner-win-02"
    assert repo.created[0]["slot_id"] == "slot-02"
    assert publisher.single_payloads[0]["runner_id"] == "runner-win-02"
    assert publisher.single_payloads[0]["payload"]["runner_id"] == "runner-win-02"
    assert publisher.single_payloads[0]["payload"]["terminal_path"] == "C:/MT5/slot-02"


def test_dispatch_fails_fast_when_runner_id_is_missing():
    router, repo, publisher = _router()

    try:
        asyncio.run(
            router.dispatch(
                command_type=CommandType.PLACE_ORDER,
                account_id=101,
                deployment_id=201,
                bot_id="GSAlgo",
                runner_id="",
                slot_id="slot-01",
                priority=70,
                payload={"request": {"symbol": "XAUUSD"}},
                trace_id="trace-missing-runner",
            )
        )
    except ValueError as exc:
        assert str(exc) == "runner_id_required"
    else:
        raise AssertionError("expected runner_id_required")

    assert repo.created == []
    assert publisher.single_payloads == []


def test_dispatch_batch_fanout_uses_one_slot_snapshot_and_per_runner_queues():
    router, repo, publisher = _router()

    results = asyncio.run(
        router.dispatch_batch(
            broadcast_id="broadcast-1",
            items=[
                {
                    "command_type": CommandType.PLACE_ORDER,
                    "account_id": 101,
                    "deployment_id": 201,
                    "bot_id": "GSAlgo",
                    "runner_id": "runner-win-01",
                    "slot_id": "slot-01",
                    "priority": 60,
                    "payload": {"request": {"symbol": "XAUUSD"}},
                    "trace_id": "trace-batch-1",
                },
                {
                    "command_type": CommandType.PLACE_ORDER,
                    "account_id": 102,
                    "deployment_id": 202,
                    "bot_id": "GSAlgo",
                    "runner_id": "runner-win-02",
                    "slot_id": "slot-02",
                    "priority": 60,
                    "payload": {"request": {"symbol": "XAUUSD"}},
                    "trace_id": "trace-batch-2",
                },
            ],
        )
    )

    assert [item["ok"] for item in results] == [True, True]
    assert repo.list_slots_calls == 1
    assert [item["queue_name"] for item in repo.created] == [
        "mt5:runner:runner-win-01:commands",
        "mt5:runner:runner-win-02:commands",
    ]
    assert [item["runner_id"] for item in publisher.batch_payloads] == [
        "runner-win-01",
        "runner-win-02",
    ]
    assert publisher.batch_payloads[0]["payload"]["terminal_path"] == "C:/MT5/slot-01"
    assert publisher.batch_payloads[1]["payload"]["terminal_path"] == "C:/MT5/slot-02"


def test_dispatch_batch_reports_missing_runner_without_publishing_that_item():
    router, repo, publisher = _router()

    results = asyncio.run(
        router.dispatch_batch(
            items=[
                {
                    "command_type": CommandType.PLACE_ORDER,
                    "account_id": 101,
                    "deployment_id": 201,
                    "bot_id": "GSAlgo",
                    "runner_id": "",
                    "slot_id": "slot-01",
                    "payload": {},
                    "trace_id": "trace-bad",
                },
                {
                    "command_type": CommandType.PLACE_ORDER,
                    "account_id": 102,
                    "deployment_id": 202,
                    "bot_id": "GSAlgo",
                    "runner_id": "runner-win-02",
                    "slot_id": "slot-02",
                    "payload": {},
                    "trace_id": "trace-good",
                },
            ],
        )
    )

    assert results[0]["ok"] is False
    assert "runner_id_required" in results[0]["error"]
    assert results[1]["ok"] is True
    assert len(repo.created) == 1
    assert len(publisher.batch_payloads) == 1
    assert publisher.batch_payloads[0]["runner_id"] == "runner-win-02"
