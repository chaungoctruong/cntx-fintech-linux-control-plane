import unittest

from app.orchestration.deployment_manager import _validate_bot_runtime_start_supported
from app.orchestration.bot_runtime_contract import (
    BOT_RUNTIME_LANE_BACKEND_WEBHOOK_SIGNAL,
    BOT_RUNTIME_LANE_MT5_EA_RUNTIME,
    bot_matches_runtime_lane,
    bot_runtime_lane,
    is_mt5_ea_runtime_bot,
)
from app.risk.orchestration_policy import OrchestrationPolicyError
from app.services.control_plane_service import (
    _dedupe_catalog_definitions_by_runtime_lane,
    _filter_bots_by_runtime_lane,
    _filter_enabled_runner_bot_strings,
    _filter_runner_bot_catalog_payload,
    _is_user_visible_catalog_bot,
    _runner_bot_definition,
)
from app.settings import settings


def _ea_runner_item() -> dict:
    return {
        "bot_id": "ea_scalper",
        "bot_code": "ea_scalper",
        "bot_name": "EA Scalper",
        "catalog_lane": "bot_ea",
        "bot_type": "mt5_ea_runtime",
        "execution_owner": "windows_runner",
        "windows_role": "mt5_ea_runtime",
        "runtime_language": "adapter",
        "requires_executor_slot": True,
        "ea": {
            "artifact_path": "Experts/CNTX/EA_Scalper.ex5",
            "source_path": "Experts/CNTX/EA_Scalper.mq5",
            "preset_template_path": "Presets/EA_Scalper.set",
        },
    }


class RunnerBotCatalogLaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old_direct_ea_runtime_enabled = settings.MT5_DIRECT_EA_RUNTIME_ENABLED

    def tearDown(self) -> None:
        settings.MT5_DIRECT_EA_RUNTIME_ENABLED = self._old_direct_ea_runtime_enabled

    def test_runner_catalog_filters_template_identities(self) -> None:
        self.assertEqual(
            _filter_enabled_runner_bot_strings(["gsalgovip", "_template", ".hidden"]),
            ["gsalgovip"],
        )

        payload = _filter_runner_bot_catalog_payload(
            {
                "source": "disk_multi",
                "bots": [
                    {"bot_code": "gsalgovip", "bot_name": "GsAlgo VIP"},
                    {"bot_code": "_template", "bot_name": "Template"},
                ],
            }
        )

        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["bot_codes"], ["gsalgovip"])

    def test_runner_bot_definition_preserves_bot_ea_contract(self) -> None:
        definition = _runner_bot_definition(runner_id="runner-win-01", source="disk_multi", raw=_ea_runner_item())

        self.assertIsNotNone(definition)
        assert definition is not None
        self.assertTrue(is_mt5_ea_runtime_bot(definition))
        self.assertEqual(definition["runtime_env"]["source"], "disk_multi")
        self.assertEqual(definition["runtime_env"]["catalog_lane"], "bot_ea")
        self.assertEqual(definition["runtime_env"]["bot_type"], "mt5_ea_runtime")
        self.assertEqual(definition["runtime_env"]["execution_owner"], "windows_runner")
        self.assertEqual(definition["runtime_env"]["windows_role"], "mt5_ea_runtime")
        self.assertEqual(definition["runtime_env"]["runtime_language"], "adapter")
        self.assertTrue(definition["runtime_env"]["ea"]["artifact_path"].endswith(".ex5"))
        self.assertEqual(definition["resource_hints"]["catalog_lane"], "bot_ea")
        self.assertTrue(definition["metadata"]["ea"]["preset_template_path"].endswith(".set"))
        self.assertEqual(bot_runtime_lane(definition), BOT_RUNTIME_LANE_MT5_EA_RUNTIME)
        self.assertTrue(bot_matches_runtime_lane(definition, "bot_ea"))
        self.assertFalse(bot_matches_runtime_lane(definition, BOT_RUNTIME_LANE_BACKEND_WEBHOOK_SIGNAL))

    def test_bot_ea_hidden_and_blocked_until_direct_ea_runtime_enabled(self) -> None:
        settings.MT5_DIRECT_EA_RUNTIME_ENABLED = False
        definition = _runner_bot_definition(runner_id="runner-win-01", source="disk_multi", raw=_ea_runner_item())
        self.assertIsNotNone(definition)
        assert definition is not None

        self.assertFalse(_is_user_visible_catalog_bot({**definition, "enabled": True, "status": "ACTIVE"}))
        with self.assertRaises(OrchestrationPolicyError) as caught:
            _validate_bot_runtime_start_supported(definition)
        self.assertEqual(str(caught.exception), "direct_ea_runtime_not_enabled")

    def test_bot_ea_visible_after_direct_ea_runtime_enabled(self) -> None:
        settings.MT5_DIRECT_EA_RUNTIME_ENABLED = True
        definition = _runner_bot_definition(runner_id="runner-win-01", source="disk_multi", raw=_ea_runner_item())
        self.assertIsNotNone(definition)
        assert definition is not None

        self.assertTrue(_is_user_visible_catalog_bot({**definition, "enabled": True, "status": "ACTIVE"}))
        _validate_bot_runtime_start_supported(definition)

    def test_bot_ea_public_start_requires_explicit_matching_lane(self) -> None:
        settings.MT5_DIRECT_EA_RUNTIME_ENABLED = True
        definition = _runner_bot_definition(runner_id="runner-win-01", source="disk_multi", raw=_ea_runner_item())
        self.assertIsNotNone(definition)
        assert definition is not None

        with self.assertRaises(OrchestrationPolicyError) as missing:
            _validate_bot_runtime_start_supported(definition, require_explicit_lane=True)
        self.assertEqual(str(missing.exception), "bot_runtime_lane_required")

        with self.assertRaises(OrchestrationPolicyError) as mismatch:
            _validate_bot_runtime_start_supported(
                definition,
                requested_runtime_lane=BOT_RUNTIME_LANE_BACKEND_WEBHOOK_SIGNAL,
                require_explicit_lane=True,
            )
        self.assertEqual(str(mismatch.exception), "bot_runtime_lane_mismatch")

        _validate_bot_runtime_start_supported(
            definition,
            requested_runtime_lane=BOT_RUNTIME_LANE_MT5_EA_RUNTIME,
            require_explicit_lane=True,
        )

    def test_runtime_lane_filter_keeps_bot_families_separate(self) -> None:
        settings.MT5_DIRECT_EA_RUNTIME_ENABLED = True
        ea_definition = _runner_bot_definition(runner_id="runner-win-01", source="disk_multi", raw=_ea_runner_item())
        assert ea_definition is not None
        backend_definition = {
            "bot_code": "gsalgovip",
            "bot_name": "GsAlgo VIP",
            "runtime_env": {
                "bot_type": "backend_webhook_signal",
                "execution_owner": "linux_backend",
                "windows_role": "mt5_executor_only",
            },
            "resource_hints": {},
        }

        bots = [backend_definition, ea_definition]
        self.assertEqual(
            [item["bot_code"] for item in _filter_bots_by_runtime_lane(bots, BOT_RUNTIME_LANE_BACKEND_WEBHOOK_SIGNAL)],
            ["gsalgovip"],
        )
        self.assertEqual(
            [item["bot_code"] for item in _filter_bots_by_runtime_lane(bots, BOT_RUNTIME_LANE_MT5_EA_RUNTIME)],
            ["ea_scalper"],
        )
        self.assertEqual(len(_filter_bots_by_runtime_lane(bots, "all")), 2)

    def test_catalog_lane_collision_keeps_backend_webhook_bot(self) -> None:
        settings.MT5_DIRECT_EA_RUNTIME_ENABLED = True
        ea_definition = _runner_bot_definition(
            runner_id="runner-win-01",
            source="disk_multi",
            raw={**_ea_runner_item(), "bot_code": "gsalgovip", "bot_id": "gsalgovip"},
        )
        assert ea_definition is not None
        backend_definition = {
            "bot_id": "gsalgovip",
            "bot_code": "gsalgovip",
            "bot_name": "GsAlgo VIP",
            "runtime_env": {
                "bot_type": "backend_webhook_signal",
                "execution_owner": "linux_backend",
                "windows_role": "mt5_executor_only",
            },
            "resource_hints": {},
        }

        deduped = _dedupe_catalog_definitions_by_runtime_lane([ea_definition, backend_definition])
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["bot_code"], "gsalgovip")
        self.assertEqual(bot_runtime_lane(deduped[0]), BOT_RUNTIME_LANE_BACKEND_WEBHOOK_SIGNAL)


if __name__ == "__main__":
    unittest.main()
