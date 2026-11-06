"""P0.2: the fleet-level "only one return" barrier, migrated not deleted.

The barrier existed because the reward screens name the expedition and not the
fleet: with two returns pending, the chain on screen cannot be attributed. A
global drain removes that ambiguity by clearing the pages without caring whose
they are - but only the dispatch ledger can then say which instance each
fleet's return closes, so these tests pin both directions:

* with a drain and a unique ledger attribution, both fleets settle and nothing
  is clicked twice;
* with a drain and no ledger, or with a chain still on screen, the old refusal
  stands and zero clicks are sent.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
    ContinuousSettlementError,
)
from kancollebot.expeditions.dispatch_registry import (
    DispatchIdentity,
    SqliteDispatchRegistry,
    new_dispatch_id,
)
from kancollebot.expeditions.planner import ExpeditionFleetSnapshot

BASE = datetime(2026, 11, 6, 4, 0, 0, tzinfo=timezone.utc)
RETURN_AT = BASE + timedelta(minutes=20)
FINAL_MS = int(RETURN_AT.timestamp() * 1000)
INTENT_AT = BASE - timedelta(minutes=30)
CONFIRMED_AT = BASE - timedelta(minutes=1)

#: Two fleets that came home on different expeditions with different ships.
FLEETS = {
    2: {"mission_id": 3, "ships": (3, 4, 5)},
    3: {"mission_id": 4, "ships": (6, 7, 8)},
}


class StepClock:
    def __init__(self, start=BASE):
        self.now = start

    def __call__(self):
        self.now = self.now + timedelta(seconds=1)
        return self.now


def snapshot(fleet_id, *, idle):
    info = FLEETS[fleet_id]
    if idle:
        return ExpeditionFleetSnapshot(
            fleet_id=fleet_id,
            ship_ids=info["ships"],
            mission_id=None,
            return_at=None,
            mission_status=0,
            reserved=0,
            ship_levels=(1,) * len(info["ships"]),
            ship_types=(1,) * len(info["ships"]),
        )
    return ExpeditionFleetSnapshot(
        fleet_id=fleet_id,
        ship_ids=info["ships"],
        mission_id=info["mission_id"],
        return_at=RETURN_AT,
        mission_status=2,
        reserved=0,
        ship_levels=(1,) * len(info["ships"]),
        ship_types=(1,) * len(info["ships"]),
    )


class TwoFleetReader:
    """Fleet 2 has already had its result accepted; fleet 3 still waits."""

    def __init__(self, clock):
        self.clock = clock

    def read_fleets(self):
        return ObservedState(
            (snapshot(2, idle=True), snapshot(3, idle=False)),
            self.clock.now + timedelta(milliseconds=500),
            "poi-redux:info.fleets",
            source_freshness_verified=True,
        )


class RecordingUi:
    def __init__(self, screen=None, *, probe=True):
        self.calls: list[tuple[str, int]] = []
        self.screen = screen
        # ``probe=False`` models a driver built before the read-only screen
        # probe existed: it cannot prove the chain is over.
        self.settlement_screen = self._screen if probe else None

    @staticmethod
    def _evidence(state):
        return UiStateEvidence(state, (state, state), "two_consecutive_frames")

    def _screen(self, *, fleet_id):
        # Read-only probe: it must never be a click.
        self.calls.append(("settlement_screen", fleet_id))
        return self.screen

    def collect_return_result(self, *, fleet_id):
        self.calls.append(("collect_return_result", fleet_id))
        return self._evidence("RETURN_RESULT")

    def claim_bonus_items(self, *, fleet_id):
        self.calls.append(("claim_bonus_items", fleet_id))
        return self._evidence("BONUS_ITEMS")

    def claim_reward(self, *, fleet_id):
        self.calls.append(("claim_reward", fleet_id))
        return self._evidence("HOME_PORT")


def prepare_two_returns(scheduler):
    for fleet_id, info in FLEETS.items():
        active = MissionTuple(1, info["mission_id"], FINAL_MS, 0, info["ships"])
        returned = MissionTuple(2, info["mission_id"], FINAL_MS, 0, info["ships"])
        scheduler.mark_ready(fleet_id)
        scheduler.begin_dispatch(fleet_id)
        scheduler.confirm_dispatch(fleet_id, active)
        scheduler.confirm_home_port_after_dispatch(
            fleet_id, ui_state="HOME_PORT", observed_mission=active
        )
        scheduler.confirm_returned(fleet_id, returned)


def persist(registry, fleet_id, *, confirmed=True):
    info = FLEETS[fleet_id]
    dispatch_id = new_dispatch_id()
    registry.record_intent(
        DispatchIdentity(
            dispatch_id=dispatch_id,
            fleet_id=fleet_id,
            mission_id=info["mission_id"],
            ship_ids=info["ships"],
            intent_persisted_at=INTENT_AT,
            detail="test",
        )
    )
    if confirmed:
        registry.record_confirmed(dispatch_id, confirmed_at=CONFIRMED_AT)
    return dispatch_id


class GlobalDrainBarrierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "dispatch.sqlite3"
        self.registry = SqliteDispatchRegistry(self.path, task_kind="expedition")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def build(self, *, registry=True, screen=None, probe=True):
        clock = StepClock()
        reader = TwoFleetReader(clock)
        ui = RecordingUi(screen=screen, probe=probe)
        scheduler = ContinuousExpeditionScheduler(
            state_store=None, dispatch_fleet_ids=(2, 3)
        )
        prepare_two_returns(scheduler)
        events: list[tuple[str, dict]] = []
        controller = ContinuousSettlementController(
            scheduler=scheduler,
            state_reader=reader,
            ui_driver=ui,
            ui_arbiter=UiOperationArbiter(),
            event_sink=lambda name, fields: events.append((name, dict(fields))),
            allow_ui_actions=True,
            clock=clock,
            dispatch_registry=self.registry if registry else None,
        )
        return controller, ui, scheduler, events

    def test_a_drained_chain_with_a_unique_ledger_row_settles_without_clicks(self):
        fleet2 = persist(self.registry, 2)
        persist(self.registry, 3)
        controller, ui, scheduler, events = self.build()

        execution = controller.settle_one_returned(2, pages_drained_globally=True)

        self.assertEqual(execution.fleet_id, 2)
        # The only driver call allowed is the read-only screen probe.
        self.assertEqual([name for name, _ in ui.calls], ["settlement_screen"])
        names = [name for name, _ in events]
        self.assertIn("settlement_screens_gone_after_global_drain", names)
        self.assertIn("settlement_pages_drained_globally", names)
        self.assertNotIn("settlement_unattributed", names)
        self.assertEqual(self.registry.get(fleet2).status, "settled")
        self.assertEqual(scheduler.fleet(2).phase, FleetPhase.RESUPPLY_REQUIRED)
        # Fleet 3 is untouched: its own return still has to be accounted for.
        self.assertEqual(scheduler.fleet(3).phase, FleetPhase.RETURNED_PENDING_UI)

    def test_both_fleets_settle_from_the_same_drain(self):
        ids = {fleet_id: persist(self.registry, fleet_id) for fleet_id in (2, 3)}
        controller, ui, _scheduler, _events = self.build()

        controller.settle_one_returned(2, pages_drained_globally=True)
        controller.settle_one_returned(3, pages_drained_globally=True)

        self.assertEqual(
            [name for name, _ in ui.calls],
            ["settlement_screen", "settlement_screen"],
        )
        for dispatch_id in ids.values():
            self.assertEqual(self.registry.get(dispatch_id).status, "settled")

    def test_without_a_ledger_the_old_refusal_stands(self):
        controller, ui, _scheduler, _events = self.build(registry=False)

        with self.assertRaisesRegex(
            ContinuousSettlementError, "no dispatch ledger is wired"
        ):
            controller.settle_one_returned(2, pages_drained_globally=True)

        self.assertEqual([name for name, _ in ui.calls], ["settlement_screen"])

    def test_a_chain_still_on_screen_is_a_stop_not_a_second_run(self):
        persist(self.registry, 2)
        controller, ui, _scheduler, events = self.build(screen="BONUS_ITEMS")

        with self.assertRaisesRegex(ContinuousSettlementError, "still shows BONUS_ITEMS"):
            controller.settle_one_returned(2, pages_drained_globally=True)

        self.assertEqual([name for name, _ in ui.calls], ["settlement_screen"])
        self.assertNotIn(
            "settlement_screens_gone_after_global_drain",
            [name for name, _ in events],
        )

    def test_a_driver_that_cannot_prove_absence_is_refused(self):
        persist(self.registry, 2)
        controller, ui, _scheduler, _events = self.build(probe=False)

        with self.assertRaisesRegex(
            ContinuousSettlementError, "cannot prove the settlement screens are gone"
        ):
            controller.settle_one_returned(2, pages_drained_globally=True)
        self.assertEqual([name for name, _ in ui.calls], [])

    def test_without_the_flag_the_fleet_barrier_is_unchanged(self):
        persist(self.registry, 2)
        persist(self.registry, 3)
        controller, ui, _scheduler, _events = self.build()

        with self.assertRaisesRegex(
            ContinuousSettlementError, "cannot be attributed to one fleet"
        ):
            controller.settle_one_returned(2)

        self.assertEqual([name for name, _ in ui.calls], [])

    def test_an_unattributed_drain_still_advances_the_fleet(self):
        # P0.2 red line: the ledger has no row for fleet 2 (the dispatch event
        # was never recorded, or ties with another), so attribution is
        # UNATTRIBUTED. With a global drain the pages are already gone, so the
        # settlement must be tagged and continued, never stranded.
        controller, ui, scheduler, events = self.build()

        execution = controller.settle_one_returned(2, pages_drained_globally=True)

        self.assertEqual(execution.fleet_id, 2)
        # Only the read-only screen probe may run; no reward click is sent.
        self.assertEqual([name for name, _ in ui.calls], ["settlement_screen"])
        names = [name for name, _ in events]
        self.assertIn("settlement_pages_drained_globally", names)
        self.assertIn("settlement_unattributed", names)
        # The fleet is advanced to RESUPPLY_REQUIRED despite the open ledger.
        self.assertEqual(scheduler.fleet(2).phase, FleetPhase.RESUPPLY_REQUIRED)
        # Fleet 3 is untouched: its own return still has to be accounted for.
        self.assertEqual(scheduler.fleet(3).phase, FleetPhase.RETURNED_PENDING_UI)

    def test_an_unattributed_drain_without_the_flag_still_refuses(self):
        # The legacy per-fleet path keeps its fail-closed refusal: there the
        # clicks have not happened yet, so an ambiguous attribution must wait.
        controller, ui, _scheduler, _events = self.build()

        with self.assertRaises(ContinuousSettlementError):
            controller.settle_one_returned(2)

        self.assertEqual([name for name, _ in ui.calls], [])


if __name__ == "__main__":
    unittest.main()
