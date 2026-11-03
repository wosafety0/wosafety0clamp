from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import one_expedition_ui_loop as controller
from kancollebot.adapters.poi.cdp_fleet_state import PoiFleetMissionState, PoiShipSupplyState


def _full_supply(ship_ids):
    return tuple(PoiShipSupplyState(ship_id, 10, 20, 10, 20) for ship_id in ship_ids)


class VisualDocumentCacheTests(unittest.TestCase):
    def setUp(self):
        controller._VISUAL_DOCUMENT_CACHE.clear()

    def test_unchanged_document_is_read_once_and_changed_file_is_reloaded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fingerprints.json"
            path.write_text('{"states":{}}', encoding="utf-8")
            original_read_text = Path.read_text
            with patch.object(
                Path,
                "read_text",
                autospec=True,
                side_effect=lambda instance, *args, **kwargs: original_read_text(instance, *args, **kwargs),
            ) as read_text:
                first = controller._read_visual_document(path)
                second = controller._read_visual_document(path)
                self.assertIs(first, second)
                read_text.assert_called_once()

                path.write_text('{"states":{"HOME_PORT":{}}}', encoding="utf-8")
                third = controller._read_visual_document(path)
                self.assertEqual(third, {"states": {"HOME_PORT": {}}})
                self.assertEqual(read_text.call_count, 2)


class ProductionStartupSafetyGateTests(unittest.TestCase):
    def make_args(self, *, authorized):
        args = MagicMock()
        args.authorize_ui_actions = authorized
        args.poll_seconds = 5.0
        args.timeout_seconds = 1800.0
        args.grace_delay_seconds = 10.0
        return args

    def test_run_requires_explicit_authorization_before_composition(self):
        args = self.make_args(authorized=False)
        with patch.object(controller, "_run_production_supervisor_once") as compose:
            with self.assertRaisesRegex(
                controller.LoopError,
                "Missing explicit --authorize-ui-actions flag",
            ):
                controller.run(args)
        compose.assert_not_called()

    def test_authorized_run_enters_production_supervisor_composition(self):
        args = self.make_args(authorized=True)
        with patch.object(
            controller, "_run_production_supervisor_once", return_value=0
        ) as compose:
            self.assertEqual(controller.run(args), 0)
        compose.assert_called_once_with(args)


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
        with (
            patch.object(controller, "_require_ui_state", return_value="accessibility") as require_state,
            patch.object(controller, "_print_event") as event_log,
        ):
            verifier(2, "after", "EXPEDITION_LIST")
        event_log.assert_called_once_with(
            "dispatch_checkpoint_confirmed",
            step=2,
            phase="after",
            expected_state="EXPEDITION_LIST",
            evidence="accessibility",
        )
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
            verifier(5, "after", "EXPEDITION_ACTIVE")
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
                verifier(5, "after", "EXPEDITION_ACTIVE")

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

    def test_accessibility_checkpoint_returns_accessibility_evidence(self):
        with (
            patch.object(controller, "require_state") as require_state,
            patch.object(controller, "_print_event") as event_log,
        ):
            evidence = controller._require_ui_state(
                self.game_session, controller.UiState.HOME_PORT
            )
        self.assertEqual(evidence, "accessibility")
        require_state.assert_called_once_with(None, controller.UiState.HOME_PORT)
        event_log.assert_called_once_with(
            "ui_state_confirmed", ui_state="HOME_PORT", evidence="accessibility"
        )
        self.game_session.input.capture_screenshot.assert_not_called()

    def test_visual_state_requires_two_consecutive_matches(self):
        self.game_session.transport.send.return_value = {"nodes": []}
        self.game_session.input.capture_screenshot.return_value = "in-memory-payload"
        scores = {"HOME_PORT": 1.0}
        with (
            patch.object(controller, "classify_screenshot_fingerprints",
                         side_effect=[("UNKNOWN", scores), ("HOME_PORT", scores), ("HOME_PORT", scores)]) as classify,
            patch.object(controller.time, "sleep"),
        ):
            evidence = controller._require_ui_state(
                self.game_session, controller.UiState.HOME_PORT
            )
        self.assertEqual(evidence, "two_consecutive_in_memory_ooi_fingerprints")
        self.assertEqual(classify.call_count, 3)

    def test_template_only_checkpoint_requires_two_consecutive_matches(self):
        self.game_session.transport.send.return_value = {"nodes": []}
        self.game_session.input.capture_screenshot.return_value = "in-memory-payload"
        scores = {"return_notice.png": 0.99}
        with (
            patch.dict(os.environ, {
                "KANCOLLEBOT_FINGERPRINTS_PATH": "fingerprints.json",
                "KANCOLLEBOT_TEMPLATE_DIR": "templates",
            }),
            patch.object(Path, "is_file", return_value=True),
            patch.object(Path, "read_text", return_value='{"states":{}}'),
            patch.object(Path, "is_dir", return_value=True),
            patch.object(controller, "classify_screenshot_png",
                         side_effect=[("UNKNOWN", {}), ("RETURN_RESULT", scores), ("RETURN_RESULT", scores)]) as classify,
            patch.object(controller.time, "sleep"),
        ):
            evidence = controller._require_ui_state(self.game_session, controller.UiState.RETURN_RESULT)
        self.assertEqual(evidence, "two_consecutive_in_memory_template_matches")
        self.assertEqual(classify.call_count, 3)
        self.assertEqual(self.game_session.input.capture_screenshot.call_count, 3)

    def test_template_only_checkpoint_rejects_a_different_known_state(self):
        self.game_session.transport.send.return_value = {"nodes": []}
        self.game_session.input.capture_screenshot.return_value = "in-memory-payload"
        with (
            patch.dict(os.environ, {
                "KANCOLLEBOT_FINGERPRINTS_PATH": "fingerprints.json",
                "KANCOLLEBOT_TEMPLATE_DIR": "templates",
            }),
            patch.object(Path, "is_file", return_value=True),
            patch.object(Path, "read_text", return_value='{"states":{}}'),
            patch.object(Path, "is_dir", return_value=True),
            patch.object(controller, "classify_screenshot_png", return_value=("HOME_PORT", {})) as classify,
            patch.object(controller.time, "sleep"),
        ):
            with self.assertRaisesRegex(controller.LoopError, "classifier returned HOME_PORT"):
                controller._require_ui_state(self.game_session, controller.UiState.RETURN_RESULT)
        classify.assert_called_once()
        self.game_session.input.capture_screenshot.assert_called_once()

    def test_template_only_checkpoint_without_template_directory_fails_before_capture(self):
        self.game_session.transport.send.return_value = {"nodes": []}
        with (
            patch.dict(os.environ, {
                "KANCOLLEBOT_FINGERPRINTS_PATH": "missing-fingerprints.json",
                "KANCOLLEBOT_TEMPLATE_DIR": "",
            }),
            patch.object(Path, "is_file", return_value=False),
        ):
            with self.assertRaisesRegex(controller.LoopError, "no fingerprint for this state"):
                controller._require_ui_state(self.game_session, controller.UiState.RETURN_RESULT)
        self.game_session.input.capture_screenshot.assert_not_called()


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


class RewardCollectionIdleGateTests(unittest.TestCase):
    def setUp(self):
        self.idle = PoiFleetMissionState(2, "Fleet 2", (0, 0, 0, 0), (1, 4, 8, 25))
        self.returned = PoiFleetMissionState(2, "Fleet 2", (2, 3, 1000, 0), (1, 4, 8, 25))

    def test_idle_state_is_confirmed_without_waiting(self):
        with (
            patch.object(controller, "read_poi_fleet_states", return_value=(self.idle,)),
            patch.object(controller.time, "monotonic", return_value=0.0),
            patch.object(controller.time, "sleep") as sleep,
        ):
            result = controller._wait_for_idle(object())
        self.assertEqual(result, self.idle)
        sleep.assert_not_called()

    def test_completed_mission_must_transition_to_idle_before_success(self):
        with (
            patch.object(controller, "read_poi_fleet_states", side_effect=[(self.returned,), (self.idle,)]),
            patch.object(controller.time, "monotonic", side_effect=[0.0, 0.0, 1.0]),
            patch.object(controller.time, "sleep") as sleep,
        ):
            result = controller._wait_for_idle(object())
        self.assertEqual(result, self.idle)
        sleep.assert_called_once_with(1.0)

    def test_active_mission_is_rejected_without_retrying(self):
        active = PoiFleetMissionState(2, "Fleet 2", (1, 3, 1000, 0), (1, 4, 8, 25))
        with (
            patch.object(controller, "read_poi_fleet_states", return_value=(active,)) as read_state,
            patch.object(controller.time, "monotonic", return_value=0.0),
            patch.object(controller.time, "sleep") as sleep,
        ):
            with self.assertRaisesRegex(controller.LoopError, "Unexpected fleet 2 state"):
                controller._wait_for_idle(object())
        read_state.assert_called_once()
        sleep.assert_not_called()

    def test_missing_idle_confirmation_times_out(self):
        with (
            patch.object(controller, "read_poi_fleet_states", return_value=(self.returned,)),
            patch.object(controller.time, "monotonic", side_effect=[0.0, 0.0, 2.0, 11.0]),
            patch.object(controller.time, "sleep") as sleep,
        ):
            with self.assertRaisesRegex(controller.LoopError, "did not become idle after bonus advance"):
                controller._wait_for_idle(object(), timeout_seconds=10.0)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual([item.args for item in sleep.call_args_list], [(1.0,), (1.0,)])


class RewardScreenOrderingTests(unittest.TestCase):
    def test_bonus_screen_failure_blocks_next_click_idle_check_and_home_recovery(self):
        game_session = MagicMock()
        with (
            patch.object(controller, "_print_event"),
            patch.object(controller.time, "sleep"),
            patch.object(controller, "_require_ui_state",
                         side_effect=controller.LoopError("BONUS_ITEMS was not confirmed")),
            patch.object(controller, "_wait_for_idle") as wait_idle,
            patch.object(controller, "_recover_home_port") as recover_home,
        ):
            with self.assertRaisesRegex(controller.LoopError, "BONUS_ITEMS was not confirmed"):
                controller._collect_returned_expedition_reward(game_session, object(), 123456)

        game_session.input.click.assert_called_once_with(controller.RETURN_CONFIRM)
        wait_idle.assert_not_called()
        recover_home.assert_not_called()

    def test_bonus_screen_is_verified_before_idle_gate_and_home_recovery(self):
        events = []
        game_session = MagicMock()
        game_session.input.click.side_effect = lambda point: events.append(("click", point.label))
        idle = PoiFleetMissionState(2, "Fleet 2", (0, 0, 0, 0), (1, 4, 8, 25))

        with (
            patch.object(controller, "_print_event") as event_log,
            patch.object(controller, "time") as clock,
            patch.object(controller, "_require_ui_state",
                         side_effect=lambda _session, state: events.append(("state", state.value))),
            patch.object(controller, "_wait_for_idle", side_effect=lambda _reader: events.append(("idle",))),
            patch.object(controller, "_recover_home_port", side_effect=lambda _session: events.append(("home",))),
            patch.object(controller, "read_poi_fleet_states", return_value=(idle,)),
        ):
            clock.sleep.side_effect = lambda seconds: events.append(("sleep", seconds))
            result = controller._collect_returned_expedition_reward(game_session, object(), 123456)

        self.assertEqual(result, idle)
        event_log.assert_any_call(
            "ONE_EXPEDITION_LOOP_VERIFIED_READY", idle, dispatch_final_time_ms=123456
        )
        self.assertEqual(
            events,
            [
                ("click", "returned-expedition-confirm"),
                ("sleep", 1.5),
                ("state", "BONUS_ITEMS"),
                ("click", "returned-expedition-confirm"),
                ("sleep", 1.0),
                ("idle",),
                ("home",),
            ],
        )


class PostDispatchHomePortTests(unittest.TestCase):
    def setUp(self):
        supply = _full_supply((1, 4, 8, 25))
        self.active = PoiFleetMissionState(
            2, "Fleet 2", (1, 1, 2000, 0), (1, 4, 8, 25), supply
        )

    def test_dispatch_returns_to_home_port_and_rechecks_same_active_mission(self):
        with (
            patch.object(controller, "_recover_home_port") as recover,
            patch.object(controller, "read_poi_fleet_states", return_value=(self.active,)) as read_state,
            patch.object(controller, "_print_event") as log_event,
        ):
            result = controller._return_home_port_after_dispatch(
                MagicMock(), object(), self.active
            )

        self.assertEqual(result, self.active)
        recover.assert_called_once()
        read_state.assert_called_once()
        log_event.assert_called_once_with(
            "dispatch_home_port_verified",
            self.active,
            evidence="two_consecutive_in_memory_ooi_fingerprints_and_poi_active_tuple",
        )

    def test_dispatch_home_port_recheck_fails_if_mission_tuple_changed(self):
        returned = PoiFleetMissionState(
            2, "Fleet 2", (2, 1, 2000, 0), (1, 4, 8, 25), self.active.supply
        )
        with (
            patch.object(controller, "_recover_home_port") as recover,
            patch.object(controller, "read_poi_fleet_states", return_value=(returned,)),
        ):
            with self.assertRaisesRegex(controller.LoopError, "mission changed while returning"):
                controller._return_home_port_after_dispatch(
                    MagicMock(), object(), self.active
                )
        recover.assert_called_once()


class DispatchCheckpointGateTests(unittest.TestCase):
    def setUp(self):
        supply = _full_supply((1, 4, 8, 25))
        self.idle = PoiFleetMissionState(2, "Fleet 2", (0, 0, 0, 0), (1, 4, 8, 25), supply)
        self.active = PoiFleetMissionState(2, "Fleet 2", (1, 3, 2000, 0), (1, 4, 8, 25), supply)

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
        self.assertEqual(game_input.click.call_count, 5)
        self.assertEqual(len(observed), 9)
        self.assertEqual(observed[0], (1, "before", "HOME_PORT"))
        self.assertEqual(observed[-1], (5, "before", "FLEET_2_SELECTED"))
        self.assertEqual(
            game_input.click.call_args_list[-1].args[0], controller.EXPEDITION_START
        )


class PreStartFleetRecheckTests(unittest.TestCase):
    def test_changed_ship_membership_blocks_final_start_click(self):
        supply = _full_supply((1, 4, 8, 25))
        idle = PoiFleetMissionState(2, "Fleet 2", (0, 0, 0, 0), (1, 4, 8, 25), supply)
        changed = PoiFleetMissionState(2, "Fleet 2", (0, 0, 0, 0), (1, 4, 8, 26), supply)
        game_input = MagicMock()
        with (
            patch.object(controller, "read_poi_fleet_states", side_effect=[(idle,), (changed,)]),
            patch.object(controller.time, "sleep"),
        ):
            with self.assertRaisesRegex(controller.LoopError, "changed before expedition start"):
                controller._dispatch(object(), game_input, 1000, checkpoint_verifier=lambda *_: None)
        self.assertEqual(game_input.click.call_count, 4)
        self.assertNotIn(controller.EXPEDITION_START, [call.args[0] for call in game_input.click.call_args_list])

    def test_new_mission_blocks_final_start_click(self):
        supply = _full_supply((1, 4, 8, 25))
        idle = PoiFleetMissionState(2, "Fleet 2", (0, 0, 0, 0), (1, 4, 8, 25), supply)
        active = PoiFleetMissionState(2, "Fleet 2", (1, 3, 2000, 0), (1, 4, 8, 25), supply)
        game_input = MagicMock()
        with (
            patch.object(controller, "read_poi_fleet_states", side_effect=[(idle,), (active,)]),
            patch.object(controller.time, "sleep"),
        ):
            with self.assertRaisesRegex(controller.LoopError, "changed before expedition start"):
                controller._dispatch(object(), game_input, 1000, checkpoint_verifier=lambda *_: None)
        self.assertEqual(game_input.click.call_count, 4)


    def test_missing_supply_snapshot_blocks_final_start_click(self):
        idle = PoiFleetMissionState(2, "Fleet 2", (0, 0, 0, 0), (1, 4, 8, 25))
        game_input = MagicMock()
        with (
            patch.object(controller, "read_poi_fleet_states", side_effect=[(idle,), (idle,)]),
            patch.object(controller.time, "sleep"),
        ):
            with self.assertRaisesRegex(controller.LoopError, "supply snapshot is missing or incomplete"):
                controller._dispatch(object(), game_input, 1000, checkpoint_verifier=lambda *_: None)
        self.assertEqual(game_input.click.call_count, 4)
        self.assertNotIn(controller.EXPEDITION_START, [call.args[0] for call in game_input.click.call_args_list])

    def test_partially_supplied_ship_blocks_final_start_click(self):
        full = _full_supply((1, 4, 8, 25))
        partial = tuple(
            PoiShipSupplyState(item.ship_id, item.fuel, 19 if item.ship_id == 1 else item.ammo,
                               item.max_fuel, item.max_ammo)
            for item in full
        )
        idle = PoiFleetMissionState(2, "Fleet 2", (0, 0, 0, 0), (1, 4, 8, 25), full)
        under_supplied = PoiFleetMissionState(2, "Fleet 2", (0, 0, 0, 0), (1, 4, 8, 25), partial)
        game_input = MagicMock()
        with (
            patch.object(controller, "read_poi_fleet_states", side_effect=[(idle,), (under_supplied,)]),
            patch.object(controller.time, "sleep"),
        ):
            with self.assertRaisesRegex(controller.LoopError, "not fully supplied"):
                controller._dispatch(object(), game_input, 1000, checkpoint_verifier=lambda *_: None)
        self.assertEqual(game_input.click.call_count, 4)
        self.assertNotIn(controller.EXPEDITION_START, [call.args[0] for call in game_input.click.call_args_list])


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
        self.assertEqual(expected_after, "FLEET_2_SELECTED")


    def test_mission_row_coordinate_matches_ooi_mission_name(self):
        self.assertEqual(
            (controller.MISSION_ROW.x, controller.MISSION_ROW.y),
            (350, 350),
        )


    def test_fleet_3_tab_coordinate_matches_ooi_fleet_tab(self):
        self.assertEqual(
            (controller.FLEET_3_TAB.x, controller.FLEET_3_TAB.y),
            (630, 170),
        )

    def test_dispatch_skips_redundant_fleet_2_tab_click_after_mission_confirm(self):
        self.assertEqual(
            [step[0] for step in controller.DISPATCH_STEPS],
            [
                controller.SORTIE,
                controller.EXPEDITION_MODE,
                controller.MISSION_ROW,
                controller.MISSION_CONFIRM,
                controller.EXPEDITION_START,
            ],
        )
if __name__ == "__main__":
    unittest.main()
