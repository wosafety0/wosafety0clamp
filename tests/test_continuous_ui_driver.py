"""Offline tests for the production CDP continuous UI driver."""

import tempfile
import unittest
from unittest.mock import patch

from kancollebot.adapters.poi.cdp_fleet_state import (
    PoiFleetMissionState,
    PoiShipSupplyState,
)
from kancollebot.adapters.poi.cdp_input import RecordedPoint
from kancollebot.adapters.poi.cdp_ui_driver import (
    DEFAULT_FLEET2_MISSION3_PLAN,
    ContinuousUiDriverError,
    DispatchPlan,
    DispatchStep,
    PoiCdpContinuousUiDriver,
    ProductionStateGate,
)
from kancollebot.adapters.poi.ui_state_gate import UiState, UiStateGateError


def make_fleet(fleet_id=2, mission=(0, 0, 0, 0), ship_ids=(5, 6, 7, 8), supply=None):
    return PoiFleetMissionState(
        fleet_id=fleet_id,
        name=None,
        mission=mission,
        ship_ids=ship_ids,
        supply=supply if supply is not None else (),
    )


def supplied_supply(ship_ids=(5, 6, 7, 8)):
    return tuple(
        PoiShipSupplyState(ship_id=ship_id, fuel=10, ammo=10, max_fuel=10, max_ammo=10)
        for ship_id in ship_ids
    )


class ScriptedFleetStates:
    """Return scripted snapshot batches; the last batch repeats forever."""

    def __init__(self, *batches):
        self.batches = [tuple(batch) for batch in batches]
        self.calls = 0

    def __call__(self):
        index = min(self.calls, len(self.batches) - 1)
        self.calls += 1
        return self.batches[index]


class ScriptedGate:
    """Pass after a configured number of failures per state."""

    def __init__(self, fail_counts=None, evidence="stub-evidence"):
        self.fail_counts = dict(fail_counts or {})
        self.evidence = evidence
        self.calls = []

    def require(self, expected):
        self.calls.append(expected)
        remaining = self.fail_counts.get(expected, 0)
        if remaining > 0:
            self.fail_counts[expected] = remaining - 1
            raise ContinuousUiDriverError(f"stub failure for {expected.value}")
        return self.evidence


class FakeInput:
    def __init__(self):
        self.clicks = []

    def click(self, point):
        self.clicks.append(point.label)


class FakeClock:
    def __init__(self, step=1.0):
        self.t = 0.0
        self.step = step

    def monotonic(self):
        value = self.t
        self.t += self.step
        return value


def build_driver(states, gate, game_input=None, clock=None):
    clock = clock or FakeClock()
    return PoiCdpContinuousUiDriver(
        plan=DEFAULT_FLEET2_MISSION3_PLAN,
        fleet_states_fn=states,
        game_input=game_input or FakeInput(),
        gate=gate,
        sleep_fn=lambda seconds: None,
        monotonic_fn=clock.monotonic,
        time_ms_fn=lambda: 1_000_000,
    )


class DispatchPlanValidationTests(unittest.TestCase):
    def test_default_plan_matches_reviewed_fleet2_mission3(self):
        plan = DEFAULT_FLEET2_MISSION3_PLAN
        self.assertEqual(plan.fleet_id, 2)
        self.assertEqual(plan.mission_id, 3)
        self.assertEqual(
            [step.point.label for step in plan.steps],
            ["sortie", "expedition-mode", "mission-3-security-task",
             "mission-confirm", "expedition-start"],
        )

    def _steps(self):
        return (
            DispatchStep(RecordedPoint(1, 1, "a"), 1.0, "HOME_PORT", "SORTIE_MENU"),
            DispatchStep(RecordedPoint(2, 2, "b"), 1.0, "SORTIE_MENU", "EXPEDITION_LIST"),
            DispatchStep(RecordedPoint(3, 3, "c"), 1.0, "EXPEDITION_LIST", "EXPEDITION_ACTIVE"),
        )

    def test_plan_rejects_broken_chain(self):
        broken = list(self._steps())
        broken[1] = DispatchStep(RecordedPoint(2, 2, "b"), 1.0, "MISSION_SELECTED", "EXPEDITION_LIST")
        with self.assertRaises(ValueError):
            DispatchPlan(fleet_id=2, mission_id=3, steps=tuple(broken))

    def test_plan_requires_active_final_step(self):
        steps = list(self._steps())
        steps[-1] = DispatchStep(RecordedPoint(3, 3, "c"), 1.0, "EXPEDITION_LIST", "MISSION_SELECTED")
        with self.assertRaises(ValueError):
            DispatchPlan(fleet_id=2, mission_id=3, steps=tuple(steps))

    def test_plan_rejects_unknown_state(self):
        with self.assertRaises(ValueError):
            DispatchStep(RecordedPoint(1, 1, "a"), 1.0, "UNKNOWN", "SORTIE_MENU")

    def test_plan_rejects_negative_settle(self):
        with self.assertRaises(ValueError):
            DispatchStep(RecordedPoint(1, 1, "a"), -0.5, "HOME_PORT", "SORTIE_MENU")


class DispatchExpeditionTests(unittest.TestCase):
    def test_unsupported_fleet_fails_closed_before_any_click(self):
        states = ScriptedFleetStates((make_fleet(),))
        gate = ScriptedGate()
        game_input = FakeInput()
        driver = build_driver(states, gate, game_input)
        with self.assertRaises(ContinuousUiDriverError):
            driver.dispatch_expedition(fleet_id=3, mission_id=3)
        self.assertEqual(game_input.clicks, [])
        self.assertEqual(gate.calls, [])

    def test_unsupported_mission_fails_closed_before_any_click(self):
        states = ScriptedFleetStates((make_fleet(),))
        game_input = FakeInput()
        driver = build_driver(states, ScriptedGate(), game_input)
        with self.assertRaises(ContinuousUiDriverError):
            driver.dispatch_expedition(fleet_id=2, mission_id=5)
        self.assertEqual(game_input.clicks, [])

    def test_fleet_not_idle_blocks_dispatch(self):
        active = make_fleet(mission=(1, 3, 1_600_000, 0))
        states = ScriptedFleetStates((active,))
        game_input = FakeInput()
        driver = build_driver(states, ScriptedGate(), game_input)
        with self.assertRaisesRegex(ContinuousUiDriverError, "not idle"):
            driver.dispatch_expedition(fleet_id=2, mission_id=3)
        self.assertEqual(game_input.clicks, [])

    def test_happy_path_clicks_each_step_once_in_order(self):
        idle = make_fleet(supply=supplied_supply())
        active = make_fleet(mission=(1, 3, 1_600_000, 0), supply=supplied_supply())
        states = ScriptedFleetStates((idle,), (idle,), (active,))
        gate = ScriptedGate()
        game_input = FakeInput()
        driver = build_driver(states, gate, game_input)
        driver.dispatch_expedition(fleet_id=2, mission_id=3)
        self.assertEqual(
            game_input.clicks,
            ["sortie", "expedition-mode", "mission-3-security-task",
             "mission-confirm", "expedition-start"],
        )
        self.assertEqual(
            gate.calls,
            [
                UiState.HOME_PORT,
                UiState.SORTIE_MENU,
                UiState.SORTIE_MENU,
                UiState.EXPEDITION_LIST,
                UiState.EXPEDITION_LIST,
                UiState.MISSION_SELECTED,
                UiState.MISSION_SELECTED,
                UiState.FLEET_2_SELECTED,
                UiState.FLEET_2_SELECTED,
            ],
        )

    def test_checkpoint_failure_prevents_subsequent_clicks(self):
        idle = make_fleet()
        states = ScriptedFleetStates((idle,))
        gate = ScriptedGate(fail_counts={UiState.MISSION_SELECTED: 1})
        game_input = FakeInput()
        driver = build_driver(states, gate, game_input)
        with self.assertRaises(ContinuousUiDriverError):
            driver.dispatch_expedition(fleet_id=2, mission_id=3)
        # MISSION_SELECTED first appears as step 3's "after" checkpoint, so the
        # failure blocks step 4; the irreversible start click was never sent.
        self.assertEqual(
            game_input.clicks,
            ["sortie", "expedition-mode", "mission-3-security-task"],
        )

    def test_supply_precondition_blocks_unsupplied_fleet(self):
        unsupplied = tuple(
            PoiShipSupplyState(ship_id=ship_id, fuel=0, ammo=10, max_fuel=10, max_ammo=10)
            for ship_id in (5, 6, 7, 8)
        )
        idle = make_fleet(supply=supplied_supply())
        pre_start = make_fleet(supply=unsupplied)
        active = make_fleet(mission=(1, 3, 1_600_000, 0), supply=unsupplied)
        states = ScriptedFleetStates((idle,), (pre_start,), (active,))
        game_input = FakeInput()
        driver = build_driver(states, ScriptedGate(), game_input)
        with self.assertRaisesRegex(ContinuousUiDriverError, "not fully supplied"):
            driver.dispatch_expedition(fleet_id=2, mission_id=3)
        self.assertNotIn("expedition-start", game_input.clicks)

    def test_supply_precondition_blocks_ship_change(self):
        idle = make_fleet(supply=supplied_supply())
        changed = make_fleet(ship_ids=(5, 6, 7, 9), supply=supplied_supply((5, 6, 7, 9)))
        states = ScriptedFleetStates((idle,), (changed,))
        game_input = FakeInput()
        driver = build_driver(states, ScriptedGate(), game_input)
        with self.assertRaisesRegex(ContinuousUiDriverError, "Fleet changed"):
            driver.dispatch_expedition(fleet_id=2, mission_id=3)
        self.assertNotIn("expedition-start", game_input.clicks)

    def test_supply_precondition_blocks_missing_snapshot(self):
        idle = make_fleet(supply=supplied_supply())
        empty = make_fleet(supply=())
        states = ScriptedFleetStates((idle,), (empty,))
        driver = build_driver(states, ScriptedGate())
        with self.assertRaisesRegex(ContinuousUiDriverError, "missing or incomplete"):
            driver.dispatch_expedition(fleet_id=2, mission_id=3)

    def test_dispatch_timeout_never_repeats_clicks(self):
        idle = make_fleet(supply=supplied_supply())
        states = ScriptedFleetStates((idle,))
        game_input = FakeInput()
        driver = build_driver(states, ScriptedGate(), game_input, clock=FakeClock(step=1.0))
        with self.assertRaisesRegex(ContinuousUiDriverError, "do not repeat clicks"):
            driver.dispatch_expedition(fleet_id=2, mission_id=3)
        self.assertEqual(len(game_input.clicks), 5)


class ReturnHomePortTests(unittest.TestCase):
    def test_immediate_confirmation_returns_evidence_without_clicks(self):
        game_input = FakeInput()
        driver = build_driver(ScriptedFleetStates((make_fleet(),)), ScriptedGate(), game_input)
        evidence = driver.return_to_home_port(fleet_id=2)
        self.assertEqual(evidence.state, "HOME_PORT")
        self.assertEqual(evidence.consecutive_states, ("HOME_PORT", "HOME_PORT"))
        self.assertEqual(evidence.evidence, "stub-evidence")
        self.assertEqual(game_input.clicks, [])

    def test_bounded_recovery_clicks_then_confirms(self):
        gate = ScriptedGate(fail_counts={UiState.HOME_PORT: 1})
        game_input = FakeInput()
        driver = build_driver(ScriptedFleetStates((make_fleet(),)), gate, game_input)
        evidence = driver.return_to_home_port(fleet_id=2)
        self.assertEqual(evidence.state, "HOME_PORT")
        self.assertEqual(game_input.clicks, ["home-port"])

    def test_recovery_exhausts_attempts_fail_closed(self):
        gate = ScriptedGate(fail_counts={UiState.HOME_PORT: 99})
        game_input = FakeInput()
        driver = build_driver(ScriptedFleetStates((make_fleet(),)), gate, game_input)
        with self.assertRaisesRegex(ContinuousUiDriverError, "bounded attempts"):
            driver.return_to_home_port(fleet_id=2)
        self.assertEqual(len(game_input.clicks), 3)

    def test_rejects_invalid_fleet_id_before_any_gate_call(self):
        gate = ScriptedGate()
        driver = build_driver(ScriptedFleetStates((make_fleet(),)), gate)
        with self.assertRaises(ContinuousUiDriverError):
            driver.return_to_home_port(fleet_id=0)
        self.assertEqual(gate.calls, [])


class FakeTransport:
    def __init__(self, response=None):
        self.methods = []
        self.response = response if response is not None else {}

    def send(self, method, params):
        self.methods.append(method)
        return self.response


class FakeScreenshotInput:
    def __init__(self):
        self.captures = 0

    def capture_screenshot(self):
        self.captures += 1
        return "frame-bytes"


class ProductionStateGateTests(unittest.TestCase):
    def test_accessibility_tier_passes_without_screenshot(self):
        transport = FakeTransport(response={"nodes": []})
        gate = ProductionStateGate(
            game_transport=transport,
            screenshot_input=FakeScreenshotInput(),
            sleep_fn=lambda seconds: None,
        )
        with patch("kancollebot.adapters.poi.cdp_ui_driver.require_state") as fake_require:
            evidence = gate.require(UiState.HOME_PORT)
        self.assertEqual(evidence, "accessibility")
        self.assertEqual(transport.methods, ["Accessibility.getFullAXTree"])

    def test_checkbox_states_fail_closed(self):
        transport = FakeTransport(response={"nodes": []})
        gate = ProductionStateGate(
            game_transport=transport,
            screenshot_input=FakeScreenshotInput(),
            sleep_fn=lambda seconds: None,
            fingerprint_document={},
        )
        with patch(
            "kancollebot.adapters.poi.cdp_ui_driver.require_state",
            side_effect=UiStateGateError("not accessible"),
        ):
            with self.assertRaisesRegex(ContinuousUiDriverError, "supply module"):
                gate.require(UiState.SUPPLY_FLEET_2_SHIP_1_CHECKED)

    def test_no_evidence_source_fails_closed(self):
        transport = FakeTransport(response={"nodes": []})
        gate = ProductionStateGate(
            game_transport=transport,
            screenshot_input=FakeScreenshotInput(),
            sleep_fn=lambda seconds: None,
            fingerprint_document={},
        )
        with patch(
            "kancollebot.adapters.poi.cdp_ui_driver.require_state",
            side_effect=UiStateGateError("not accessible"),
        ):
            with self.assertRaisesRegex(ContinuousUiDriverError, "no fingerprint for this state"):
                gate.require(UiState.HOME_PORT)

    def test_fingerprint_tier_requires_two_consecutive_matches(self):
        transport = FakeTransport(response={"nodes": []})
        screenshot = FakeScreenshotInput()
        gate = ProductionStateGate(
            game_transport=transport,
            screenshot_input=screenshot,
            sleep_fn=lambda seconds: None,
            fingerprint_document={"states": {"HOME_PORT": {"any": "descriptor"}}},
        )
        with patch(
            "kancollebot.adapters.poi.cdp_ui_driver.require_state",
            side_effect=UiStateGateError("not accessible"),
        ), patch(
            "kancollebot.adapters.poi.visual_fingerprint.classify_screenshot_fingerprints",
            return_value=("HOME_PORT", {"score": 1.0}),
        ):
            evidence = gate.require(UiState.HOME_PORT)
        self.assertEqual(evidence, "two_consecutive_in_memory_ooi_fingerprints")
        self.assertEqual(screenshot.captures, 2)
        mouse_moves = transport.methods.count("Input.dispatchMouseEvent")
        self.assertEqual(mouse_moves, 2)

    def test_fingerprint_tier_fails_closed_on_confident_mismatch(self):
        transport = FakeTransport(response={"nodes": []})
        gate = ProductionStateGate(
            game_transport=transport,
            screenshot_input=FakeScreenshotInput(),
            sleep_fn=lambda seconds: None,
            fingerprint_document={"states": {"HOME_PORT": {"any": "descriptor"}}},
        )
        with patch(
            "kancollebot.adapters.poi.cdp_ui_driver.require_state",
            side_effect=UiStateGateError("not accessible"),
        ), patch(
            "kancollebot.adapters.poi.visual_fingerprint.classify_screenshot_fingerprints",
            return_value=("SORTIE_MENU", {"score": 0.9}),
        ):
            with self.assertRaisesRegex(ContinuousUiDriverError, "observed SORTIE_MENU"):
                gate.require(UiState.HOME_PORT)

    def test_template_tier_requires_two_consecutive_matches(self):
        transport = FakeTransport(response={"nodes": []})
        with tempfile.TemporaryDirectory() as directory:
            gate = ProductionStateGate(
                game_transport=transport,
                screenshot_input=FakeScreenshotInput(),
                sleep_fn=lambda seconds: None,
                fingerprint_document={},
                template_dir=directory,
            )
            with patch(
                "kancollebot.adapters.poi.cdp_ui_driver.require_state",
                side_effect=UiStateGateError("not accessible"),
            ), patch(
                "kancollebot.adapters.poi.visual_state_gate.classify_screenshot_png",
                return_value=("HOME_PORT", {"template": 1.0}),
            ):
                evidence = gate.require(UiState.HOME_PORT)
        self.assertEqual(evidence, "two_consecutive_in_memory_template_matches")

    def test_template_tier_rejects_missing_directory(self):
        transport = FakeTransport(response={"nodes": []})
        gate = ProductionStateGate(
            game_transport=transport,
            screenshot_input=FakeScreenshotInput(),
            sleep_fn=lambda seconds: None,
            fingerprint_document={},
            template_dir="Z:/definitely/not/a/directory",
        )
        with patch(
            "kancollebot.adapters.poi.cdp_ui_driver.require_state",
            side_effect=UiStateGateError("not accessible"),
        ):
            with self.assertRaisesRegex(ContinuousUiDriverError, "does not exist"):
                gate.require(UiState.HOME_PORT)


if __name__ == "__main__":
    unittest.main()
