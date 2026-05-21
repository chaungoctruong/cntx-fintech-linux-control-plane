from __future__ import annotations

from app.api.v2.system import (
    _build_broker_pool_health,
    _build_health_badge,
    _build_mt5_capacity_check,
    _build_ops_summary,
)


def _runner(
    *,
    runner_id: str,
    status: str,
    broker_key: str,
    operational_status: str = "ONLINE_AVAILABLE",
    is_stale: bool = False,
    accepts_new_work: bool = False,
    ready_slots: int = 0,
    available_slots: int = 0,
    servers: list[str] | None = None,
) -> dict:
    return {
        "runner_id": runner_id,
        "status": status,
        "operational_status": operational_status,
        "is_stale": is_stale,
        "accepts_new_work": accepts_new_work,
        "ready_slots": ready_slots,
        "available_slots": available_slots,
        "metadata_json": {
            "runner_pool": broker_key,
            "supported_brokers": [broker_key],
            "supported_mt5_servers": list(servers or []),
        },
    }


def test_broker_pool_health_flags_configured_broker_when_runner_is_offline():
    health = _build_broker_pool_health(
        [
            _runner(
                runner_id="runner-win-01",
                status="online",
                broker_key="exness",
                accepts_new_work=True,
                ready_slots=8,
                available_slots=8,
            ),
            _runner(
                runner_id="runner-win-02",
                status="offline",
                broker_key="dbg",
                is_stale=True,
                ready_slots=9,
                available_slots=9,
                servers=["DBGMarkets-Live"],
            ),
        ]
    )

    by_broker = {item["broker_key"]: item for item in health["items"]}
    assert health["status"] == "down"
    assert by_broker["dbg"]["status"] == "down"
    assert by_broker["dbg"]["service_available_slots"] == 0
    assert by_broker["dbg"]["supported_mt5_servers"] == ["DBGMarkets-Live"]
    assert "broker_pool_no_online_runner" in by_broker["dbg"]["alerts"]
    assert "broker_pool_down:dbg" in health["alerts"]


def test_broker_pool_health_keeps_pool_ok_when_degraded_runner_still_has_capacity():
    health = _build_broker_pool_health(
        [
            _runner(
                runner_id="runner-win-02",
                status="online",
                operational_status="DEGRADED",
                broker_key="dbg",
                accepts_new_work=False,
                ready_slots=9,
                available_slots=9,
                servers=["DBGMarkets-Live"],
            )
        ]
    )

    dbg = {item["broker_key"]: item for item in health["items"]}["dbg"]
    assert health["status"] == "ok"
    assert dbg["status"] == "ok"
    assert dbg["ready_runners"] == 1
    assert dbg["service_available_slots"] == 9
    assert "broker_pool_has_degraded_runner" in dbg["warnings"]


def test_health_badge_reports_broker_pool_down_without_exposing_runner_ids():
    dashboard = {
        "summary": {
            "total_runners": 2,
            "online_runners": 1,
            "ready_runners": 1,
            "maintenance_runners": 0,
            "degraded_runners": 0,
            "full_runners": 0,
            "stale_runners": 1,
            "login_slot_queue_depth": 0,
            "command_queue_depth": 0,
            "capacity_available": True,
        },
        "runners": [
            _runner(
                runner_id="runner-win-01",
                status="online",
                broker_key="exness",
                accepts_new_work=True,
                ready_slots=8,
                available_slots=8,
            ),
            _runner(
                runner_id="runner-win-02",
                status="offline",
                broker_key="dbg",
                is_stale=True,
                ready_slots=9,
                available_slots=9,
                servers=["DBGMarkets-Live"],
            ),
        ],
    }

    badge = _build_health_badge(dashboard)

    assert badge["level"] == "degraded"
    assert badge["reason"] == "broker_pool_down"
    assert badge["summary"]["broker_pools"]["down"] == 1
    assert "runner-win-02" not in str(badge)


def test_ops_summary_promotes_broker_pool_down_to_internal_alert():
    broker_pools = _build_broker_pool_health(
        [
            _runner(
                runner_id="runner-win-02",
                status="offline",
                broker_key="dbg",
                is_stale=True,
                ready_slots=9,
                available_slots=9,
                servers=["DBGMarkets-Live"],
            )
        ]
    )
    summary = _build_ops_summary(
        {
            "runners": {"total": 1, "online": 0, "stale": 1, "degraded": 0},
            "slots": {"total": 9, "ready": 9, "available": 0, "broken": 0},
            "login_slots": {},
            "commands": {},
            "deployments": {},
            "bindings": {},
            "thresholds": {},
        },
        {
            "redis_available": True,
            "redis_login_slot_depth": 0,
            "redis_command_depth": 0,
            "redis_event_pending": 0,
            "redis_event_stream_length": 0,
            "runner_queue_depths": [],
        },
        broker_pools=broker_pools,
        now=1,
    )

    assert summary["ok"] is False
    assert "broker_pool_down" in summary["alerts"]
    assert summary["broker_pools"]["status"] == "down"


def test_mt5_capacity_check_degrades_when_one_broker_pool_is_down_without_leaking_runner_ids():
    check = _build_mt5_capacity_check(
        {
            "summary": {
                "total_runners": 2,
                "online_runners": 1,
                "ready_runners": 1,
                "available_slots": 8,
                "capacity_available": True,
            },
            "runners": [
                _runner(
                    runner_id="runner-win-01",
                    status="online",
                    broker_key="exness",
                    accepts_new_work=True,
                    ready_slots=8,
                    available_slots=8,
                ),
                _runner(
                    runner_id="runner-win-02",
                    status="offline",
                    broker_key="dbg",
                    is_stale=True,
                    ready_slots=9,
                    available_slots=9,
                    servers=["DBGMarkets-Live"],
                ),
            ],
        }
    )

    assert check["status"] == "degraded"
    assert check["error"] == "broker_pool_down"
    assert check["broker_pools"]["down"] == 1
    assert "runner-win-02" not in str(check)


def test_mt5_capacity_check_stays_ok_when_broker_pools_have_service_slots():
    check = _build_mt5_capacity_check(
        {
            "summary": {
                "total_runners": 1,
                "online_runners": 1,
                "ready_runners": 0,
                "available_slots": 9,
                "capacity_available": False,
            },
            "runners": [
                _runner(
                    runner_id="runner-win-02",
                    status="online",
                    operational_status="DEGRADED",
                    broker_key="dbg",
                    ready_slots=9,
                    available_slots=9,
                    servers=["DBGMarkets-Live"],
                )
            ],
        }
    )

    assert check["status"] == "ok"
    assert check["broker_pools"]["ok"] == 1
    assert "broker_pool_has_degraded_runner" in check["warnings"]
