"""The expedition-result witness inside the startup settlement chain.

The witness is the game's own statement of which fleet completed which
expedition. It may agree with the chain being closed, and it may refuse it;
it may never be weighed against the screen and it may never stand in for the
dispatch confirmation.
"""
import tempfile
import unittest
from datetime import datetime, timezone

from kancollebot.expeditions.continuous_scheduler import (
    ContinuousExpeditionScheduler,
    FleetPhase,
    UiOperationArbiter,
)
from kancollebot.expeditions.continuous_settlement_controller import (
    ContinuousSettlementController,
)
from kancollebot.expeditions.mission_result_witness import MissionResultWitness
from kancollebot.expeditions.startup_settlement import (
    StartupSettlementRefused,
    select_startup_screen,
)

from tests.test_continuous_settlement_controller_startup import (
    FLEET,
    OTHER,
    SHIPS,
    NoNavigationReader,
    SpySettlementUi,
    fleet_snapshot,
    make_observation,
)

NOW = datetime(2026, 11, 5, 8, 51, 59, tzinfo=timezone.utc)
OTHER_SHIPS = (111, 222, 333, 444)


def witness(
    *,
    fleet_id=FLEET,
    quest_name="練習航海",
    clear_result=2,
    ship_ids=SHIPS,
    response_received_at=NOW,
):
    return MissionResultWitness(
        fleet_id=fleet_id,
        quest_name=quest_name,
        clear_result=clear_result,
        ship_ids=tuple(ship_ids),
        requested_at=response_received_at,
        response_received_at=response_received_at,
    )


class WitnessInTheChainTests(unittest.TestCase):
    def make_controller(self, observation, ui):
        scheduler = ContinuousExpeditionScheduler(dispatch_fleet_ids=(FLEET,))
        controller = ContinuousSettlementController(
            scheduler=scheduler,
            state_reader=NoNavigationReader(observation),
            ui_driver=ui,
            ui_arbiter=UiOperationArbiter(),
            event_sink=lambda event, fields: None,
            allow_ui_actions=True,
        )
        return controller, scheduler

    def run_chain(self, controller, *, witness_value, screen="BONUS_ITEMS"):
        return controller.settle_visible_reward_chain(
            fleet_id=FLEET,
            screen=select_startup_screen(composited_state=screen),
            expected_ship_ids=SHIPS,
            expected_ship_ids_source="dispatch_intent_mission_1",
            mission_result_witness=witness_value,
        )

    def test_a_witness_that_agrees_is_recorded_and_the_chain_closes(self):
        with tempfile.TemporaryDirectory() as directory:
            ui = SpySettlementUi(displayed_screen="BONUS_ITEMS")
            poi = make_observation([fleet_snapshot(), fleet_snapshot(OTHER)])
            controller, _ = self.make_controller(poi, ui)
            result = self.run_chain(controller, witness_value=witness())
        self.assertEqual(ui.calls, [("settlement_screen", FLEET), ("claim_reward", FLEET)])
        self.assertIsNotNone(result.witness_verdict)
        self.assertTrue(result.witness_verdict.allowed)
        # Without a durable dispatch record there is nothing to cross-check
        # against, so the witness is recorded as an observation - it still
        # agrees with the fleet it is claimed for.
        self.assertEqual(
            result.witness_verdict.reason, "witness_observed_without_dispatch_record"
        )
        self.assertEqual(result.witness_verdict.fleet_id, FLEET)

    def test_without_a_witness_the_chain_is_unaffected(self):
        with tempfile.TemporaryDirectory() as directory:
            ui = SpySettlementUi(displayed_screen="BONUS_ITEMS")
            poi = make_observation([fleet_snapshot(), fleet_snapshot(OTHER)])
            controller, _ = self.make_controller(poi, ui)
            result = self.run_chain(controller, witness_value=None)
        self.assertEqual(ui.calls, [("settlement_screen", FLEET), ("claim_reward", FLEET)])
        self.assertIsNone(result.witness_verdict)

    def test_a_witness_naming_another_fleet_refuses_with_zero_clicks(self):
        with tempfile.TemporaryDirectory() as directory:
            ui = SpySettlementUi(displayed_screen="BONUS_ITEMS")
            poi = make_observation([fleet_snapshot(), fleet_snapshot(OTHER)])
            controller, _ = self.make_controller(poi, ui)
            with self.assertRaises(StartupSettlementRefused) as caught:
                self.run_chain(controller, witness_value=witness(fleet_id=OTHER))
        self.assertEqual(caught.exception.reason, "witness_fleet_mismatch")
        self.assertEqual(
            [call for call in ui.calls if call[0] != "settlement_screen"], []
        )

    def test_a_witness_with_other_ships_refuses_with_zero_clicks(self):
        with tempfile.TemporaryDirectory() as directory:
            ui = SpySettlementUi(displayed_screen="BONUS_ITEMS")
            poi = make_observation([fleet_snapshot(), fleet_snapshot(OTHER)])
            controller, _ = self.make_controller(poi, ui)
            with self.assertRaises(StartupSettlementRefused) as caught:
                self.run_chain(controller, witness_value=witness(ship_ids=OTHER_SHIPS))
        self.assertEqual(caught.exception.reason, "witness_ship_set_mismatch")
        self.assertEqual(
            [call for call in ui.calls if call[0] != "settlement_screen"], []
        )

    def test_a_witness_from_the_future_refuses(self):
        from datetime import timedelta, timezone as _timezone

        future = datetime.now(_timezone.utc) + timedelta(minutes=10)
        with tempfile.TemporaryDirectory() as directory:
            ui = SpySettlementUi(displayed_screen="BONUS_ITEMS")
            poi = make_observation([fleet_snapshot(), fleet_snapshot(OTHER)])
            controller, _ = self.make_controller(poi, ui)
            with self.assertRaises(StartupSettlementRefused) as caught:
                self.run_chain(
                    controller, witness_value=witness(response_received_at=future)
                )
        self.assertEqual(caught.exception.reason, "witness_not_yet_observed")


class WitnessAgainstDispatchEvidenceTests(unittest.TestCase):
    """A witness that disagrees with the durable dispatch refuses the chain."""

    def make_controller(self, observation, ui):
        from kancollebot.expeditions.blocked_recovery import DispatchEvidence

        scheduler = ContinuousExpeditionScheduler(dispatch_fleet_ids=(FLEET,))
        evidence = DispatchEvidence(
            fleet_id=FLEET,
            mission_id=1,
            final_time_ms=1793868719133,
            ship_ids=SHIPS,
            confirmed_at=NOW,
            home_port_verified_at=NOW,
            home_port_evidence="two_consecutive_in_memory_ooi_fingerprints",
            home_port_consecutive_states=("HOME_PORT", "HOME_PORT"),
        )
        controller = ContinuousSettlementController(
            scheduler=scheduler,
            state_reader=NoNavigationReader(observation),
            ui_driver=ui,
            ui_arbiter=UiOperationArbiter(),
            event_sink=lambda event, fields: None,
            allow_ui_actions=True,
        )
        return controller, evidence

    def run_chain(self, controller, evidence, *, witness_value):
        return controller.settle_visible_reward_chain(
            fleet_id=FLEET,
            screen=select_startup_screen(composited_state="BONUS_ITEMS"),
            expected_ship_ids=SHIPS,
            expected_ship_ids_source="dispatch_intent_mission_1",
            blocked_evidence=evidence,
            mission_result_witness=witness_value,
        )

    def test_a_witness_matching_the_dispatch_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            ui = SpySettlementUi(displayed_screen="BONUS_ITEMS")
            poi = make_observation([fleet_snapshot(), fleet_snapshot(OTHER)])
            controller, evidence = self.make_controller(poi, ui)
            result = self.run_chain(controller, evidence, witness_value=witness())
        self.assertIsNotNone(result.witness_verdict)
        self.assertTrue(result.witness_verdict.allowed)
        self.assertEqual(
            result.witness_verdict.reason, "witness_agrees_with_dispatch_record"
        )

    def test_a_witness_whose_ships_differ_from_the_dispatch_refuses(self):
        with tempfile.TemporaryDirectory() as directory:
            ui = SpySettlementUi(displayed_screen="BONUS_ITEMS")
            poi = make_observation([fleet_snapshot(), fleet_snapshot(OTHER)])
            controller, evidence = self.make_controller(poi, ui)
            with self.assertRaises(StartupSettlementRefused) as caught:
                self.run_chain(
                    controller, evidence, witness_value=witness(ship_ids=OTHER_SHIPS)
                )
        # The ship-set check runs first: it is the narrower, more specific
        # reason, and it fires before the chain is cross-checked.
        self.assertEqual(caught.exception.reason, "witness_ship_set_mismatch")
        self.assertEqual(
            [call for call in ui.calls if call[0] != "settlement_screen"], []
        )


if __name__ == "__main__":
    unittest.main()
