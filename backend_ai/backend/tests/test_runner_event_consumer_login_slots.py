from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.events.runner_event_consumer import RunnerEventConsumerService
from app.services.control_plane_service import (
    MT5ControlPlaneService,
    MT5_LOGIN_PUBLIC_ERROR_CODE,
    MT5_LOGIN_PUBLIC_ERROR_MESSAGE,
)


class FakeRepo:
    def __init__(self, *, release_count: int = 1, fail_transition: bool = False) -> None:
        self.release_count = release_count
        self.fail_transition = fail_transition
        self.completed: list[dict[str, Any]] = []
        self.released_by_id: list[dict[str, Any]] = []
        self.released_by_account: list[dict[str, Any]] = []
        self.deliveries: list[dict[str, Any]] = []
        self.audits: list[dict[str, Any]] = []

    def complete_login_reservation(self, **kwargs: Any) -> dict[str, Any]:
        if self.fail_transition:
            raise RuntimeError("db_transition_failed")
        self.completed.append(kwargs)
        return {
            "id": kwargs.get("reservation_id"),
            "status": "verified" if kwargs.get("ok") else "failed",
            "last_error": kwargs.get("error_text"),
        }

    def release_login_reservation_by_id(self, **kwargs: Any) -> int:
        self.released_by_id.append(kwargs)
        return self.release_count

    def release_login_reservation(self, **kwargs: Any) -> int:
        self.released_by_account.append(kwargs)
        return self.release_count

    def update_execution_command_delivery(self, **kwargs: Any) -> None:
        self.deliveries.append(kwargs)

    def upsert_execution_audit(self, **kwargs: Any) -> None:
        self.audits.append(kwargs)


class FakeRedis:
    def __init__(self, *, fields: dict[str, Any], stop_event: asyncio.Event) -> None:
        self.fields = fields
        self.stop_event = stop_event
        self.read_count = 0
        self.xack_calls: list[tuple[Any, ...]] = []

    async def xgroup_create(self, **kwargs: Any) -> None:
        return None

    async def xreadgroup(self, **kwargs: Any) -> list[tuple[str, list[tuple[str, dict[str, Any]]]]]:
        self.read_count += 1
        if self.read_count == 1:
            return [("mt5:execution:events", [("1779285440192-0", self.fields)])]
        self.stop_event.set()
        return []

    async def xack(self, *args: Any) -> None:
        self.xack_calls.append(args)


class RedisBackedConsumer(RunnerEventConsumerService):
    def __init__(self, redis: FakeRedis, repo: FakeRepo) -> None:
        super().__init__(
            repo=repo,
            stream_key="mt5:execution:events",
            group_name="test-group",
            consumer_name="test-consumer",
            block_ms=1000,
        )
        self.fake_redis = redis

    async def _redis(self) -> FakeRedis:
        return self.fake_redis


def fields_for(event_type: str, **overrides: Any) -> dict[str, Any]:
    payload = {
        "reservation_id": overrides.pop("reservation_id", 140),
        "error_code": overrides.pop("error_code", "mt5_auth_failed"),
        "login_slot_ttl_sec": overrides.pop("login_slot_ttl_sec", 300),
        "terminal_pid": overrides.pop("terminal_pid", 1234),
    }
    payload.update(overrides.pop("payload", {}))
    fields = {
        "event_id": f"login-slot:{payload['reservation_id']}:{event_type.lower()}",
        "event_type": event_type,
        "command_id": overrides.pop("command_id", "cmd-1"),
        "runner_id": overrides.pop("runner_id", "runner-win-01"),
        "slot_id": overrides.pop("slot_id", "slot_04"),
        "account_id": str(overrides.pop("account_id", 211)),
        "severity": "info",
        "payload_json": json.dumps(payload),
    }
    fields.update(overrides)
    return fields


class RunnerEventConsumerLoginSlotTests(unittest.TestCase):
    def test_login_slot_failed_projects_reservation_failed_and_audits(self) -> None:
        repo = FakeRepo()
        consumer = RunnerEventConsumerService(repo=repo)
        asyncio.run(
            consumer._process_stream_entry(
                stream_id="1779285440192-0",
                fields=fields_for("LOGIN_SLOT_FAILED", command_id="22627202d78a4b6c8f5656b1bef7253f"),
            )
        )

        self.assertEqual(len(repo.completed), 1)
        transition = repo.completed[0]
        self.assertEqual(transition["reservation_id"], 140)
        self.assertEqual(transition["command_id"], "22627202d78a4b6c8f5656b1bef7253f")
        self.assertFalse(transition["ok"])
        self.assertEqual(transition["error_text"], "mt5_auth_failed")
        self.assertEqual(transition["runner_id"], "runner-win-01")
        self.assertEqual(transition["slot_id"], "slot-04")
        self.assertEqual(transition["payload"]["stream_id"], "1779285440192-0")
        self.assertEqual(repo.deliveries[0]["status"], "failed")
        self.assertEqual(repo.deliveries[0]["error_text"], "mt5_auth_failed")
        self.assertEqual(repo.audits[0]["audit_status"], "stream_projected")

    def test_login_slot_verified_projects_verified_with_slot_details(self) -> None:
        repo = FakeRepo()
        consumer = RunnerEventConsumerService(repo=repo)
        asyncio.run(
            consumer._process_stream_entry(
                stream_id="verified-stream-id",
                fields=fields_for(
                    "LOGIN_SLOT_VERIFIED",
                    reservation_id=141,
                    command_id="verified-command",
                    account_id=212,
                    slot_id="slot_01",
                    error_code="",
                    payload={"reason": "login_verified", "login_slot_ttl_sec": 450},
                ),
            )
        )

        transition = repo.completed[0]
        self.assertEqual(transition["reservation_id"], 141)
        self.assertTrue(transition["ok"])
        self.assertIsNone(transition["error_text"])
        self.assertEqual(transition["runner_id"], "runner-win-01")
        self.assertEqual(transition["slot_id"], "slot-01")
        self.assertEqual(transition["ttl_sec"], 450)
        self.assertEqual(transition["payload"]["terminal_pid"], 1234)
        self.assertEqual(repo.deliveries[0]["status"], "acknowledged")

    def test_login_slot_released_stale_event_does_not_overwrite_terminal_state(self) -> None:
        repo = FakeRepo(release_count=0)
        consumer = RunnerEventConsumerService(repo=repo)
        asyncio.run(
            consumer._process_stream_entry(
                stream_id="released-stream-id",
                fields=fields_for(
                    "LOGIN_SLOT_RELEASED",
                    reservation_id=140,
                    command_id="late-release-command",
                    payload={"reason": "expired_not_claimed"},
                ),
            )
        )

        self.assertEqual(repo.released_by_id[0]["reservation_id"], 140)
        self.assertEqual(repo.released_by_id[0]["account_id"], 211)
        self.assertEqual(repo.released_by_id[0]["reason"], "expired_not_claimed")
        self.assertEqual(repo.deliveries, [])
        self.assertEqual(len(repo.audits), 1)

    def test_consumer_does_not_xack_when_db_transition_fails(self) -> None:
        async def _run_case() -> tuple[FakeRedis, FakeRepo]:
            stop_event = asyncio.Event()
            repo = FakeRepo(fail_transition=True)
            redis = FakeRedis(fields=fields_for("LOGIN_SLOT_FAILED"), stop_event=stop_event)
            consumer = RedisBackedConsumer(redis=redis, repo=repo)
            await consumer.run_forever(stop_event)
            return redis, repo

        redis, repo = asyncio.run(_run_case())

        self.assertEqual(redis.xack_calls, [])
        self.assertEqual(repo.audits, [])

    def test_failed_login_slot_response_uses_public_message(self) -> None:
        response = MT5ControlPlaneService._login_slot_response(
            object(),
            reservation={
                "id": 140,
                "account_id": 211,
                "status": "failed",
                "last_error": "mt5_auth_failed",
                "payload_json": {"terminal_log_line": "authorization failed"},
            },
            account={"id": 211, "status": "login_failed", "last_error": "mt5_auth_failed"},
        )

        self.assertEqual(response["status"], "failed")
        self.assertEqual(response["login_state"], "FAILED")
        self.assertEqual(response["last_error"], MT5_LOGIN_PUBLIC_ERROR_CODE)
        self.assertEqual(response["account"]["last_error"], MT5_LOGIN_PUBLIC_ERROR_CODE)
        self.assertEqual(response["reservation"]["last_error"], MT5_LOGIN_PUBLIC_ERROR_CODE)
        self.assertNotIn("payload_json", response["reservation"])
        self.assertEqual(response["detail"], MT5_LOGIN_PUBLIC_ERROR_MESSAGE)


if __name__ == "__main__":
    unittest.main()
