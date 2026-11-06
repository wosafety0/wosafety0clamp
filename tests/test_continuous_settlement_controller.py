"""Offline tests for the settlement (reward-collection) controller."""

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
    ContinuousSettlementError,
)
from kancollebot.expeditions.planner import ExpeditionFleetSnapshot


BASE = datetime(2026, 11, 4, 0, 0, 0, tzinfo=timezone.utc)
RETURN_AT = BASE + timedelta(minutes=20)
FINAL_MS = int(RETURN_AT.timestamp() * 1000)
FLEET = 3
SHIPS = (3, 4, 5)


def returned_snapshot(fleet_id=FLEET, return_at=RETURN_AT):
    return ExpeditionFleetSnapshot(
        fleet_id=fleet_id,
        ship_ids=SHIPS,
        mission_id=3,
        return_at=return_at,
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


def observation(at, fleets):
    return ObservedState(
        tuple(fleets), at, "poi-redux:info.fleets+info.ships",
        source_freshness_verified=True,
    )


def returned_mission(return_at=RETURN_AT):
    return MissionTuple(
        mission_status=2,
        mission_id=3,
        final_time_ms=int(return_at.timestamp() * 1000),
        reserved=0,
        ship_ids=SHIPS,
    )


def prepare_returned(scheduler):
    """Drive the real scheduler into RETURNED_PENDING_UI with a returned tuple."""
    active = MissionTuple(
        mission_status=1,
        mission_id=3,
        final_time_ms=FINAL_MS,
        reserved=0,
        ship_ids=SHIPS,
    )
    scheduler.mark_ready(FLEET)
    scheduler.begin_dispatch(FLEET)
    scheduler.confirm_dispatch(FLEET, active)
    scheduler.confirm_home_port_after_dispatch(
        FLEET, ui_state="HOME_PORT", observed_mission=active
    )
    scheduler.confirm_returned(FLEET, returned_mission())


class FakeClock:
    def __init__(self, *values):
        self.values = list(values)

    def __call__(self):
        if not self.values:
            raise AssertionError("unexpected extra clock read")
        return self.values.pop(0)


class FakeReader:
    def __init__(self, *observations):
        self.observations = list(observations)
        self.read_count = 0

    def read_fleets(self):
        self.read_count += 1
        if not self.observations:
            raise AssertionError("unexpected extra Poi read")
        return self.observations.pop(0)


class FakeSettlementUi:
    """Records settlement calls and returns matching two-frame evidence."""

    def __init__(self, claim_evidence_state="HOME_PORT"):
        self.calls = []
        self.claim_evidence_state = claim_evidence_state

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
        return self._evidence(self.claim_evidence_state)


class ContinuousSettlementControllerTests(unittest.TestCase):
    def make_controller(
        self,
        directory,
        reader,
        ui,
        *,
        clock=None,
        allow=True,
        events=None,
        returned=True,
    ):
        db_path = Path(directory) / "state.sqlite3"
        scheduler = ContinuousExpeditionScheduler(
            state_store=None, dispatch_fleet_ids=(FLEET,)
        )
        if returned:
            prepare_returned(scheduler)
        events = events if events is not None else []
        controller = ContinuousSettlementController(
            scheduler=scheduler,
            state_reader=reader,
            ui_driver=ui,
            ui_arbiter=UiOperationArbiter(),
            event_sink=lambda name, fields: events.append((name, dict(fields))),
            allow_ui_actions=allow,
            clock=clock
            or FakeClock(
                BASE,
                BASE + timedelta(seconds=1),
                BASE + timedelta(seconds=2),
                BASE + timedelta(seconds=3),
                BASE + timedelta(seconds=4),
                BASE + timedelta(seconds=5),
                BASE + timedelta(seconds=6),
            ),
        )
        return controller, scheduler, events

    def happy_path_fixtures(self, directory, *, returned=True):
        reader = FakeReader(
            observation(BASE - timedelta(seconds=1), [returned_snapshot()]),
            observation(BASE + timedelta(seconds=1.5), [returned_snapshot()]),
            observation(BASE + timedelta(seconds=3.5), [returned_snapshot()]),
            observation(BASE + timedelta(seconds=5.5), [idle_snapshot()]),
        )
        ui = FakeSettlementUi()
        clock = FakeClock(
            BASE,
            BASE + timedelta(seconds=1),
            BASE + timedelta(seconds=2),
            BASE + timedelta(seconds=3),
            BASE + timedelta(seconds=4),
            BASE + timedelta(seconds=5),
            BASE + timedelta(seconds=6),
        )
        return self.make_controller(
            directory, reader, ui, clock=clock, returned=returned
        ), ui

    def test_requires_explicit_ui_enablement(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, scheduler, events = self.make_controller(
                directory, FakeReader(), FakeSettlementUi(), allow=False
            )
            with self.assertRaisesRegex(
                ContinuousSettlementError, "explicit enablement is required"
            ):
                controller.settle_one_returned(FLEET)
            self.assertEqual(
                scheduler.fleet(FLEET).phase, FleetPhase.RETURNED_PENDING_UI
            )
            self.assertEqual(events, [])

    def test_refuses_fleet_outside_dispatch_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, _, _ = self.make_controller(
                directory, FakeReader(), FakeSettlementUi()
            )
            with self.assertRaisesRegex(ContinuousSettlementError, "dispatch/settlement fleet"):
                controller.settle_one_returned(2)

    def test_refuses_scheduler_phase_outside_settlement(self):
        with tempfile.TemporaryDirectory() as directory:
            reader = FakeReader(
                observation(BASE - timedelta(seconds=1), [returned_snapshot()])
            )
            controller, scheduler, _ = self.make_controller(
                directory, reader, FakeSettlementUi(), returned=False
            )
            with self.assertRaisesRegex(
                ContinuousSettlementError, "not in a settlement phase"
            ):
                controller.settle_one_returned(FLEET)
            self.assertEqual(scheduler.fleet(FLEET).phase, FleetPhase.IDLE)

    def test_an_idle_fleet_is_not_settled_while_another_return_is_pending(self):
        # Live 2026-11-05: fleet 4's result had already been accepted (so it read
        # idle) while fleet 3 was still waiting on screen. The reward screens say
        # which expedition it was, never which fleet, so the next chain settled
        # fleet 3 and the scheduler credited fleet 4.
        with tempfile.TemporaryDirectory() as directory:
            reader = FakeReader(
                observation(
                    BASE - timedelta(seconds=1),
                    [idle_snapshot(), returned_snapshot(fleet_id=4)],
                )
            )
            ui = FakeSettlementUi()
            controller, scheduler, events = self.make_controller(directory, reader, ui)

            with self.assertRaisesRegex(
                ContinuousSettlementError, "cannot be attributed"
            ):
                controller.settle_one_returned(FLEET)

            self.assertEqual(ui.calls, [])
            self.assertEqual(
                scheduler.fleet(FLEET).phase, FleetPhase.RETURNED_PENDING_UI
            )
            # The only row is the lifecycle start: the refusal happened before
            # any attribution or block could be recorded.
            self.assertEqual([name for name, _ in events], ["settlement_started"])

    def test_an_idle_fleet_alone_still_reaches_the_old_refusal(self):
        # The new guard must not swallow the ordinary path: with no other fleet
        # pending, an idle reading without a screen probe keeps the old error.
        with tempfile.TemporaryDirectory() as directory:
            reader = FakeReader(
                observation(
                    BASE - timedelta(seconds=1),
                    [idle_snapshot(), idle_snapshot(fleet_id=4)],
                )
            )
            controller, _, _ = self.make_controller(
                directory, reader, FakeSettlementUi()
            )
            with self.assertRaisesRegex(
                ContinuousSettlementError, "no returned mission tuple"
            ):
                controller.settle_one_returned(FLEET)

    def test_refuses_baseline_without_returned_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            reader = FakeReader(
                observation(BASE - timedelta(seconds=1), [idle_snapshot()])
            )
            controller, _, _ = self.make_controller(
                directory, reader, FakeSettlementUi()
            )
            with self.assertRaisesRegex(
                ContinuousSettlementError, "no returned mission tuple"
            ):
                controller.settle_one_returned(FLEET)

    def test_settles_from_returned_pending_ui_through_resupply_required(self):
        with tempfile.TemporaryDirectory() as directory:
            (controller, scheduler, events), ui = self.happy_path_fixtures(directory)

            result = controller.settle_one_returned(FLEET)

            self.assertEqual(result.fleet_id, FLEET)
            self.assertEqual(result.mission.mission_status, 2)
            self.assertEqual(result.mission.ship_ids, SHIPS)
            self.assertEqual(result.return_result_evidence, "two_consecutive_frames")
            self.assertEqual(result.bonus_items_evidence, "two_consecutive_frames")
            self.assertEqual(result.reward_claimed_evidence, "two_consecutive_frames")
            self.assertEqual(
                ui.calls,
                [
                    ("collect_return_result", FLEET),
                    ("claim_bonus_items", FLEET),
                    ("claim_reward", FLEET),
                ],
            )
            self.assertEqual(
                scheduler.fleet(FLEET).phase, FleetPhase.RESUPPLY_REQUIRED
            )
            self.assertEqual(
                [name for name, _ in events],
                [
                    # A settlement is its own lifecycle and now opens with a
                    # start row, so the chain has a beginning in the log too.
                    "settlement_started",
                    "settlement_return_result_confirmed",
                    "settlement_bonus_items_confirmed",
                    "settlement_reward_claimed",
                ],
            )

    def test_settles_from_mid_phase_return_result(self):
        with tempfile.TemporaryDirectory() as directory:
            reader = FakeReader(
                observation(BASE - timedelta(seconds=1), [returned_snapshot()]),
                observation(BASE + timedelta(seconds=1.5), [returned_snapshot()]),
                observation(BASE + timedelta(seconds=3.5), [idle_snapshot()]),
            )
            ui = FakeSettlementUi()
            clock = FakeClock(
                BASE,
                BASE + timedelta(seconds=1),
                BASE + timedelta(seconds=2),
                BASE + timedelta(seconds=3),
                BASE + timedelta(seconds=4),
            )
            controller, scheduler, _ = self.make_controller(
                directory,
                reader,
                ui,
                clock=clock,
            )
            scheduler.confirm_return_result(FLEET, ui_state="RETURN_RESULT")

            controller.settle_one_returned(FLEET)

            self.assertEqual(
                ui.calls,
                [("claim_bonus_items", FLEET), ("claim_reward", FLEET)],
            )
            self.assertEqual(
                scheduler.fleet(FLEET).phase, FleetPhase.RESUPPLY_REQUIRED
            )

    def test_mismatched_returned_identity_blocks_fleet(self):
        with tempfile.TemporaryDirectory() as directory:
            drifted = returned_snapshot(return_at=RETURN_AT + timedelta(minutes=1))
            reader = FakeReader(
                observation(BASE - timedelta(seconds=1), [drifted])
            )
            ui = FakeSettlementUi()
            controller, scheduler, events = self.make_controller(
                directory, reader, ui
            )

            with self.assertRaisesRegex(
                ContinuousSettlementError, "settlement blocked"
            ):
                controller.settle_one_returned(FLEET)

            self.assertEqual(ui.calls, [])
            self.assertEqual(scheduler.fleet(FLEET).phase, FleetPhase.BLOCKED)
            self.assertEqual(
                [name for name, _ in events],
                ["settlement_started", "settlement_blocked"],
            )

    def test_non_idle_after_reward_claim_blocks_fleet(self):
        with tempfile.TemporaryDirectory() as directory:
            reader = FakeReader(
                observation(BASE - timedelta(seconds=1), [returned_snapshot()]),
                observation(BASE + timedelta(seconds=1.5), [returned_snapshot()]),
                observation(BASE + timedelta(seconds=3.5), [returned_snapshot()]),
                observation(BASE + timedelta(seconds=5.5), [returned_snapshot()]),
            )
            ui = FakeSettlementUi()
            clock = FakeClock(
                BASE,
                BASE + timedelta(seconds=1),
                BASE + timedelta(seconds=2),
                BASE + timedelta(seconds=3),
                BASE + timedelta(seconds=4),
                BASE + timedelta(seconds=5),
                BASE + timedelta(seconds=6),
            )
            controller, scheduler, events = self.make_controller(
                directory, reader, ui, clock=clock
            )

            with self.assertRaisesRegex(
                ContinuousSettlementError, "did not become idle"
            ):
                controller.settle_one_returned(FLEET)

            self.assertEqual(scheduler.fleet(FLEET).phase, FleetPhase.BLOCKED)
            # The first two advances succeeded and were confirmed; only the
            # final idle verification blocked.
            self.assertEqual(
                [name for name, _ in events],
                [
                    # A settlement is its own lifecycle and now opens with a
                    # start row, so the chain has a beginning in the log too.
                    "settlement_started",
                    "settlement_return_result_confirmed",
                    "settlement_bonus_items_confirmed",
                    "settlement_blocked",
                ],
            )

    def test_stale_observation_refused_before_any_click(self):
        with tempfile.TemporaryDirectory() as directory:
            stale = ObservedState(
                (returned_snapshot(),),
                BASE - timedelta(hours=1),
                "poi-redux:info.fleets+info.ships",
                source_freshness_verified=True,
            )
            reader = FakeReader(stale)
            ui = FakeSettlementUi()
            controller, scheduler, _ = self.make_controller(
                directory, reader, ui
            )

            with self.assertRaisesRegex(
                ContinuousSettlementError, "not trusted"
            ):
                controller.settle_one_returned(FLEET)

            self.assertEqual(ui.calls, [])
            self.assertEqual(
                scheduler.fleet(FLEET).phase, FleetPhase.RETURNED_PENDING_UI
            )


if __name__ == "__main__":
    unittest.main()
