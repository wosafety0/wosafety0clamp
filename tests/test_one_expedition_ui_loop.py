from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import one_expedition_ui_loop as controller
from kancollebot.adapters.poi.cdp_fleet_state import PoiFleetMissionState


class ProductionStartupSafetyGateTests(unittest.TestCase):
    def test_run_fails_before_opening_any_cdp_session(self):
        args = MagicMock()
        args.authorize_ui_actions = True
        args.poll_seconds = 5.0
        args.timeout_seconds = 1800.0
        args.grace_delay_seconds = 10.0

        with (
            patch.object(controller, "connect_poi_session") as connect_poi,
            patch.object(controller, "connect_game_webview") as connect_game,
        ):
            with self.assertRaisesRegex(
                controller.LoopError,
                "return-result and bonus-item checkpoints are not yet verified",
            ):
                controller.run(args)

        connect_poi.assert_not_called()
        connect_game.assert_not_called()


class LiveCheckpointVerifierTests(unittest.TestCase):
    def setUp(self):
        self.reader = MagicMock()
        self.game_session = MagicMock()
        self.expected_start_ms = 1000
        self.active = PoiFleetMissionState(
            2, "Fleet 2", (1, 3, 2000, 0), (1, 4, 8, 25)
        )

    def test_visual_checkpoint_routes_to_live_ui_state_gate(self):
        verifier = controller.build_checkpoint_verifier(
            self.reader, self.game_session, self.expected_start_ms
        )
        with patch.object(controller, "_require_ui_state") as require_state:
            verifier(2, "after", "EXPEDITION_LIST")
        require_state.assert_called_once_with(
            self.game_session, controller.UiState.EXPEDITION_LIST
        )

    def test_active_checkpoint_requires_same_new_mission(self):
        verifier = controller.build_checkpoint_verifier(
            self.reader, self.game_session, self.expected_start_ms
        )
        with patch.object(
            controller, "read_poi_fleet_states", return_value=[self.active]
        ) as read_states:
            verifier(6, "after", "EXPEDITION_ACTIVE")
        read_states.assert_called_once_with(self.reader)

    def test_active_checkpoint_rejects_stale_mission_timestamp(self):
        stale = PoiFleetMissionState(
            2, "Fleet 2", (1, 3, 1000, 0), (1, 4, 8, 25)
        )
        verifier = controller.build_checkpoint_verifier(
            self.reader, self.game_session, self.expected_start_ms
        )
        with patch.object(controller, "read_poi_fleet_states", return_value=[stale]):
            with self.assertRaisesRegex(controller.LoopError, "same newly started expedition"):
                verifier(6, "after", "EXPEDITION_ACTIVE")

    def test_unknown_checkpoint_state_is_rejected(self):
        verifier = controller.build_checkpoint_verifier(
            self.reader, self.game_session, self.expected_start_ms
        )
        with self.assertRaisesRegex(controller.LoopError, "Unknown UI checkpoint state"):
            verifier(1, "before", "NOT_A_REAL_STATE")

    def test_unknown_visual_state_is_bounded_and_fails_closed(self):
        self.game_session.transport.send.return_value = {"nodes": []}
        self.game_session.input.capture_screenshot.return_value = "in-memory-payload"
        scores = {"HOME_PORT": 0.5}
        with (
            patch.object(controller, "classify_screenshot_fingerprints",
                         side_effect=[("UNKNOWN", scores)] * 3) as classify,
            patch.object(controller.time, "sleep"),
        ):
            with self.assertRaisesRegex(controller.LoopError, "no stable fingerprint match"):
                controller._require_ui_state(
                    self.game_session, controller.UiState.HOME_PORT
                )
        self.assertEqual(classify.call_count, 3)

    def test_visual_state_requires_two_consecutive_matches(self):
        self.game_session.transport.send.return_value = {"nodes": []}
        self.game_session.input.capture_screenshot.return_value = "in-memory-payload"
        scores = {"HOME_PORT": 1.0}
        with (
            patch.object(controller, "classify_screenshot_fingerprints",
                         side_effect=[("UNKNOWN", scores), ("HOME_PORT", scores), ("HOME_PORT", scores)]) as classify,
            patch.object(controller.time, "sleep"),
        ):
            controller._require_ui_state(
                self.game_session, controller.UiState.HOME_PORT
            )
        self.assertEqual(classify.call_count, 3)


class ReturnGraceDelayTests(unittest.TestCase):
    def setUp(self):
        self.active = PoiFleetMissionState(2, "Fleet 2", (1, 3, 1000, 0), (1, 4, 8, 25))
        self.returned = PoiFleetMissionState(2, "Fleet 2", (2, 3, 1000, 0), (1, 4, 8, 25))

    def test_home_port_recovery_uses_live_verified_icon_coordinate(self):
        self.assertEqual(controller.HOME_PORT.x, 50)
        self.assertEqual(controller.HOME_PORT.y, 50)

    def test_scheduled_expiry_waits_one_grace_period_then_requests_ui_refresh(self):
        with (
            patch.object(controller, "read_poi_fleet_states", return_value=(self.active,)),
            patch.object(controller.time, "monotonic", side_effect=[0.0, 0.0, 11.0]),
            patch.object(controller.time, "time", return_value=2.0),
            patch.object(controller.time, "sleep") as sleep,
        ):
            result = controller._wait_for_return(object(), 1000, 5.0, 60.0, 10.0)
        self.assertEqual(result, self.active)
        sleep.assert_called_once_with(10.0)

    def test_already_returned_state_skips_grace_delay(self):
        with (
            patch.object(controller, "read_poi_fleet_states", return_value=(self.returned,)),
            patch.object(controller.time, "monotonic", side_effect=[0.0, 0.0]),
            patch.object(controller.time, "sleep") as sleep,
        ):
            result = controller._wait_for_return(object(), 1000, 5.0, 60.0, 10.0)
        self.assertEqual(result, self.returned)
        sleep.assert_not_called()

    def test_unexpected_mission_fails_closed_before_sleep(self):
        other = PoiFleetMissionState(2, "Fleet 2", (1, 1, 1000, 0), (1, 4, 8, 25))
        with (
            patch.object(controller, "read_poi_fleet_states", return_value=(other,)),
            patch.object(controller.time, "monotonic", side_effect=[0.0, 0.0]),
            patch.object(controller.time, "sleep") as sleep,
        ):
            with self.assertRaises(controller.LoopError):
                controller._wait_for_return(object(), 1000, 5.0, 60.0, 10.0)
        sleep.assert_not_called()


class DispatchCheckpointGateTests(unittest.TestCase):
    def setUp(self):
        self.idle = PoiFleetMissionState(2, "Fleet 2", (0, 0, 0, 0), (1, 4, 8, 25))
        self.active = PoiFleetMissionState(2, "Fleet 2", (1, 3, 2000, 0), (1, 4, 8, 25))

    def test_dispatch_refuses_to_click_without_checkpoint_verifier(self):
        game_input = MagicMock()
        with patch.object(
            controller, "read_poi_fleet_states", return_value=(self.idle,)
        ) as read_state:
            with self.assertRaisesRegex(controller.LoopError, "before any click"):
                controller._dispatch(object(), game_input, 1000)
        game_input.click.assert_not_called()
        read_state.assert_not_called()

    def test_dispatch_stops_immediately_when_a_post_click_checkpoint_fails(self):
        game_input = MagicMock()
        observed = []

        def verify(step, phase, expected_state):
            observed.append((step, phase, expected_state))
            if (step, phase) == (2, "after"):
                raise controller.LoopError("expedition list checkpoint was not confirmed")

        with (
            patch.object(
                controller, "read_poi_fleet_states", return_value=(self.idle,)
            ),
            patch.object(controller.time, "sleep"),
        ):
            with self.assertRaisesRegex(controller.LoopError, "checkpoint was not confirmed"):
                controller._dispatch(
                    object(), game_input, 1000, checkpoint_verifier=verify
                )

        self.assertEqual(game_input.click.call_count, 2)
        self.assertEqual(
            observed,
            [
                (1, "before", "HOME_PORT"),
                (1, "after", "SORTIE_MENU"),
                (2, "before", "SORTIE_MENU"),
                (2, "after", "EXPEDITION_LIST"),
            ],
        )

    def test_dispatch_checks_every_checkpoint_before_next_click_and_verifies_active_tuple(self):
        game_input = MagicMock()
        observed = []

        def verify(step, phase, expected_state):
            observed.append((step, phase, expected_state))

        with (
            patch.object(
                controller,
                "read_poi_fleet_states",
                side_effect=[(self.idle,), (self.idle,), (self.active,)],
            ),
            patch.object(controller.time, "monotonic", side_effect=[0.0, 1.0]),
            patch.object(controller.time, "sleep"),
        ):
            result = controller._dispatch(
                object(), game_input, 1000, checkpoint_verifier=verify
            )

        self.assertEqual(result, self.active)
        self.assertEqual(game_input.click.call_count, 6)
        self.assertEqual(len(observed), 11)
        self.assertEqual(observed[0], (1, "before", "HOME_PORT"))
        self.assertEqual(observed[-1], (6, "before", "FLEET_2_SELECTED"))
        self.assertEqual(
            game_input.click.call_args_list[-1].args[0], controller.EXPEDITION_START
        )


class PreStartFleetRecheckTests(unittest.TestCase):
    def test_changed_ship_membership_blocks_final_start_click(self):
        idle = PoiFleetMissionState(2, "Fleet 2", (0, 0, 0, 0), (1, 4, 8, 25))
        changed = PoiFleetMissionState(2, "Fleet 2", (0, 0, 0, 0), (1, 4, 8, 26))
        game_input = MagicMock()
        with (
            patch.object(controller, "read_poi_fleet_states", side_effect=[(idle,), (changed,)]),
            patch.object(controller.time, "sleep"),
        ):
            with self.assertRaisesRegex(controller.LoopError, "changed before expedition start"):
                controller._dispatch(object(), game_input, 1000, checkpoint_verifier=lambda *_: None)
        self.assertEqual(game_input.click.call_count, 5)
        self.assertNotIn(controller.EXPEDITION_START, [call.args[0] for call in game_input.click.call_args_list])

    def test_new_mission_blocks_final_start_click(self):
        idle = PoiFleetMissionState(2, "Fleet 2", (0, 0, 0, 0), (1, 4, 8, 25))
        active = PoiFleetMissionState(2, "Fleet 2", (1, 3, 2000, 0), (1, 4, 8, 25))
        game_input = MagicMock()
        with (
            patch.object(controller, "read_poi_fleet_states", side_effect=[(idle,), (active,)]),
            patch.object(controller.time, "sleep"),
        ):
            with self.assertRaisesRegex(controller.LoopError, "changed before expedition start"):
                controller._dispatch(object(), game_input, 1000, checkpoint_verifier=lambda *_: None)
        self.assertEqual(game_input.click.call_count, 5)


class LiveUiCoordinateEvidenceTests(unittest.TestCase):
    def test_expedition_mode_coordinate_matches_ooi_menu_card(self):
        self.assertEqual(
            (controller.EXPEDITION_MODE.x, controller.EXPEDITION_MODE.y),
            (1000, 360),
        )


    def test_fleet_2_tab_coordinate_matches_ooi_fleet_tab(self):
        self.assertEqual(
            (controller.FLEET_2_TAB.x, controller.FLEET_2_TAB.y),
            (590, 170),
        )


    def test_mission_confirm_is_gated_by_mission_selected_state(self):
        point, _, expected_before, expected_after = controller.DISPATCH_STEPS[3]
        self.assertEqual(point, controller.MISSION_CONFIRM)
        self.assertEqual(expected_before, "MISSION_SELECTED")
        self.assertEqual(expected_after, "FLEET_SELECTION")


    def test_mission_row_coordinate_matches_ooi_mission_name(self):
        self.assertEqual(
            (controller.MISSION_ROW.x, controller.MISSION_ROW.y),
            (350, 350),
        )


if __name__ == "__main__":
    unittest.main()
