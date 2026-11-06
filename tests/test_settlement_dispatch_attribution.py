"""P0-3 wiring: the settlement controller must attribute before it clicks.

These tests use the real controller, the real scheduler and the real SQLite
ledger; only Poi and the UI are doubles. The point is the gate, not the chain:
zero matches and two matches must both end with no UI call at all.
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
FLEET = 3
MISSION = 3
SHIPS = (3, 4, 5)
#: Both recorded times sit *before* ``BASE``, which is where every fake clock
#: starts. A confirmation later than the observation that reported the return
#: would be filtered out as future-dated, so the fixtures must be in the past.
INTENT_AT = BASE - timedelta(minutes=30)
CONFIRMED_AT = BASE - timedelta(minutes=1)


class StepClock:
    """Every read advances the clock, so freshness bounds are easy to satisfy."""

    def __init__(self, start=BASE):
        self.now = start

    def __call__(self):
        self.now = self.now + timedelta(seconds=1)
        return self.now


def returned_snapshot(fleet_id=FLEET):
    return ExpeditionFleetSnapshot(
        fleet_id=fleet_id,
        ship_ids=SHIPS,
        mission_id=MISSION,
        return_at=RETURN_AT,
        mission_status=2,
        reserved=0,
        ship_levels=(1,) * len(SHIPS),
        ship_types=(1,) * len(SHIPS),
    )


def idle_snapshot(fleet_id=FLEET):
    return ExpeditionFleetSnapshot(
        fleet_id=fleet_id,
        ship_ids=SHIPS,
        mission_id=None,
        return_at=None,
        mission_status=0,
        reserved=0,
        ship_levels=(1,) * len(SHIPS),
        ship_types=(1,) * len(SHIPS),
    )


class PairedReader:
    """Returns an observation timestamped half a second before the next read."""

    def __init__(self, clock: StepClock, *, fleet_id=FLEET):
        self.clock = clock
        self.fleet_id = fleet_id
        self.idle = False

    def read_fleets(self):
        snapshot = idle_snapshot(self.fleet_id) if self.idle else returned_snapshot(self.fleet_id)
        return ObservedState(
            (snapshot,),
            self.clock.now + timedelta(milliseconds=500),
            "poi-redux:info.fleets",
            source_freshness_verified=True,
        )


class RecordingUi:
    def __init__(self, reader: PairedReader | None = None):
        self.calls: list[tuple[str, int]] = []
        self.reader = reader

    @staticmethod
    def _evidence(state):
        return UiStateEvidence(state, (state, state), "two_consecutive_frames")

    def collect_return_result(self, *, fleet_id):
        self.calls.append(("collect_return_result", fleet_id))
        return self._evidence("RETURN_RESULT")

    def claim_bonus_items(self, *, fleet_id):
        self.calls.append(("claim_bonus_items", fleet_id))
        return self._evidence("BONUS_ITEMS")

    def claim_reward(self, *, fleet_id):
        self.calls.append(("claim_reward", fleet_id))
        if self.reader is not None:
            self.reader.idle = True
        return self._evidence("HOME_PORT")


def prepare_returned(scheduler, *, mission_id=MISSION, ships=SHIPS):
    active = MissionTuple(1, mission_id, FINAL_MS, 0, ships)
    returned = MissionTuple(2, mission_id, FINAL_MS, 0, ships)
    scheduler.mark_ready(FLEET)
    scheduler.begin_dispatch(FLEET)
    scheduler.confirm_dispatch(FLEET, active)
    scheduler.confirm_home_port_after_dispatch(
        FLEET, ui_state="HOME_PORT", observed_mission=active
    )
    scheduler.confirm_returned(FLEET, returned)


def identity(*, fleet_id=FLEET, mission_id=MISSION, ships=SHIPS) -> DispatchIdentity:
    """One pending template row; the helpers below persist it for real."""
    return DispatchIdentity(
        dispatch_id=new_dispatch_id(),
        fleet_id=fleet_id,
        mission_id=mission_id,
        ship_ids=tuple(ships),
        intent_persisted_at=INTENT_AT,
        detail="test",
    )


def persist(
    registry,
    *,
    fleet_id=FLEET,
    mission_id=MISSION,
    ships=SHIPS,
    confirmed_at=CONFIRMED_AT,
) -> DispatchIdentity:
    """Persist one dispatch instance the way production does: intent, then confirmation."""
    item = identity(fleet_id=fleet_id, mission_id=mission_id, ships=ships)
    registry.record_intent(item)
    return registry.record_confirmed(item.dispatch_id, confirmed_at=confirmed_at)


class SettlementAttributionWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "dispatch.sqlite3"
        self.registry = SqliteDispatchRegistry(self.path, task_kind="expedition")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def build(self, *, registry=True, events=None):
        clock = StepClock()
        reader = PairedReader(clock)
        ui = RecordingUi(reader)
        scheduler = ContinuousExpeditionScheduler(
            state_store=None, dispatch_fleet_ids=(FLEET,)
        )
        prepare_returned(scheduler)
        events = events if events is not None else []
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

    def test_a_single_matching_dispatch_lets_the_chain_run(self):
        item = persist(self.registry)
        controller, ui, scheduler, events = self.build()

        execution = controller.settle_one_returned(FLEET)

        self.assertEqual(execution.fleet_id, FLEET)
        self.assertEqual(
            [name for name, _ in ui.calls],
            ["collect_return_result", "claim_bonus_items", "claim_reward"],
        )
        self.assertNotIn("settlement_unattributed", [name for name, _ in events])
        # The instance is closed, so a later return cannot match it again.
        self.assertEqual(self.registry.get(item.dispatch_id).status, "settled")
        self.assertIn(scheduler.fleet(FLEET).phase, (FleetPhase.RESUPPLY_REQUIRED, FleetPhase.READY))

    def test_no_dispatch_record_means_zero_clicks(self):
        controller, ui, _scheduler, events = self.build()

        with self.assertRaisesRegex(ContinuousSettlementError, "cannot be attributed"):
            controller.settle_one_returned(FLEET)

        self.assertEqual(ui.calls, [])
        unattributed = dict(events)["settlement_unattributed"]
        self.assertEqual(unattributed["attribution_result"], "UNATTRIBUTED")
        self.assertEqual(unattributed["dispatch_clicks_sent"], 0)

    def test_two_matching_dispatches_mean_zero_clicks(self):
        # Same fleet, same mission, same composition, two separate attempts.
        for offset in (0, 30):
            persist(
                self.registry,
                confirmed_at=CONFIRMED_AT + timedelta(seconds=offset),
            )
        self.assertEqual(len(self.registry.load_for_fleet(FLEET)), 2)
        controller, ui, _scheduler, events = self.build()

        with self.assertRaisesRegex(ContinuousSettlementError, "cannot be attributed"):
            controller.settle_one_returned(FLEET)

        self.assertEqual(ui.calls, [])
        field = dict(events)["settlement_unattributed"]
        self.assertEqual(field["candidate_count"], 2)
        self.assertEqual(len(field["considered_dispatch_ids"]), 2)

    def test_a_record_with_another_ship_set_means_zero_clicks(self):
        persist(self.registry, ships=(99, 98))
        controller, ui, _scheduler, _events = self.build()

        with self.assertRaises(ContinuousSettlementError):
            controller.settle_one_returned(FLEET)
        self.assertEqual(ui.calls, [])

    def test_without_a_registry_the_legacy_behaviour_is_unchanged(self):
        controller, ui, _scheduler, events = self.build(registry=False)
        controller.settle_one_returned(FLEET)
        self.assertEqual(len(ui.calls), 3)
        self.assertNotIn("settlement_unattributed", [name for name, _ in events])

    def test_an_unreadable_ledger_is_a_refusal_not_a_guess(self):
        controller, ui, _scheduler, _events = self.build()
        self.registry.path = str(Path(self.directory.name) / "missing" / "db.sqlite3")
        with self.assertRaisesRegex(ContinuousSettlementError, "unreadable"):
            controller.settle_one_returned(FLEET)
        self.assertEqual(ui.calls, [])

    def test_the_returned_ship_set_can_come_from_the_ledger(self):
        """``_expected_ship_ids_from_registry`` is the startup entry's source."""
        persist(self.registry)
        controller, _ui, _scheduler, _events = self.build()

        ships, source, rejection = controller._expected_ship_ids_from_registry(
            FLEET, MISSION, SHIPS
        )
        self.assertEqual(ships, SHIPS)
        self.assertEqual(source, "dispatch_registry")
        self.assertIsNone(rejection)

    def test_the_ledger_source_refuses_when_two_instances_match(self):
        for offset in (0, 30):
            persist(
                self.registry,
                confirmed_at=CONFIRMED_AT + timedelta(seconds=offset),
            )
        controller, _ui, _scheduler, _events = self.build()

        ships, source, rejection = controller._expected_ship_ids_from_registry(
            FLEET, MISSION, SHIPS
        )
        self.assertIsNone(ships)
        self.assertIsNone(source)
        self.assertEqual(rejection, "UNATTRIBUTED")


class MidPhaseScreensGoneTests(unittest.TestCase):
    """A chain interrupted mid-way whose screens are gone still closes.

    Live 2026-11-06: a run died after the durable phase had advanced to
    RETURN_RESULT but before the chain finished; the screens were then
    advanced past while it was down. The already-complete close used to cover
    only RETURNED_PENDING_UI, stranding a mid-phase fleet on fail-closed.
    The scheduler invariant always allowed all three settlement phases; the
    controller condition now matches it.
    """

    class ScreensGoneUi(RecordingUi):
        def settlement_screen(self, *, fleet_id):
            self.calls.append(("settlement_screen", fleet_id))
            return None

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "dispatch.sqlite3"
        self.registry = SqliteDispatchRegistry(self.path, task_kind="expedition")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_a_return_result_phase_with_no_screens_closes_without_clicks(self):
        item = persist(self.registry)
        clock = StepClock()
        reader = PairedReader(clock)
        reader.idle = True  # the game cleared api_mission; screens are gone
        ui = self.ScreensGoneUi(reader)
        scheduler = ContinuousExpeditionScheduler(
            state_store=None, dispatch_fleet_ids=(FLEET,)
        )
        prepare_returned(scheduler)
        scheduler.confirm_return_result(FLEET, ui_state="RETURN_RESULT")
        controller = ContinuousSettlementController(
            scheduler=scheduler,
            state_reader=reader,
            ui_driver=ui,
            ui_arbiter=UiOperationArbiter(),
            event_sink=lambda name, fields: None,
            allow_ui_actions=True,
            clock=clock,
            dispatch_registry=self.registry,
        )

        execution = controller.settle_one_returned(FLEET)

        self.assertEqual(execution.fleet_id, FLEET)
        self.assertEqual(execution.reward_claimed_evidence,
                         "home_port_without_reward_screen")
        self.assertEqual(
            [call for call in ui.calls if call[0] != "settlement_screen"], []
        )
        self.assertEqual(self.registry.get(item.dispatch_id).status, "settled")
        self.assertIn(scheduler.fleet(FLEET).phase,
                      (FleetPhase.RESUPPLY_REQUIRED, FleetPhase.READY))


if __name__ == "__main__":
    unittest.main()
