"""Offline tests for the start-up settlement gate.

The gate decides, from one read-only observation and one composited-layer
screen proof, whether a process may close a reward chain it found already on
display. Every refusal must be zero-click by construction, so each test pins
one refusal reason.
"""

import unittest
from datetime import datetime, timezone

from kancollebot.expeditions.continuous_scheduler import MissionTuple
from kancollebot.expeditions.planner import ExpeditionFleetSnapshot
from kancollebot.expeditions.startup_settlement import (
    REWARD_SCREEN_STATES,
    StartupAttribution,
    StartupScreen,
    StartupSettlementDecision,
    evaluate_startup_settlement,
    resolve_recorded_composition,
    select_startup_screen,
)

FLEET = 3
SHIPS = (16374, 13350, 6, 5747)


def snapshot(fleet_id=FLEET, ship_ids=SHIPS, mission_status=0):
    returned = mission_status != 0
    return ExpeditionFleetSnapshot(
        fleet_id=fleet_id,
        ship_ids=ship_ids,
        mission_id=3 if returned else None,
        return_at=datetime(2026, 11, 5, 5, 43, 18, tzinfo=timezone.utc) if returned else None,
        mission_status=mission_status,
        reserved=0,
        ship_levels=(1,) * len(ship_ids),
        ship_types=(1,) * len(ship_ids),
    )


class SelectStartupScreenTests(unittest.TestCase):
    """The composited layer decides; the default layer is audit only."""

    def test_a_proven_reward_screen_is_selected(self):
        for state in REWARD_SCREEN_STATES:
            with self.subTest(state=state):
                screen = select_startup_screen(
                    composited_state=state, default_surface_state="HOME_PORT"
                )
                self.assertEqual(screen.state, state)
                self.assertTrue(screen.is_reward_screen)

    def test_a_plain_port_is_not_a_reward_screen(self):
        screen = select_startup_screen(composited_state="HOME_PORT")
        self.assertIsNone(screen.state)
        self.assertFalse(screen.is_reward_screen)

    def test_an_unknown_frame_is_not_a_reward_screen(self):
        screen = select_startup_screen(composited_state="UNKNOWN")
        self.assertIsNone(screen.state)

    def test_the_default_layer_cannot_create_a_startup_path(self):
        # Live 2026-11-05: the default surface classified BONUS_ITEMS while the
        # composited one showed a plain port. Trusting the default layer would
        # let Home Port recovery click the chain blind, so it must not count.
        screen = select_startup_screen(
            composited_state="HOME_PORT", default_surface_state="BONUS_ITEMS"
        )
        self.assertIsNone(screen.state)

    def test_layer_disagreement_is_recorded_not_obeyed(self):
        screen = select_startup_screen(
            composited_state="HOME_PORT_RETURN_NOTICE",
            default_surface_state="HOME_PORT",
        )
        self.assertEqual(screen.state, "HOME_PORT_RETURN_NOTICE")
        self.assertTrue(screen.layers_disagree)
        self.assertEqual(screen.evidence, "composited_layer_ooi_fingerprints")

    def test_agreeing_layers_do_not_report_disagreement(self):
        screen = select_startup_screen(
            composited_state="RETURN_RESULT", default_surface_state="RETURN_RESULT"
        )
        self.assertFalse(screen.layers_disagree)

    def test_the_composited_classification_is_required(self):
        with self.assertRaises(ValueError):
            select_startup_screen(composited_state="")


class ResolveRecordedCompositionTests(unittest.TestCase):
    def test_the_scheduler_mission_wins(self):
        mission = MissionTuple(
            mission_status=2, mission_id=3, final_time_ms=1, reserved=0,
            ship_ids=SHIPS,
        )
        recorded = resolve_recorded_composition(mission=mission, intent=None)
        self.assertEqual(recorded, (SHIPS, "scheduler_mission"))

    def test_a_dispatch_intent_is_the_fallback(self):
        class Intent:
            mission_id = 1
            ship_ids = SHIPS

        recorded = resolve_recorded_composition(mission=None, intent=Intent())
        self.assertEqual(recorded, (SHIPS, "dispatch_intent_mission_1"))

    def test_no_recorded_composition_is_reported_as_none(self):
        self.assertIsNone(resolve_recorded_composition(mission=None, intent=None))

    def test_an_intent_without_ship_ids_is_not_a_record(self):
        class Intent:
            mission_id = 1
            ship_ids = ()

        self.assertIsNone(resolve_recorded_composition(mission=None, intent=Intent()))


class EvaluateStartupSettlementTests(unittest.TestCase):
    """Every refusal below must leave the driver with nothing to click."""

    def allow(self, **overrides):
        kwargs = dict(
            fleet_id=FLEET,
            screen=select_startup_screen(composited_state="BONUS_ITEMS"),
            snapshot=snapshot(),
            expected_ship_ids=SHIPS,
            expected_ship_ids_source="dispatch_intent_mission_1",
            other_fleets_awaiting_result=(),
        )
        kwargs.update(overrides)
        return evaluate_startup_settlement(**kwargs)

    def test_a_proven_screen_with_a_matching_fleet_is_allowed(self):
        decision = self.allow()
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "screen_proven_and_fleet_attributed")
        self.assertEqual(decision.attribution.observed_ship_ids, SHIPS)
        self.assertEqual(decision.attribution.expected_source, "dispatch_intent_mission_1")

    def test_an_idle_fleet_is_attributable_when_the_composition_matches(self):
        # The result was already accepted, so Poi reads idle; the recorded
        # composition is what still ties the open screen to this fleet.
        decision = self.allow(snapshot=snapshot(mission_status=0))
        self.assertTrue(decision.allowed)

    def test_no_proven_screen_refuses(self):
        decision = self.allow(
            screen=select_startup_screen(composited_state="HOME_PORT")
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "no_proven_reward_screen")

    def test_a_missing_fleet_record_refuses(self):
        decision = self.allow(snapshot=None)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "poi_reported_no_fleet_record")

    def test_unavailable_observed_ship_ids_refuse(self):
        decision = self.allow(snapshot=snapshot(ship_ids=()))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "observed_ship_ids_unavailable")

    def test_unavailable_recorded_composition_refuses(self):
        decision = self.allow(expected_ship_ids=None, expected_ship_ids_source=None)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "recorded_composition_unavailable")

    def test_a_mismatched_composition_refuses(self):
        decision = self.allow(expected_ship_ids=(1, 2, 3, 4))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "observed_composition_does_not_match_recorded")

    def test_other_fleets_awaiting_their_result_refuse(self):
        # Two unaccepted results: the screens name the expedition, not the
        # fleet, so the chain could be credited to the wrong one (live
        # 2026-11-05: fleet 3's settlement consumed fleet 2's result).
        decision = self.allow(other_fleets_awaiting_result=(2,))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "other_fleets_awaiting_result")

    def test_a_fleet_still_on_expedition_refuses(self):
        decision = self.allow(snapshot=snapshot(mission_status=1))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "fleet_is_still_on_expedition")

    def test_composition_is_compared_as_a_set(self):
        decision = self.allow(snapshot=snapshot(ship_ids=tuple(reversed(SHIPS))))
        self.assertTrue(decision.allowed)


if __name__ == "__main__":
    unittest.main()
