from datetime import datetime, timezone

from app.orchestration.broker_routing import BrokerRoutePolicy
from app.orchestration.scheduler import choose_slot_for_account


def _route() -> BrokerRoutePolicy:
    return BrokerRoutePolicy(
        enabled=True,
        strict=True,
        route_key="dbg",
        broker="DBG Markets",
        server="DBGMarkets-Live",
        aliases={"dbg": frozenset({"dbg", "dbgmarkets"})},
    )


def _slot(*, runner_id: str, runner_status: str, broker: str) -> dict:
    return {
        "runner_id": runner_id,
        "slot_id": "slot-01",
        "status": "ready",
        "runner_status": runner_status,
        "runner_last_heartbeat_at": datetime.now(timezone.utc),
        "last_heartbeat_at": datetime.now(timezone.utc),
        "max_slots": 12,
        "allowed_profile_classes": [],
        "supported_profiles": [],
        "capability_tags": [],
        "metadata_json": {
            "available_for_new_account": True,
            "runner_pool": broker,
            "supported_brokers": [broker],
            "supported_mt5_servers": ["DBGMarkets-Live"] if broker == "dbg" else [],
        },
        "runner_metadata_json": {
            "runner_pool": broker,
            "supported_brokers": [broker],
            "supported_mt5_servers": ["DBGMarkets-Live"] if broker == "dbg" else [],
        },
    }


def test_broker_route_reports_offline_when_compatible_runner_exists_but_is_offline():
    decision = choose_slot_for_account(
        account_id=225,
        bot={"profile_class": "normal"},
        slots=[
            _slot(runner_id="runner-win-01", runner_status="online", broker="exness"),
            _slot(runner_id="runner-win-02", runner_status="offline", broker="dbg"),
        ],
        sticky_binding=None,
        broker_route=_route(),
    )

    assert not decision.ok
    assert decision.reason == "runner_offline"


def test_broker_route_reports_no_compatible_runner_when_none_support_the_broker():
    decision = choose_slot_for_account(
        account_id=225,
        bot={"profile_class": "normal"},
        slots=[
            _slot(runner_id="runner-win-01", runner_status="online", broker="exness"),
        ],
        sticky_binding=None,
        broker_route=_route(),
    )

    assert not decision.ok
    assert decision.reason == "no_compatible_runner_for_broker"
