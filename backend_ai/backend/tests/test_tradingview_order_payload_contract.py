import asyncio
import time

from app.settings import settings
from app.services.control_plane_service import MT5ControlPlaneService


def _service(repo):
    return MT5ControlPlaneService(repo=repo, store=object())


class _DeliveryRepo:
    def __init__(self):
        self.kwargs = None

    def update_execution_command_delivery(self, **kwargs):
        self.kwargs = kwargs
        return {"command_id": kwargs["command_id"], "delivery_status": kwargs["status"]}


def _contract_body(**overrides):
    now_ms = int(time.time() * 1000)
    body = {
        "contract_version": 2,
        "schema_version": 2,
        "alert_time_ms": now_ms,
        "bar_time_ms": now_ms,
    }
    body.update(overrides)
    return body


def _backend_webhook_subscriber_contract() -> dict:
    return {
        "bot_runtime_env": {
            "bot_type": "backend_webhook_signal",
            "execution_owner": "linux_backend",
            "windows_role": "mt5_executor_only",
            "tradingview_webhook_owner": "linux",
        },
        "bot_resource_hints": {
            "bot_type": "backend_webhook_signal",
            "execution_owner": "linux_backend",
            "windows_role": "mt5_executor_only",
            "tradingview_webhook_owner": "linux",
        },
        "bot_metadata_json": {},
    }


def _ea_runtime_subscriber_contract() -> dict:
    return {
        "bot_runtime_env": {
            "catalog_lane": "bot_ea",
            "bot_type": "mt5_ea_runtime",
            "execution_owner": "windows_runner",
            "windows_role": "mt5_ea_runtime",
            "runtime_language": "adapter",
        },
        "bot_resource_hints": {
            "catalog_lane": "bot_ea",
            "bot_type": "mt5_ea_runtime",
            "execution_owner": "windows_runner",
            "windows_role": "mt5_ea_runtime",
            "runtime_language": "adapter",
        },
        "bot_metadata_json": {},
    }


def test_acknowledged_command_delivery_does_not_store_success_retcode_as_error():
    repo = _DeliveryRepo()
    service = _service(repo)

    result = service.update_execution_command_delivery(
        command_id="cmd-1",
        delivery_status="acknowledged",
        error_text="10009",
        payload={"result": {"retcode": 10009}},
    )

    assert result["delivery_status"] == "acknowledged"
    assert repo.kwargs["error_text"] is None


def test_tradingview_place_order_request_uses_runner_distance_contract_with_absolute_levels():
    request = MT5ControlPlaneService._tradingview_place_order_request(
        symbol="XAUUSD",
        side="sell",
        volume=0.01,
        stop_loss=4524.0,
        take_profit=4504.0,
        magic=123456,
        body=_contract_body(entry_price=4514.0),
    )

    assert request["stop_loss"] == 10.0
    assert request["take_profit"] == 10.0
    assert request["sl_price"] == 10.0
    assert request["tp_price"] == 10.0
    assert request["sl"] == 10.0
    assert request["tp"] == 10.0
    assert request["sl_distance"] == 10.0
    assert request["tp_distance"] == 10.0
    assert request["entry_price"] == 4514.0
    assert request["risk_unit"] == "price_distance"
    assert request["distance_unit"] == "price_distance"
    assert request["sl_tp_unit"] == "price_distance"
    assert request["runner_order_contract_version"] == 3
    assert request["legacy_sltp_aliases_unit"] == "price_distance"
    assert request["legacy_sltp_aliases_are_distances"] is True
    assert "price_level_unit" not in request
    assert request["entry_type"] == "market"
    assert request["order_type"] == "MARKET"
    assert request["pending_order"] is False
    assert request["order_contract_version"] == 2
    assert request["risk_guard"]["account_locking"] is False
    assert request["risk_guard"]["enforcement"] == "reject_order_only"


def test_tradingview_place_order_request_rejects_wrong_side_price_levels():
    try:
        MT5ControlPlaneService._tradingview_place_order_request(
            symbol="XAUUSD",
            side="sell",
            volume=0.01,
            stop_loss=4300.0,
            take_profit=4800.0,
            magic=123456,
            body=_contract_body(entry_price=4514.0),
        )
    except ValueError as exc:
        assert str(exc) == "tradingview_price_levels_invalid"
    else:
        raise AssertionError("expected tradingview_price_levels_invalid")


def test_tradingview_place_order_request_requires_entry_price_to_bridge_runner_distances():
    try:
        MT5ControlPlaneService._tradingview_place_order_request(
            symbol="XAUUSD",
            side="buy",
            volume=0.01,
            stop_loss=2380.0,
            take_profit=2450.0,
            magic=123456,
            body=_contract_body(),
        )
    except ValueError as exc:
        assert str(exc) == "tradingview_entry_price_required"
    else:
        raise AssertionError("expected tradingview_entry_price_required")


def test_tradingview_place_order_request_keeps_legacy_input_aliases_outbound_safe():
    body = _contract_body(sl=2380.0, tp=2450.0, entry=2400.0)
    request = MT5ControlPlaneService._tradingview_place_order_request(
        symbol="XAUUSD",
        side="buy",
        volume=0.01,
        stop_loss=MT5ControlPlaneService._optional_positive_body_float(body, "sl", "stop_loss"),
        take_profit=MT5ControlPlaneService._optional_positive_body_float(body, "tp", "take_profit"),
        magic=123456,
        body=body,
    )

    assert request["stop_loss"] == 20.0
    assert request["take_profit"] == 50.0
    assert request["sl_price"] == 20.0
    assert request["tp_price"] == 50.0
    assert request["entry_price"] == 2400.0
    assert request["sl"] == 20.0
    assert request["tp"] == 50.0
    assert request["sl_distance"] == 20.0
    assert request["tp_distance"] == 50.0


def test_tradingview_place_order_request_supports_dca_limit_contract():
    body = _contract_body(
        entry_price=2397.5,
        dca_price=2397.5,
        signal_role="DCA",
    )
    request = MT5ControlPlaneService._tradingview_place_order_request(
        symbol="XAUUSD",
        side="buy",
        volume=0.01,
        stop_loss=2395.0,
        take_profit=2410.0,
        magic=123456,
        body=body,
        entry_type="limit",
    )

    assert request["entry_type"] == "limit"
    assert request["order_type"] == "BUY_LIMIT"
    assert request["pending_order"] is True
    assert request["limit_price"] == 2397.5
    assert request["price"] == 2397.5
    assert request["stop_loss"] == 2.5
    assert request["take_profit"] == 12.5
    assert request["sl_price"] == 2.5
    assert request["tp_price"] == 12.5


def test_tradingview_place_order_request_supports_sell_dca_limit_contract():
    body = _contract_body(
        entry_price=4533.97,
        dca_price=4533.97,
        signal_role="DCA",
    )
    request = MT5ControlPlaneService._tradingview_place_order_request(
        symbol="XAUUSD",
        side="sell",
        volume=0.01,
        stop_loss=4536.47,
        take_profit=4526.47,
        magic=123456,
        body=body,
        entry_type="limit",
    )

    assert request["entry_type"] == "limit"
    assert request["order_type"] == "SELL_LIMIT"
    assert request["pending_order"] is True
    assert request["limit_price"] == 4533.97
    assert request["price"] == 4533.97
    assert request["entry_price"] == 4533.97
    assert request["stop_loss"] == 2.5
    assert request["take_profit"] == 7.5
    assert request["sl_price"] == 2.5
    assert request["tp_price"] == 7.5


def test_tradingview_dca_limit_body_uses_dca_price_as_entry():
    body = _contract_body(
        entry_price=2400.0,
        dca_price=2397.5,
        dca_order_type="limit",
    )

    assert MT5ControlPlaneService._tradingview_dca_limit_requested(body) is True
    dca_body = MT5ControlPlaneService._tradingview_dca_limit_body(body)
    assert dca_body["entry_price"] == 2397.5
    assert dca_body["signal_role"] == "DCA"


def test_dispatch_tradingview_broadcast_sends_entry_and_dca_limit_commands():
    class FakeRepo:
        def list_subscribers_for_signal(self, *, signal_id, bot_code, limit):
            return [
                {
                    "subscription_id": 10,
                    "account_id": 101,
                    "deployment_id": 9001,
                    "runner_id": "runner-win-01",
                    "slot_id": "slot-01",
                    "bot_code": "gsalgovip",
                    "subscription_priority": 80,
                    "deployment_config_json": {"trading": {"lot_size": 0.02}},
                    "runner_capabilities_json": {"supports_pending_limit_orders": True},
                    **_backend_webhook_subscriber_contract(),
                }
            ]

    class FakeCommandRouter:
        def __init__(self):
            self.items = []
            self.broadcast_id = ""

        async def dispatch_batch(self, *, items, broadcast_id):
            self.items = items
            self.broadcast_id = broadcast_id
            return [
                {"ok": True, "command_record": {"command_id": f"cmd-{index}"}}
                for index, _ in enumerate(items)
            ]

    router = FakeCommandRouter()
    service = _service(FakeRepo())
    service._command_router = router

    body = _contract_body(
        alert_id="test-dca-limit-broadcast",
        signal_id="gsalgovip-xauusd",
        bot_code="gsalgovip",
        action="BUY",
        symbol="XAUUSD",
        entry_price=2400.0,
        stop_loss=2395.0,
        take_profit=2410.0,
        dca_order_type="limit",
        dca_entry_type="limit",
        dca_price=2397.5,
        dca_limit_price=2397.5,
    )

    result = asyncio.run(
        service.dispatch_tradingview_broadcast(
            body=body,
            header_secret=str(getattr(settings, "TRADINGVIEW_WEBHOOK_SECRET", "") or ""),
        )
    )

    assert result["subscribers_total"] == 1
    assert result["commands_total"] == 2
    assert [item["signal_role"] for item in result["results"]] == ["ENTRY", "DCA"]

    entry_request = router.items[0]["payload"]["request"]
    dca_request = router.items[1]["payload"]["request"]

    assert entry_request["entry_type"] == "market"
    assert entry_request["order_type"] == "MARKET"
    assert entry_request["pending_order"] is False
    assert entry_request["entry_price"] == 2400.0
    assert entry_request["volume"] == 0.02
    assert entry_request["stop_loss"] == 5.0
    assert entry_request["take_profit"] == 10.0
    assert entry_request["sl_price"] == 5.0
    assert entry_request["tp_price"] == 10.0
    assert router.items[0]["payload"]["tradingview_stop_loss_price"] == 2395.0
    assert router.items[0]["payload"]["tradingview_take_profit_price"] == 2410.0
    assert router.items[0]["payload"]["tradingview_price_level_unit"] == "absolute_price"
    assert router.items[0]["payload"]["runner_order_contract"]["version"] == 3
    assert router.items[0]["payload"]["runner_order_contract"]["request_sl_tp_unit"] == "price_distance"

    assert dca_request["entry_type"] == "limit"
    assert dca_request["order_type"] == "BUY_LIMIT"
    assert dca_request["pending_order"] is True
    assert dca_request["limit_price"] == 2397.5
    assert dca_request["entry_price"] == 2397.5
    assert dca_request["volume"] == 0.02
    assert dca_request["stop_loss"] == 2.5
    assert dca_request["take_profit"] == 12.5
    assert dca_request["sl_price"] == 2.5
    assert dca_request["tp_price"] == 12.5
    assert router.items[1]["payload"]["tradingview_stop_loss_price"] == 2395.0
    assert router.items[1]["payload"]["tradingview_take_profit_price"] == 2410.0
    assert router.items[1]["payload"]["tradingview_price_level_unit"] == "absolute_price"
    assert router.items[1]["payload"]["runner_order_contract"]["legacy_aliases_unit"] == "price_distance"
    assert router.items[0]["payload"]["strategy_code"] == "default"
    assert router.items[0]["payload"]["order_intent"]["role"] == "ENTRY"
    assert router.items[1]["payload"]["order_intent"]["role"] == "DCA"


def test_dispatch_tradingview_broadcast_skips_dca_limit_without_runner_capability():
    class FakeRepo:
        def list_subscribers_for_signal(self, *, signal_id, bot_code, limit):
            return [
                {
                    "subscription_id": 10,
                    "account_id": 101,
                    "deployment_id": 9001,
                    "runner_id": "runner-win-01",
                    "slot_id": "slot-01",
                    "bot_code": "gsalgovip",
                    "subscription_priority": 80,
                    "deployment_config_json": {"trading": {"lot_size": 0.02}},
                    "runner_capabilities_json": {},
                    **_backend_webhook_subscriber_contract(),
                }
            ]

        def get_runner(self, *, runner_id):
            return {"runner_id": runner_id, "capabilities_json": {}}

    class FakeCommandRouter:
        def __init__(self):
            self.items = []

        async def dispatch_batch(self, *, items, broadcast_id):
            self.items = items
            return [{"ok": True, "command_record": {"command_id": "cmd-entry"}}]

    router = FakeCommandRouter()
    service = _service(FakeRepo())
    service._command_router = router

    body = _contract_body(
        alert_id="test-dca-limit-unsupported-runner",
        signal_id="gsalgovip-xauusd",
        bot_code="gsalgovip",
        action="BUY",
        symbol="XAUUSD",
        entry_price=2400.0,
        stop_loss=2395.0,
        take_profit=2410.0,
        dca_order_type="limit",
        dca_price=2397.5,
    )

    result = asyncio.run(
        service.dispatch_tradingview_broadcast(
            body=body,
            header_secret=str(getattr(settings, "TRADINGVIEW_WEBHOOK_SECRET", "") or ""),
        )
    )

    assert result["subscribers_total"] == 1
    assert result["commands_total"] == 1
    assert result["dispatched"] == 1
    assert result["failed"] == 1
    assert len(router.items) == 1
    assert router.items[0]["payload"]["request"]["order_type"] == "MARKET"
    assert result["results"][0]["signal_role"] == "ENTRY"
    assert result["results"][1]["signal_role"] == "DCA"
    assert result["results"][1]["error"] == "tradingview_dca_limit_runner_unsupported"


def test_dispatch_tradingview_broadcast_routes_strategy_metadata():
    class FakeRepo:
        def list_subscribers_for_signal(self, *, signal_id, bot_code, limit):
            return [
                {
                    "subscription_id": 10,
                    "account_id": 101,
                    "deployment_id": 9001,
                    "runner_id": "runner-win-01",
                    "slot_id": "slot-01",
                    "bot_code": "gsalgovip",
                    "subscription_priority": 80,
                    "deployment_config_json": {"trading": {"lot_size": 0.02}},
                    "subscription_metadata": {"allowed_strategy_codes": ["turtle-soup-v1"]},
                    "runner_capabilities_json": {"supports_pending_limit_orders": True},
                    **_backend_webhook_subscriber_contract(),
                }
            ]

    class FakeCommandRouter:
        def __init__(self):
            self.items = []
            self.broadcast_id = ""

        async def dispatch_batch(self, *, items, broadcast_id):
            self.items = items
            self.broadcast_id = broadcast_id
            return [{"ok": True, "command_record": {"command_id": "cmd-entry"}}]

    router = FakeCommandRouter()
    service = _service(FakeRepo())
    service._command_router = router

    body = _contract_body(
        alert_id="test-strategy-route",
        signal_id="gsalgovip-xauusd",
        bot_code="gsalgovip",
        strategy_code="turtle-soup-v1",
        action="BUY",
        symbol="XAUUSD",
        entry_price=2400.0,
        stop_loss=2395.0,
        take_profit=2410.0,
    )

    result = asyncio.run(
        service.dispatch_tradingview_broadcast(
            body=body,
            header_secret=str(getattr(settings, "TRADINGVIEW_WEBHOOK_SECRET", "") or ""),
        )
    )

    assert result["strategy_code"] == "turtle-soup-v1"
    assert result["commands_total"] == 1
    assert result["failed"] == 0
    assert result["results"][0]["strategy_code"] == "turtle-soup-v1"
    assert result["broadcast_id"].endswith(":strategy:turtle-soup-v1")
    assert ":strategy:turtle-soup-v1:" in router.items[0]["trace_id"]

    payload = router.items[0]["payload"]
    assert payload["broadcast_strategy_code"] == "turtle-soup-v1"
    assert payload["tradingview_signal_contract"]["strategy_code"] == "turtle-soup-v1"
    assert payload["tradingview_signal_contract"]["allowed_strategy_codes"] == ["turtle-soup-v1"]
    assert payload["order_intent"]["strategy_code"] == "turtle-soup-v1"
    assert payload["order_intent"]["role"] == "ENTRY"


def test_dispatch_tradingview_broadcast_skips_strategy_mismatch():
    class FakeRepo:
        def list_subscribers_for_signal(self, *, signal_id, bot_code, limit):
            return [
                {
                    "subscription_id": 10,
                    "account_id": 101,
                    "deployment_id": 9001,
                    "runner_id": "runner-win-01",
                    "slot_id": "slot-01",
                    "bot_code": "gsalgovip",
                    "subscription_priority": 80,
                    "deployment_config_json": {"trading": {"lot_size": 0.02}},
                    "subscription_metadata": {"strategy_code": "mean-reversion-v2"},
                    "runner_capabilities_json": {"supports_pending_limit_orders": True},
                    **_backend_webhook_subscriber_contract(),
                }
            ]

    class FakeCommandRouter:
        def __init__(self):
            self.items = None

        async def dispatch_batch(self, *, items, broadcast_id):
            self.items = items
            return []

    router = FakeCommandRouter()
    service = _service(FakeRepo())
    service._command_router = router

    body = _contract_body(
        alert_id="test-strategy-mismatch",
        signal_id="gsalgovip-xauusd",
        bot_code="gsalgovip",
        strategy_code="turtle-soup-v1",
        action="SELL",
        symbol="XAUUSD",
        entry_price=2400.0,
        stop_loss=2405.0,
        take_profit=2390.0,
    )

    result = asyncio.run(
        service.dispatch_tradingview_broadcast(
            body=body,
            header_secret=str(getattr(settings, "TRADINGVIEW_WEBHOOK_SECRET", "") or ""),
        )
    )

    assert router.items == []
    assert result["commands_total"] == 0
    assert result["dispatched"] == 0
    assert result["failed"] == 1
    assert result["results"][0]["signal_role"] == "ROUTE"
    assert result["results"][0]["strategy_code"] == "turtle-soup-v1"
    assert result["results"][0]["allowed_strategy_codes"] == ["mean-reversion-v2"]
    assert result["results"][0]["error"] == "tradingview_strategy_mismatch"


def test_dispatch_tradingview_broadcast_skips_non_webhook_runtime_lane():
    class FakeRepo:
        def list_subscribers_for_signal(self, *, signal_id, bot_code, limit):
            return [
                {
                    "subscription_id": 10,
                    "account_id": 101,
                    "deployment_id": 9001,
                    "runner_id": "runner-win-01",
                    "slot_id": "slot-01",
                    "bot_code": "ea_scalper",
                    "subscription_priority": 80,
                    "deployment_config_json": {"trading": {"lot_size": 0.02}},
                    "runner_capabilities_json": {"supports_pending_limit_orders": True},
                    **_ea_runtime_subscriber_contract(),
                }
            ]

    class FakeCommandRouter:
        def __init__(self):
            self.items = None

        async def dispatch_batch(self, *, items, broadcast_id):
            self.items = items
            return []

    router = FakeCommandRouter()
    service = _service(FakeRepo())
    service._command_router = router

    body = _contract_body(
        alert_id="test-runtime-lane-mismatch",
        signal_id="gsalgovip-xauusd",
        action="BUY",
        symbol="XAUUSD",
        entry_price=2400.0,
        stop_loss=2395.0,
        take_profit=2410.0,
    )

    result = asyncio.run(
        service.dispatch_tradingview_broadcast(
            body=body,
            header_secret=str(getattr(settings, "TRADINGVIEW_WEBHOOK_SECRET", "") or ""),
        )
    )

    assert router.items == []
    assert result["commands_total"] == 0
    assert result["dispatched"] == 0
    assert result["failed"] == 1
    assert result["results"][0]["signal_role"] == "ROUTE"
    assert result["results"][0]["runtime_lane"] == "mt5_ea_runtime"
    assert result["results"][0]["error"] == "tradingview_runtime_lane_mismatch"


def test_tradingview_place_order_request_rejects_old_contract():
    try:
        MT5ControlPlaneService._tradingview_place_order_request(
            symbol="XAUUSD",
            side="buy",
            volume=0.01,
            stop_loss=2395.0,
            take_profit=2410.0,
            magic=123456,
            body={"entry_price": 2400.0},
        )
    except ValueError as exc:
        assert str(exc) == "tradingview_contract_version_required"
    else:
        raise AssertionError("expected tradingview_contract_version_required")


def test_tradingview_place_order_request_rejects_stale_alert():
    try:
        MT5ControlPlaneService._tradingview_place_order_request(
            symbol="XAUUSD",
            side="buy",
            volume=0.01,
            stop_loss=2395.0,
            take_profit=2410.0,
            magic=123456,
            body=_contract_body(entry_price=2400.0, alert_time_ms=1_700_000_000_000),
        )
    except ValueError as exc:
        assert str(exc) == "tradingview_alert_stale"
    else:
        raise AssertionError("expected tradingview_alert_stale")


def test_tradingview_place_order_request_rejects_extreme_distance():
    try:
        MT5ControlPlaneService._tradingview_place_order_request(
            symbol="XAUUSD",
            side="sell",
            volume=0.01,
            stop_loss=4800.0,
            take_profit=4300.0,
            magic=123456,
            body=_contract_body(entry_price=4514.0),
        )
    except ValueError as exc:
        assert str(exc) == "tradingview_price_distance_exceeds_limit"
    else:
        raise AssertionError("expected tradingview_price_distance_exceeds_limit")
