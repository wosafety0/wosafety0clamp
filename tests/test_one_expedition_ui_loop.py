from __future__ import annotations

import base64
import inspect
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
        # A MagicMock is truthy for every attribute, so the wait-mode defaults
        # have to be real values here or run() would validate a mock.
        args.wait_for_return = False
        args.max_ticks = 60
        args.tick_interval_seconds = 10.0
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
        with patch(
            "kancollebot.adapters.poi.visual_target.require_visual_dependencies"
        ), patch.object(
            controller, "_run_production_supervisor_once", return_value=0
        ) as compose:
            self.assertEqual(controller.run(args), 0)
        compose.assert_called_once_with(args)


class SettleOnlyDispatcherTests(unittest.TestCase):
    """``--settle-only`` closes out a returned fleet and can never dispatch.

    Fleets whose expedition has no verified composition requirement cannot go
    through the production entry at all, because that gate runs before anything
    is observed. Settling them needs no composition, so the gate is skipped - and
    the dispatcher handed to the supervisor refuses the one thing that gate
    exists to protect.
    """

    def test_a_ready_fleet_is_never_dispatched(self):
        dispatcher = controller.SettleOnlyDispatcher(
            scheduler=object(),
            state_reader=object(),
            event_sink=lambda event, payload: None,
            mission_requirements=None,
        )
        with self.assertRaisesRegex(controller.LoopError, "settle-only run"):
            dispatcher.dispatch_one_ready(3)

    def test_it_wires_the_shared_objects_the_supervisor_compares(self):
        scheduler, reader, sink = object(), object(), lambda event, payload: None
        dispatcher = controller.SettleOnlyDispatcher(
            scheduler=scheduler,
            state_reader=reader,
            event_sink=sink,
            mission_requirements={},
        )
        self.assertIs(dispatcher.scheduler, scheduler)
        self.assertIs(dispatcher.state_reader, reader)
        self.assertIs(dispatcher.event_sink, sink)
        self.assertEqual(dispatcher.mission_requirements, {})


class WaitForReturnArgumentTests(unittest.TestCase):
    """--wait-for-return turns one tick into a bounded wait; the bound is checked."""

    def make_args(self, **overrides):
        args = MagicMock()
        args.authorize_ui_actions = True
        args.wait_for_return = True
        args.max_ticks = 60
        args.tick_interval_seconds = 10.0
        for name, value in overrides.items():
            setattr(args, name, value)
        return args

    def run_with_visual_dependencies(self, args):
        with patch("kancollebot.adapters.poi.visual_target.require_visual_dependencies"):
            with patch.object(controller, "_run_production_supervisor_once", return_value=0):
                return controller.run(args)

    def test_a_positive_tick_budget_is_accepted(self):
        self.assertEqual(self.run_with_visual_dependencies(self.make_args()), 0)

    def test_a_zero_tick_budget_is_refused(self):
        with self.assertRaisesRegex(controller.LoopError, "max-ticks"):
            self.run_with_visual_dependencies(self.make_args(max_ticks=0))

    def test_a_boolean_tick_budget_is_refused(self):
        # bool is an int in Python; a True budget would silently mean "one tick".
        with self.assertRaisesRegex(controller.LoopError, "max-ticks"):
            self.run_with_visual_dependencies(self.make_args(max_ticks=True))

    def test_a_non_positive_interval_is_refused(self):
        with self.assertRaisesRegex(controller.LoopError, "tick-interval-seconds"):
            self.run_with_visual_dependencies(self.make_args(tick_interval_seconds=0))

    def test_the_budget_is_ignored_without_wait_mode(self):
        # Single-tick runs keep their historical contract regardless of --max-ticks.
        self.assertEqual(
            self.run_with_visual_dependencies(
                self.make_args(wait_for_return=False, max_ticks=0)
            ),
            0,
        )


try:
    import cv2  # noqa: F401
    import numpy  # noqa: F401
    _VISION_DEPS_PRESENT = True
except ImportError:
    _VISION_DEPS_PRESENT = False


@unittest.skipIf(_VISION_DEPS_PRESENT, "vision dependencies are present")
class ProductionStartupVisualDependencyTests(unittest.TestCase):
    def make_args(self, *, authorized=True):
        args = MagicMock()
        args.authorize_ui_actions = authorized
        args.wait_for_return = False
        args.max_ticks = 60
        args.tick_interval_seconds = 10.0
        args.poll_seconds = 5.0
        args.timeout_seconds = 1800.0
        args.grace_delay_seconds = 10.0
        return args

    def test_authorized_run_fails_closed_without_visual_dependencies(self):
        args = self.make_args(authorized=True)
        with self.assertRaisesRegex(
            controller.LoopError, "Visual expedition UI unavailable"
        ):
            controller.run(args)


class DispatchTargetGateTests(unittest.TestCase):
    """Which mission and fleet a run is allowed to aim at, before any click."""

    def make_args(self, **overrides):
        args = MagicMock()
        args.fleet_id = 3
        args.mission_id = 3
        args.minimum_ships = None
        args.flagship_level = None
        for name, value in overrides.items():
            setattr(args, name, value)
        return args

    def test_a_verified_pair_needs_no_flags(self):
        requirement = controller._mission_requirement(self.make_args(), 3, 3)
        self.assertEqual(requirement.mission_id, 3)
        self.assertEqual(requirement.minimum_ships, 3)
        self.assertEqual(requirement.minimum_flagship_level, 3)

    def test_the_other_verified_pair_is_fleet_2_mission_1(self):
        requirement = controller._mission_requirement(self.make_args(), 2, 1)
        self.assertEqual((requirement.minimum_ships, requirement.minimum_flagship_level), (2, 1))

    def test_r3i_static_table_replaces_the_entry_local_hardcoded_pairs(self):
        # Mission 5 was "unverified" when only two live pairs were hardcoded.
        # R3-I supplies its numbers from the single versioned static table.
        requirement = controller._mission_requirement(self.make_args(mission_id=5), 3, 5)
        self.assertEqual((requirement.minimum_ships, requirement.minimum_flagship_level), (4, 3))

    def test_a_mission_without_a_static_rule_is_refused(self):
        with self.assertRaisesRegex(
            controller.LoopError, "no static requirement rule for fleet 3 / mission 999"
        ):
            controller._mission_requirement(self.make_args(mission_id=999), 3, 999)

    def test_a_missing_rule_cannot_be_bypassed_with_explicit_numbers(self):
        with self.assertRaisesRegex(controller.LoopError, "no static requirement rule"):
            controller._mission_requirement(
                self.make_args(mission_id=999, minimum_ships=6, flagship_level=99), 3, 999
            )

    def test_explicit_numbers_may_only_make_the_gate_stricter(self):
        requirement = controller._mission_requirement(
            self.make_args(mission_id=5, minimum_ships=5, flagship_level=4), 3, 5
        )
        self.assertEqual(requirement.mission_id, 5)
        self.assertEqual(requirement.minimum_ships, 5)
        self.assertEqual(requirement.minimum_flagship_level, 4)

    def test_an_override_weaker_than_the_static_rule_is_refused(self):
        for ships, level in ((3, 3), (4, 2)):
            with self.subTest(ships=ships, level=level):
                with self.assertRaisesRegex(controller.LoopError, "weaker than the static rule"):
                    controller._mission_requirement(
                        self.make_args(
                            mission_id=5, minimum_ships=ships, flagship_level=level
                        ),
                        3,
                        5,
                    )

    def test_half_a_requirement_is_refused(self):
        with self.assertRaisesRegex(controller.LoopError, "must be given together"):
            controller._mission_requirement(self.make_args(minimum_ships=4), 3, 5)

    def test_a_number_that_is_not_a_positive_integer_is_refused(self):
        for value in (0, -1, True, "4", 2.5):
            with self.subTest(value=value):
                with self.assertRaisesRegex(controller.LoopError, "must be a positive integer"):
                    controller._mission_requirement(
                        self.make_args(minimum_ships=value, flagship_level=2), 3, 5
                    )


class R3IProductionGateWiringTests(unittest.TestCase):
    """Guard against the LIVE dispatch gate being quietly removed.

    The controller's checker is optional for legacy/offline callers, so a unit
    test of the controller alone cannot prove that the production entry enables
    it. This source-level guard checks the only production construction site.
    """

    def test_the_production_entry_always_injects_the_requirement_checker(self):
        source = inspect.getsource(controller._run_production_supervisor_once)
        self.assertIn("requirement_checker=requirement_checker", source)
        self.assertIn('minimum_confidence="cross_checked"', source)
        self.assertIn("unlocked_fleet_ids=unlocked_fleet_ids", source)
        self.assertIn("dispatchable_fleet_ids=dispatchable_fleet_ids", source)
        self.assertIn("dispatch_requirement_gate_enabled", source)

    def test_the_entry_does_not_expose_a_bypass_switch_or_private_rule_table(self):
        source = inspect.getsource(controller._run_production_supervisor_once)
        self.assertNotIn("--disable-requirement-check", source)
        self.assertNotIn("requirement_checker=None", source)
        self.assertFalse(hasattr(controller, "KNOWN_COMPOSITION"))

    def test_the_monitor_composition_root_reads_the_static_rule_table(self):
        import run_continuous_expedition_monitor as monitor

        source = inspect.getsource(monitor.build_runtime)
        self.assertIn("requirements_for_assignments", source)
        self.assertNotIn("minimum_ships=2,", source)


class DispatchFleetValidationTests(unittest.TestCase):
    """The static policy gate: Fleet 1 reserved, 2/3/4 dispatchable."""

    def test_fleet_1_is_rejected_as_reserved(self):
        with self.assertRaisesRegex(controller.LoopError, "reserved"):
            controller._validate_dispatch_fleet_id(1)

    def test_unconfigured_fleet_is_rejected_by_policy(self):
        with self.assertRaisesRegex(controller.LoopError, "outside the dispatchable fleet policy"):
            controller._validate_dispatch_fleet_id(5)

    def test_dispatchable_fleets_are_accepted(self):
        # Fleet 4 is now dispatchable in principle. Whether it exists on a
        # given account is checked separately by _probe_unlocked_fleet_ids.
        controller._validate_dispatch_fleet_id(2)
        controller._validate_dispatch_fleet_id(3)
        controller._validate_dispatch_fleet_id(4)

    def test_probe_normalizes_the_observed_account_fleet_set(self):
        from kancollebot.adapters.poi.cdp_fleet_state import PoiFleetMissionState

        states = tuple(
            PoiFleetMissionState(fleet_id, f"Fleet {fleet_id}", (0, 0, 0, 0), (1, 2, 3, 4))
            for fleet_id in (1, 2, 3)
        )
        session = MagicMock()
        session.__enter__.return_value = session
        session.__exit__.return_value = False
        with patch.object(controller, "connect_poi_session", return_value=session), \
                patch.object(controller, "read_poi_fleet_states", return_value=states):
            self.assertEqual(controller._probe_unlocked_fleet_ids(), (1, 2, 3))


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
                         side_effect=[("UNKNOWN", scores)] * 6) as classify,
            patch.object(controller.time, "sleep"),
        ):
            with self.assertRaisesRegex(controller.LoopError, "no stable fingerprint match"):
                controller._require_ui_state(
                    self.game_session, controller.UiState.HOME_PORT, visual_attempts=6
                )
        self.assertEqual(classify.call_count, 6)

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

    def test_confirmed_state_writes_a_diagnostic_capture_only_when_asked(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        scores = {"HOME_PORT": 1.0}
        self.game_session.transport.send.return_value = {"nodes": []}
        self.game_session.input.capture_screenshot.return_value = base64.b64encode(
            png
        ).decode("ascii")
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(
                    controller,
                    "classify_screenshot_fingerprints",
                    side_effect=[("HOME_PORT", scores)] * 2,
                ),
                patch.object(controller.time, "sleep"),
                patch.object(controller, "_print_event") as event_log,
            ):
                controller._require_ui_state(
                    self.game_session,
                    controller.UiState.HOME_PORT,
                    capture_dir=directory,
                )
            written = Path(directory) / "HOME_PORT.png"
            self.assertEqual(written.read_bytes(), png)
        events = [call.args[0] for call in event_log.call_args_list]
        self.assertIn("diagnostic_capture_written", events)

    def test_the_return_notice_may_ask_for_the_composited_surface(self):
        # fromSurface=True drops the notice bubble (2026-11-05 live: the notice
        # scored 0.8332 there and 0.9994 on the composited surface), so the one
        # state that lives on that overlay has to be able to request it.
        scores = {"HOME_PORT_RETURN_NOTICE": 1.0}
        self.game_session.transport.send.return_value = {"nodes": []}
        self.game_session.input.capture_screenshot.return_value = "in-memory-payload"
        with (
            patch.object(
                controller,
                "classify_screenshot_fingerprints",
                side_effect=[("HOME_PORT_RETURN_NOTICE", scores)] * 2,
            ),
            patch.object(controller.time, "sleep"),
            patch.object(controller, "_print_event"),
        ):
            controller._require_ui_state(
                self.game_session,
                controller.UiState.HOME_PORT_RETURN_NOTICE,
                from_surface=False,
            )
        for call in self.game_session.input.capture_screenshot.call_args_list:
            self.assertIs(call.kwargs.get("from_surface"), False)

    def test_the_default_surface_stays_the_calibrated_one(self):
        scores = {"HOME_PORT": 1.0}
        self.game_session.transport.send.return_value = {"nodes": []}
        self.game_session.input.capture_screenshot.return_value = "in-memory-payload"
        with (
            patch.object(
                controller,
                "classify_screenshot_fingerprints",
                side_effect=[("HOME_PORT", scores)] * 2,
            ),
            patch.object(controller.time, "sleep"),
            patch.object(controller, "_print_event"),
        ):
            controller._require_ui_state(self.game_session, controller.UiState.HOME_PORT)
        for call in self.game_session.input.capture_screenshot.call_args_list:
            self.assertIs(call.kwargs.get("from_surface"), True)

    def test_a_non_bool_surface_choice_is_refused(self):
        with self.assertRaisesRegex(ValueError, "from_surface"):
            controller._require_ui_state(
                self.game_session,
                controller.UiState.HOME_PORT,
                from_surface="composited",
            )

    def test_no_diagnostic_capture_without_a_directory(self):
        scores = {"HOME_PORT": 1.0}
        self.game_session.transport.send.return_value = {"nodes": []}
        self.game_session.input.capture_screenshot.return_value = "in-memory-payload"
        with (
            patch.object(
                controller,
                "classify_screenshot_fingerprints",
                side_effect=[("HOME_PORT", scores)] * 2,
            ),
            patch.object(controller.time, "sleep"),
            patch.object(controller, "_write_diagnostic_capture") as writer,
        ):
            controller._require_ui_state(
                self.game_session, controller.UiState.HOME_PORT
            )
        writer.assert_not_called()

    def test_unreadable_capture_is_reported_but_does_not_block_the_gate(self):
        scores = {"HOME_PORT": 1.0}
        self.game_session.transport.send.return_value = {"nodes": []}
        self.game_session.input.capture_screenshot.return_value = "in-memory-payload"
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(
                    controller,
                    "classify_screenshot_fingerprints",
                    side_effect=[("HOME_PORT", scores)] * 2,
                ),
                patch.object(controller.time, "sleep"),
                patch.object(controller, "_print_event") as event_log,
            ):
                evidence = controller._require_ui_state(
                    self.game_session,
                    controller.UiState.HOME_PORT,
                    capture_dir=directory,
                )
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertEqual(evidence, "two_consecutive_in_memory_ooi_fingerprints")
        events = [call.args[0] for call in event_log.call_args_list]
        self.assertIn("diagnostic_capture_failed", events)

    def test_monitor_visual_checkpoint_uses_extended_bounded_attempts(self):
        self.game_session.transport.send.return_value = {"nodes": []}
        self.game_session.input.capture_screenshot.return_value = "in-memory-payload"
        scores = {"SORTIE_MENU": 1.0}
        observations = [("UNKNOWN", scores)] * 3 + [("SORTIE_MENU", scores)] * 2
        with (
            patch.object(
                controller,
                "classify_screenshot_fingerprints",
                side_effect=observations,
            ) as classify,
            patch.object(controller.time, "sleep"),
        ):
            evidence = controller._require_ui_state(
                self.game_session,
                controller.UiState.SORTIE_MENU,
                visual_attempts=5,
            )
        self.assertEqual(evidence, "two_consecutive_in_memory_ooi_fingerprints")
        self.assertEqual(classify.call_count, 5)

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
class HiddenHomePortUiLayerTests(unittest.TestCase):
    """A faded Home Port UI cannot be restored by the 母港 control inside it."""

    def test_faded_port_is_woken_by_exactly_one_area_click(self):
        game_session = MagicMock()
        states = iter([
            # One failed pre-check, then the three bounded 母港-button attempts,
            # and only then the wake click's verification.
            controller.LoopError("HOME_PORT was not confirmed"),
            controller.LoopError("HOME_PORT was not confirmed"),
            controller.LoopError("HOME_PORT was not confirmed"),
            controller.LoopError("HOME_PORT was not confirmed"),
            "two_consecutive_in_memory_ooi_fingerprints",
        ])

        def require_state(_session, _state):
            outcome = next(states)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with (
            patch.object(controller, "_print_event") as events,
            patch.object(controller.time, "sleep"),
            patch.object(controller, "_require_ui_state", side_effect=require_state),
            patch.object(controller, "_home_port_ui_layer_is_hidden", return_value=True),
            patch.object(controller, "_visible_settlement_screen", return_value=None),
        ):
            controller._recover_home_port(game_session)

        clicked = [call.args[0] for call in game_session.input.click.call_args_list]
        # Three bounded 母港-button attempts, then one wake click on the port area.
        self.assertEqual(len(clicked), 4)
        self.assertEqual(clicked[:3], [
            controller.home_port_candidates(controller.HOME_PORT)[0],
            controller.home_port_candidates(controller.HOME_PORT)[1],
            controller.home_port_candidates(controller.HOME_PORT)[2],
        ])
        self.assertEqual(clicked[3].label, "home-port-ui-wake")
        events.assert_any_call(
            "home_port_recovery_confirmed", attempt=4,
            evidence="ui_layer_woken_by_area_click",
        )

    def test_a_non_faded_screen_never_gets_the_area_click(self):
        # Every attempt fails and the frame is not a faded port, so the loop must
        # stop without clicking the middle of an unknown screen. The observed
        # fallback that follows may click the 母港 control - that is the point
        # of the fallback - but never the middle of the screen.
        game_session = MagicMock()
        with (
            patch.object(controller, "_print_event"),
            patch.object(controller.time, "sleep"),
            patch.object(controller, "_require_ui_state",
                         side_effect=controller.LoopError("not HOME_PORT")),
            patch.object(controller, "_home_port_ui_layer_is_hidden", return_value=False),
            patch.object(controller, "_visible_settlement_screen", return_value=None),
            patch.object(controller, "_reward_screen_on_any_layer", return_value=None),
        ):
            with self.assertRaisesRegex(controller.LoopError, "not reached after"):
                controller._recover_home_port(game_session)

        clicked = [call.args[0] for call in game_session.input.click.call_args_list]
        self.assertTrue(all(point.label == "home-port" for point in clicked))


class VisibleSettlementScreenTests(unittest.TestCase):
    """The startup guard: which screens the recovery is not allowed to click."""

    @staticmethod
    def session():
        game_session = MagicMock()
        game_session.input.capture_screenshot.return_value = "frame"
        return game_session

    def classify(self, observed):
        return (
            patch.object(controller, "_read_visual_document", return_value={}),
            patch.object(
                controller,
                "classify_screenshot_fingerprints",
                return_value=(observed, {}),
            ),
        )

    def test_a_reward_screen_is_reported(self):
        for state in ("HOME_PORT_RETURN_NOTICE", "RETURN_RESULT", "BONUS_ITEMS"):
            with self.subTest(state=state):
                read_doc, classify = self.classify(state)
                with read_doc, classify:
                    self.assertEqual(
                        controller._visible_settlement_screen(self.session()), state
                    )

    def test_the_port_is_not_a_reward_screen(self):
        read_doc, classify = self.classify("HOME_PORT")
        with read_doc, classify:
            self.assertIsNone(controller._visible_settlement_screen(self.session()))

    def test_an_unreadable_frame_is_not_treated_as_a_reward_screen(self):
        game_session = MagicMock()
        game_session.input.capture_screenshot.side_effect = RuntimeError("no frame")
        self.assertIsNone(controller._visible_settlement_screen(game_session))

    def test_it_asks_for_the_composited_frame(self):
        # The return notice only exists in the composited capture; asking for the
        # page surface is the bug this whole path exists to avoid.
        read_doc, classify = self.classify("HOME_PORT")
        game_session = self.session()
        with read_doc, classify:
            controller._visible_settlement_screen(game_session)
        game_session.input.capture_screenshot.assert_called_once_with(from_surface=False)


class RewardScreenRecoveryGuardTests(unittest.TestCase):
    """Recovery must not walk a reward chain it cannot prove.

    Every reward screen accepts a click anywhere, so a 母港 recovery click sent
    while one is up advances the chain blindly. Live 2026-11-05 the loop started
    on a result screen left by an interrupted settlement and two recovery clicks
    took it through bonus items back to the port.
    """

    def test_recovery_never_clicks_through_a_reward_screen(self):
        game_session = MagicMock()
        with (
            patch.object(controller, "_print_event"),
            patch.object(controller.time, "sleep"),
            patch.object(controller, "_require_ui_state",
                         side_effect=controller.LoopError("not HOME_PORT")),
            patch.object(controller, "_visible_settlement_screen",
                         return_value="RETURN_RESULT"),
        ):
            with self.assertRaisesRegex(controller.LoopError, "reward screen"):
                controller._recover_home_port(game_session)

        self.assertEqual(game_session.input.click.call_count, 0)


class StartupScreenProbeTests(unittest.TestCase):
    """The start-up snapshot: both layers captured, the composited one decides."""

    @staticmethod
    def session(capture_error=None):
        game_session = MagicMock()
        if capture_error is not None:
            game_session.input.capture_screenshot.side_effect = capture_error
        else:
            game_session.input.capture_screenshot.return_value = "frame"
        return game_session

    def probe(self, composited, default):
        return (
            patch.object(controller, "_read_visual_document", return_value={}),
            patch.object(
                controller,
                "classify_screenshot_fingerprints",
                side_effect=[(composited, {composited: 0.9987}), (default, {})],
            ),
            patch.object(controller, "_print_event"),
        )

    def test_the_composited_layer_decides_a_disagreeing_startup(self):
        # Live 2026-11-05: composited RETURN_NOTICE 0.9987 vs default
        # HOME_PORT 0.9986 on the same frame. The default reading would have
        # sent recovery into the reward chain, so it must be audit only.
        read_doc, classify, _event = self.probe(
            "HOME_PORT_RETURN_NOTICE", "HOME_PORT"
        )
        with read_doc, classify, _event:
            screen = controller._probe_startup_screen(self.session())
        self.assertEqual(screen.state, "HOME_PORT_RETURN_NOTICE")
        self.assertTrue(screen.layers_disagree)
        self.assertEqual(screen.default_surface_state, "HOME_PORT")

    def test_the_default_layer_proves_a_result_screen(self):
        # Live 2026-11-05: the result screen classified 1.0000 on the
        # default surface while the composited capture said UNKNOWN. A
        # composited-only probe walked straight past a live reward chain.
        read_doc, classify, _event = self.probe("UNKNOWN", "RETURN_RESULT")
        with read_doc, classify, _event:
            screen = controller._probe_startup_screen(self.session())
        self.assertEqual(screen.state, "RETURN_RESULT")

    def test_the_composited_layer_wins_when_both_prove_one(self):
        read_doc, classify, _event = self.probe(
            "HOME_PORT_RETURN_NOTICE", "BONUS_ITEMS"
        )
        with read_doc, classify, _event:
            screen = controller._probe_startup_screen(self.session())
        self.assertEqual(screen.state, "HOME_PORT_RETURN_NOTICE")

    def test_a_cdp_failure_propagates_without_any_click(self):
        with (
            patch.object(controller, "_read_visual_document", return_value={}),
            patch.object(
                controller,
                "classify_screenshot_fingerprints",
                side_effect=[("BONUS_ITEMS", {}), ("BONUS_ITEMS", {})],
            ),
            patch.object(controller, "_print_event"),
        ):
            broken = self.session(capture_error=RuntimeError("cdp gone"))
            with self.assertRaises(RuntimeError):
                controller._probe_startup_screen(broken)


class RewardScreenAnyLayerGuardTests(unittest.TestCase):
    """Recovery refuses a screen proven on either capture layer."""

    def test_a_default_layer_screen_refuses_recovery(self):
        # The result/bonus fingerprints were captured from the default surface,
        # so a composited UNKNOWN is not proof of absence.
        game_session = MagicMock()
        game_session.input.capture_screenshot.return_value = "frame"
        with (
            patch.object(controller, "_print_event"),
            patch.object(controller.time, "sleep"),
            patch.object(controller, "_require_ui_state",
                         side_effect=controller.LoopError("not HOME_PORT")),
            patch.object(controller, "_visible_settlement_screen", return_value=None),
            patch.object(
                controller,
                "classify_screenshot_fingerprints",
                return_value=("BONUS_ITEMS", {}),
            ),
        ):
            with self.assertRaisesRegex(controller.LoopError, "BONUS_ITEMS"):
                controller._recover_home_port(game_session)
        self.assertEqual(game_session.input.click.call_count, 0)


class ObservedHomePortRecoveryTests(unittest.TestCase):
    """The start-up recovery clicks and observes when it cannot name the page.

    Live 2026-11-05 the named-page recovery failed twice in a row on the sortie
    menu and the client had to be walked back by hand. The observed loop
    clicks the 母港 control, looks again, and stops at a recognised port - but
    it never clicks while a reward screen is up, because those advance on a
    click anywhere and belong to the settlement controller.
    """

    def test_the_observed_loop_clicks_until_the_port_is_proven(self):
        game_session = MagicMock()
        checks = {"count": 0}

        def require_state(_session, state, **kwargs):
            checks["count"] += 1
            if checks["count"] < 3:
                raise controller.LoopError("not HOME_PORT")
            return "two_consecutive_in_memory_ooi_fingerprints"

        with (
            patch.object(controller, "_print_event"),
            patch.object(controller.time, "sleep"),
            patch.object(controller, "_require_ui_state", side_effect=require_state),
            patch.object(controller, "_visible_settlement_screen", return_value=None),
            patch.object(controller, "_reward_screen_on_any_layer", return_value=None),
        ):
            controller._recover_home_port_by_observation(game_session)

        clicked = [call.args[0] for call in game_session.input.click.call_args_list]
        self.assertTrue(clicked)
        self.assertTrue(all(point.label == "home-port" for point in clicked))

    def test_the_observed_loop_refuses_while_a_reward_screen_is_up(self):
        game_session = MagicMock()
        with (
            patch.object(controller, "_print_event"),
            patch.object(controller.time, "sleep"),
            patch.object(controller, "_require_ui_state",
                         side_effect=controller.LoopError("not HOME_PORT")),
            patch.object(controller, "_reward_screen_on_any_layer",
                         return_value="RETURN_RESULT"),
        ):
            with self.assertRaisesRegex(controller.LoopError, "RETURN_RESULT"):
                controller._recover_home_port_by_observation(game_session)
        self.assertEqual(game_session.input.click.call_count, 0)

    def test_the_observed_loop_is_bounded(self):
        game_session = MagicMock()
        with (
            patch.object(controller, "_print_event"),
            patch.object(controller.time, "sleep"),
            patch.object(controller, "_require_ui_state",
                         side_effect=controller.LoopError("not HOME_PORT")),
            patch.object(controller, "_reward_screen_on_any_layer", return_value=None),
        ):
            with self.assertRaisesRegex(controller.LoopError, "not reached after"):
                controller._recover_home_port_by_observation(
                    game_session, attempts=3
                )
        self.assertLessEqual(game_session.input.click.call_count, 3)

    def test_the_named_recovery_falls_back_to_the_observed_loop(self):
        game_session = MagicMock()
        with (
            patch.object(controller, "_print_event"),
            patch.object(controller.time, "sleep"),
            patch.object(controller, "_require_ui_state",
                         side_effect=controller.LoopError("not HOME_PORT")),
            patch.object(controller, "_home_port_ui_layer_is_hidden", return_value=False),
            patch.object(controller, "_visible_settlement_screen", return_value=None),
            patch.object(controller, "_reward_screen_on_any_layer", return_value=None),
        ):
            with self.assertRaisesRegex(controller.LoopError, "not reached after"):
                controller._recover_home_port(game_session)
        self.assertGreaterEqual(game_session.input.click.call_count, 4)


if __name__ == "__main__":
    unittest.main()
