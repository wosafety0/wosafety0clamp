"""Offline tests for the start-up reward-chain settlement path.

The start-up path closes a reward chain that is already on display without a
fresh port witness - the witness is unobtainable while a reward screen is up,
which is exactly the deadlock it exists to resolve (live 2026-11-05). These
tests pin the replacement contract: screen proven, fleet attributed by ship
IDs, every refusal zero-click, the freshness reader never asked to navigate.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from kancollebot.core.observed_state import ObservedState
from kancollebot.expeditions.continuous_dispatch_controller import UiStateEvidence
from kancollebot.expeditions.continuous_scheduler import (
    ContinuousExpeditionScheduler,
    FleetPhase,
    MissionTuple,
    UiOperationArbiter,
)
from kancollebot.expeditions.continuous_settlement_controller import (
    ContinuousSettlementController,
)
from kancollebot.expeditions.startup_settlement import (
    StartupSettlementRefused,
    select_startup_screen,
)
from kancollebot.expeditions.planner import ExpeditionFleetSnapshot

BASE = datetime(2026, 11, 5, 5, 0, 0, tzinfo=timezone.utc)
RETURN_AT = BASE + timedelta(minutes=43)
FINAL_MS = int(RETURN_AT.timestamp() * 1000)
FLEET = 3
OTHER = 2
SHIPS = (16374, 13350, 6, 5747)


def fleet_snapshot(fleet_id=FLEET, ship_ids=SHIPS, mission_status=0):
    returned = mission_status != 0
    return ExpeditionFleetSnapshot(
        fleet_id=fleet_id,
        ship_ids=ship_ids,
        mission_id=3 if returned else None,
        return_at=RETURN_AT if returned else None,
        mission_status=mission_status,
        reserved=0,
        ship_levels=(1,) * len(ship_ids),
        ship_types=(1,) * len(ship_ids),
    )


def make_observation(fleets):
    return ObservedState(
        tuple(fleets), BASE, "poi-redux:info.fleets+info.ships",
        source_freshness_verified=True,
    )


def returned_mission():
    return MissionTuple(
        mission_status=2, mission_id=3, final_time_ms=FINAL_MS,
        reserved=0, ship_ids=SHIPS,
    )


def prepare_returned(scheduler):
    active = MissionTuple(
        mission_status=1, mission_id=3, final_time_ms=FINAL_MS,
        reserved=0, ship_ids=SHIPS,
    )
    scheduler.mark_ready(FLEET)
    scheduler.begin_dispatch(FLEET)
    scheduler.confirm_dispatch(FLEET, active)
    scheduler.confirm_home_port_after_dispatch(
        FLEET, ui_state="HOME_PORT", observed_mission=active
    )
    scheduler.confirm_returned(FLEET, returned_mission())


class NoNavigationReader:
    """A reader whose ensure_fresh must never be called on the startup path."""

    def __init__(self, observation):
        self._observation = observation
        self.read_calls = 0

    def read_fleets(self):
        self.read_calls += 1
        return self._observation

    def ensure_fresh(self, *, origin):
        raise AssertionError(
            f"the startup settlement navigated via ensure_fresh(origin={origin})"
        )


class SpySettlementUi:
    """Records every settlement call; settlement_screen is separately settable."""

    def __init__(self, displayed_screen="BONUS_ITEMS"):
        self.calls = []
        self.displayed_screen = displayed_screen

    @staticmethod
    def _evidence(state):
        return UiStateEvidence(state, (state, state), "two_consecutive_frames")

    def settlement_screen(self, *, fleet_id):
        self.calls.append(("settlement_screen", fleet_id))
        return self.displayed_screen

    def collect_return_result(self, *, fleet_id):
        self.calls.append(("collect_return_result", fleet_id))
        return self._evidence("RETURN_RESULT")

    def claim_bonus_items(self, *, fleet_id):
        self.calls.append(("claim_bonus_items", fleet_id))
        return self._evidence("BONUS_ITEMS")

    def claim_reward(self, *, fleet_id):
        self.calls.append(("claim_reward", fleet_id))
        return self._evidence("HOME_PORT")


class StartupChainTests(unittest.TestCase):
    def make_controller(self, directory, observation, ui, *, returned=False):
        scheduler = ContinuousExpeditionScheduler(
            dispatch_fleet_ids=(FLEET,),
            state_store=None,
        )
        if returned:
            prepare_returned(scheduler)
        controller = ContinuousSettlementController(
            scheduler=scheduler,
            state_reader=NoNavigationReader(observation),
            ui_driver=ui,
            ui_arbiter=UiOperationArbiter(),
            event_sink=lambda event, fields: None,
            allow_ui_actions=True,
        )
        return controller, scheduler

    def run_chain(self, controller, *, screen="BONUS_ITEMS", expected=SHIPS):
        return controller.settle_visible_reward_chain(
            fleet_id=FLEET,
            screen=select_startup_screen(composited_state=screen),
            expected_ship_ids=expected,
            expected_ship_ids_source="dispatch_intent_mission_1",
        )

    def test_a_bonus_screen_settles_with_one_step(self):
        with tempfile.TemporaryDirectory() as directory:
            ui = SpySettlementUi(displayed_screen="BONUS_ITEMS")
            poi = make_observation([fleet_snapshot(), fleet_snapshot(OTHER)])
            controller, scheduler = self.make_controller(directory, poi, ui)
            result = self.run_chain(controller)
        self.assertEqual(ui.calls, [("settlement_screen", FLEET), ("claim_reward", FLEET)])
        self.assertEqual(result.start_screen, "BONUS_ITEMS")
        self.assertEqual(result.steps, (("HOME_PORT", "two_consecutive_frames"),))
        self.assertEqual(result.ship_ids, SHIPS)

    def test_a_return_notice_settles_the_whole_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            ui = SpySettlementUi(displayed_screen="HOME_PORT_RETURN_NOTICE")
            poi = make_observation([fleet_snapshot(), fleet_snapshot(OTHER)])
            controller, _ = self.make_controller(directory, poi, ui)
            result = self.run_chain(controller, screen="HOME_PORT_RETURN_NOTICE")
        self.assertEqual(
            [name for name, _ in ui.calls],
            ["settlement_screen", "collect_return_result", "claim_bonus_items", "claim_reward"],
        )
        self.assertEqual(
            [state for state, _ in result.steps],
            ["RETURN_RESULT", "BONUS_ITEMS", "HOME_PORT"],
        )

    def test_a_persisted_returned_mission_is_credited_after_the_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            ui = SpySettlementUi(displayed_screen="BONUS_ITEMS")
            poi = make_observation([fleet_snapshot(mission_status=2), fleet_snapshot(OTHER)])
            controller, scheduler = self.make_controller(
                directory, poi, ui, returned=True
            )
            result = self.run_chain(controller)
            state = scheduler.fleet(FLEET)
        self.assertEqual(result.mission, returned_mission())
        self.assertIs(state.phase, FleetPhase.RESUPPLY_REQUIRED)
        self.assertEqual(result.scheduler_phase_after, "RESUPPLY_REQUIRED")

    def test_without_a_durable_mission_the_scheduler_is_left_untouched(self):
        # Nothing fabricated: an uncredited chain is recorded as evidence, and
        # the monitor keeps whatever phase the durable state actually had.
        with tempfile.TemporaryDirectory() as directory:
            ui = SpySettlementUi(displayed_screen="BONUS_ITEMS")
            poi = make_observation([fleet_snapshot(), fleet_snapshot(OTHER)])
            controller, scheduler = self.make_controller(directory, poi, ui)
            result = self.run_chain(controller)
            state = scheduler.fleet(FLEET)
        self.assertIsNone(result.mission)
        self.assertIs(state.phase, FleetPhase.IDLE)
        self.assertEqual(result.scheduler_phase_after, "IDLE")

    def test_the_freshness_reader_is_never_asked_to_navigate(self):
        # The whole point of the startup path: the monitor's freshness gate is
        # not involved, and nothing may navigate while a reward screen is up.
        with tempfile.TemporaryDirectory() as directory:
            ui = SpySettlementUi(displayed_screen="BONUS_ITEMS")
            poi = make_observation([fleet_snapshot(), fleet_snapshot(OTHER)])
            controller, _ = self.make_controller(directory, poi, ui)
            self.run_chain(controller)
        self.assertEqual(controller.state_reader.read_calls, 1)


class StartupChainRefusalTests(unittest.TestCase):
    """Every refusal must leave the driver with zero advance calls."""

    def make(self, directory, observation, ui):
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

    def refuse(self, poi, ui, *, screen="BONUS_ITEMS", expected=SHIPS):
        with tempfile.TemporaryDirectory() as directory:
            controller, _ = self.make(directory, poi, ui)
            with self.assertRaises(StartupSettlementRefused) as caught:
                controller.settle_visible_reward_chain(
                    fleet_id=FLEET,
                    screen=select_startup_screen(composited_state=screen),
                    expected_ship_ids=expected,
                    expected_ship_ids_source="dispatch_intent_mission_1",
                )
        advance_calls = [
            call for call in ui.calls if call[0] != "settlement_screen"
        ]
        return caught.exception, advance_calls

    def test_a_mismatched_composition_refuses_with_zero_clicks(self):
        ui = SpySettlementUi(displayed_screen="BONUS_ITEMS")
        poi = make_observation([fleet_snapshot(), fleet_snapshot(OTHER)])
        exc, advance_calls = self.refuse(poi, ui, expected=(1, 2, 3, 4))
        self.assertEqual(exc.reason, "observed_composition_does_not_match_recorded")
        self.assertEqual(advance_calls, [])

    def test_unavailable_recorded_composition_refuses_with_zero_clicks(self):
        ui = SpySettlementUi(displayed_screen="BONUS_ITEMS")
        poi = make_observation([fleet_snapshot(), fleet_snapshot(OTHER)])
        exc, advance_calls = self.refuse(poi, ui, expected=None)
        self.assertEqual(exc.reason, "recorded_composition_unavailable")
        self.assertEqual(advance_calls, [])
        # The screen is never probed either: the gate runs first.
        self.assertEqual(ui.calls, [])

    def test_unavailable_observed_ship_ids_refuse_with_zero_clicks(self):
        ui = SpySettlementUi(displayed_screen="BONUS_ITEMS")
        poi = make_observation([fleet_snapshot(ship_ids=()), fleet_snapshot(OTHER)])
        exc, advance_calls = self.refuse(poi, ui)
        self.assertEqual(exc.reason, "observed_ship_ids_unavailable")
        self.assertEqual(advance_calls, [])

    def test_a_second_returning_fleet_refuses_with_zero_clicks(self):
        ui = SpySettlementUi(displayed_screen="BONUS_ITEMS")
        poi = make_observation([
            fleet_snapshot(),
            fleet_snapshot(OTHER, mission_status=2),
        ])
        exc, advance_calls = self.refuse(poi, ui)
        self.assertEqual(exc.reason, "other_fleets_awaiting_result")
        self.assertEqual(advance_calls, [])

    def test_a_fleet_still_on_expedition_refuses_with_zero_clicks(self):
        ui = SpySettlementUi(displayed_screen="BONUS_ITEMS")
        poi = make_observation([fleet_snapshot(mission_status=1), fleet_snapshot(OTHER)])
        exc, advance_calls = self.refuse(poi, ui)
        self.assertEqual(exc.reason, "fleet_is_still_on_expedition")
        self.assertEqual(advance_calls, [])

    def test_an_unproven_screen_refuses_with_zero_clicks(self):
        ui = SpySettlementUi(displayed_screen="BONUS_ITEMS")
        poi = make_observation([fleet_snapshot(), fleet_snapshot(OTHER)])
        exc, advance_calls = self.refuse(poi, ui, screen="HOME_PORT")
        self.assertEqual(exc.reason, "no_proven_reward_screen")
        self.assertEqual(advance_calls, [])

    def test_a_screen_that_vanished_before_the_first_click_refuses(self):
        # Re-running after the chain is done must not re-click anything: the
        # re-probe finds no reward screen and the refusal keeps it that way.
        ui = SpySettlementUi(displayed_screen=None)
        poi = make_observation([fleet_snapshot(), fleet_snapshot(OTHER)])
        exc, advance_calls = self.refuse(poi, ui)
        self.assertEqual(exc.reason, "reward_screen_no_longer_displayed")
        self.assertEqual(advance_calls, [])


class StartupChainDisabledTests(unittest.TestCase):
    def test_ui_actions_require_explicit_enablement(self):
        scheduler = ContinuousExpeditionScheduler(dispatch_fleet_ids=(FLEET,))
        controller = ContinuousSettlementController(
            scheduler=scheduler,
            state_reader=NoNavigationReader(
                make_observation([fleet_snapshot(), fleet_snapshot(OTHER)])
            ),
            ui_driver=SpySettlementUi(),
            ui_arbiter=UiOperationArbiter(),
            event_sink=lambda event, fields: None,
            allow_ui_actions=False,
        )
        with self.assertRaisesRegex(RuntimeError, "UI actions are disabled"):
            controller.settle_visible_reward_chain(
                fleet_id=FLEET,
                screen=select_startup_screen(composited_state="BONUS_ITEMS"),
                expected_ship_ids=SHIPS,
                expected_ship_ids_source="dispatch_intent_mission_1",
            )

    def test_an_unconfigured_fleet_refuses(self):
        scheduler = ContinuousExpeditionScheduler(dispatch_fleet_ids=(2,))
        controller = ContinuousSettlementController(
            scheduler=scheduler,
            state_reader=NoNavigationReader(
                make_observation([fleet_snapshot(), fleet_snapshot(OTHER)])
            ),
            ui_driver=SpySettlementUi(),
            ui_arbiter=UiOperationArbiter(),
            event_sink=lambda event, fields: None,
            allow_ui_actions=True,
        )
        with self.assertRaisesRegex(RuntimeError, "not a configured dispatch"):
            controller.settle_visible_reward_chain(
                fleet_id=FLEET,
                screen=select_startup_screen(composited_state="BONUS_ITEMS"),
                expected_ship_ids=SHIPS,
                expected_ship_ids_source="dispatch_intent_mission_1",
            )


if __name__ == "__main__":
    unittest.main()
