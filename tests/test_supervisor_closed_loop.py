"""Offline integration tests for the full supervisor closed loop.

These tests wire the REAL settlement and resupply controllers (with a scripted
state reader and a scripted supply provider) into the REAL supervisor, so the
whole ``RETURNED_PENDING_UI -> RETURN_RESULT -> BONUS_ITEMS ->
RESUPPLY_REQUIRED -> READY -> EXPEDITION_ACTIVE`` chain is exercised through
the production code paths. Only the two I/O edges are faked: the Poi state
reader and the visible UI driver.

Offline only. A green run here proves the orchestration and its guards, not
that a real client reaches these screens; that remains the Poi acceptance step.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from kancollebot.core.observed_state import ObservedState
from kancollebot.expeditions.continuous_resupply_controller import (
    ContinuousResupplyController,
    ContinuousResupplyError,
    FleetSupplyObservation,
)
from kancollebot.expeditions.continuous_scheduler import (
    ContinuousExpeditionScheduler,
    FleetPhase,
    MissionTuple,
    UiOperationArbiter,
)
from kancollebot.expeditions.continuous_settlement_controller import (
    ContinuousSettlementController,
)
from kancollebot.expeditions.continuous_supervisor import (
    ContinuousExpeditionSupervisor,
)
from kancollebot.expeditions.planner import ExpeditionFleetSnapshot
from kancollebot.adapters.poi.full_supply import (
    build_fleet_supply_plan,
    verify_fleet_supply_complete,
)
from kancollebot.expeditions.continuous_dispatch_controller import UiStateEvidence

FLEET_ID = 3
SHIP_IDS = (31, 32, 33)


class StepClock:
    """A monotonically advancing clock so observation boundaries are provable."""

    def __init__(self) -> None:
        self._now = datetime.now(timezone.utc)

    def __call__(self) -> datetime:
        self._now = self._now + timedelta(milliseconds=1)
        return self._now


def returned_snapshot(return_at: datetime) -> ExpeditionFleetSnapshot:
    return ExpeditionFleetSnapshot(
        fleet_id=FLEET_ID,
        ship_ids=SHIP_IDS,
        mission_id=3,
        return_at=return_at,
        fuel_ready=True,
        ammo_ready=True,
        damaged_ship_ids=(),
        mission_status=2,
        reserved=0,
        ship_levels=(3, 1, 1),
        ship_types=(1, 1, 1),
    )


def idle_snapshot() -> ExpeditionFleetSnapshot:
    return ExpeditionFleetSnapshot(
        fleet_id=FLEET_ID,
        ship_ids=SHIP_IDS,
        mission_id=None,
        return_at=None,
        fuel_ready=True,
        ammo_ready=True,
        damaged_ship_ids=(),
        mission_status=0,
        reserved=0,
        ship_levels=(3, 1, 1),
        ship_types=(1, 1, 1),
    )


class ScriptedReader:
    """Returns a fixed sequence of Poi observations, all source-verified."""

    def __init__(self, clock, snapshots) -> None:
        self.clock = clock
        self.snapshots = list(snapshots)
        self.reads = 0

    def read_fleets(self):
        index = min(self.reads, len(self.snapshots) - 1)
        snapshot = self.snapshots[index]
        self.reads += 1
        return ObservedState(
            (snapshot,),
            self.clock(),
            "poi-redux:closed-loop-test",
            source_freshness_verified=True,
        )


class FakeMonitor:
    """The monitor's tick is not what these tests exercise."""

    def __init__(self, scheduler, state_reader, event_sink) -> None:
        self.scheduler = scheduler
        self.state_reader = state_reader
        self.event_sink = event_sink
        self.ticks = 0

    def tick(self):
        self.ticks += 1
        return SimpleNamespace(fleets=())


class FakeClosedLoopDriver:
    """Implements both the settlement and the resupply UI driver protocols."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []
        self.resupply_result_is_verified = True

    def _evidence(self, state: str) -> UiStateEvidence:
        return UiStateEvidence(state, (state, state), f"two_frame:{state}")

    def collect_return_result(self, *, fleet_id: int) -> UiStateEvidence:
        self.calls.append(("collect_return_result", fleet_id))
        return self._evidence("RETURN_RESULT")

    def claim_bonus_items(self, *, fleet_id: int) -> UiStateEvidence:
        self.calls.append(("claim_bonus_items", fleet_id))
        return self._evidence("BONUS_ITEMS")

    def claim_reward(self, *, fleet_id: int) -> UiStateEvidence:
        self.calls.append(("claim_reward", fleet_id))
        return self._evidence("HOME_PORT")

    def resupply_fleet(self, *, fleet_id: int) -> UiStateEvidence:
        self.calls.append(("resupply_fleet", fleet_id))
        return self._evidence("HOME_PORT")


class ScriptedSupplyProvider:
    """Supplies a needs-supply snapshot first, then a fully supplied one."""

    def __init__(self, clock, *, converge: bool = True) -> None:
        self.clock = clock
        self.converge = converge
        self.calls = 0
        self.masters = {
            str(master_id): {
                "api_id": master_id,
                "api_fuel_max": 15,
                "api_bull_max": 20,
                "api_stype": 1,
            }
            for master_id in (101, 102, 103)
        }

    def _ships(self, *, supplied: bool) -> dict:
        return {
            str(ship_id): {
                "api_id": ship_id,
                "api_ship_id": master_id,
                "api_nowhp": 30,
                "api_maxhp": 30,
                "api_fuel": 15 if supplied else 6,
                "api_bull": 20 if supplied else 10,
                "api_lv": 20,
                "api_cond": 49,
            }
            for ship_id, master_id in zip(SHIP_IDS, (101, 102, 103))
        }

    def __call__(self) -> FleetSupplyObservation:
        supplied = self.calls > 0 and self.converge
        self.calls += 1
        return FleetSupplyObservation(
            fleets=[
                {
                    "api_id": FLEET_ID,
                    "api_name": "fleet-3",
                    "api_mission": [0, 0, 0, 0],
                    "api_ship": list(SHIP_IDS),
                }
            ],
            ships=self._ships(supplied=supplied),
            master_ships=self.masters,
            resources=[5000, 5000, 5000, 5000],
            observed_at=self.clock(),
            source_freshness_verified=True,
            source="poi-redux:port",
        )


class FakeDispatchController:
    def __init__(self, scheduler, state_reader, event_sink, ui_arbiter, mission) -> None:
        self.scheduler = scheduler
        self.state_reader = state_reader
        self.event_sink = event_sink
        self.ui_arbiter = ui_arbiter
        self.mission = mission
        self.allow_ui_actions = True
        self.calls: list[int] = []

    def dispatch_one_ready(self, fleet_id: int, *, dispatch_id=None):
        self.calls.append(fleet_id)
        # Same three scheduler transitions a real dispatch confirms.
        self.scheduler.begin_dispatch(fleet_id)
        self.scheduler.confirm_dispatch(fleet_id, self.mission)
        self.scheduler.confirm_home_port_after_dispatch(
            fleet_id, ui_state="HOME_PORT", observed_mission=self.mission
        )
        return SimpleNamespace(fleet_id=fleet_id)


class SupervisorClosedLoopTests(unittest.TestCase):
    def build(self, *, converge: bool = True, resupply_enabled: bool = True):
        clock = StepClock()
        return_at = datetime.now(timezone.utc) + timedelta(minutes=20)
        final_time_ms = int(return_at.timestamp() * 1000)

        scheduler = ContinuousExpeditionScheduler(
            dispatch_fleet_ids=(FLEET_ID,),
            read_only_fleet_ids=(1, 2),
            deferred_fleet_ids=(4,),
        )
        active_mission = MissionTuple(1, 3, final_time_ms, 0, SHIP_IDS)
        returned_mission = MissionTuple(2, 3, final_time_ms, 0, SHIP_IDS)
        scheduler.mark_ready(FLEET_ID)
        scheduler.begin_dispatch(FLEET_ID)
        scheduler.confirm_dispatch(FLEET_ID, active_mission)
        scheduler.confirm_home_port_after_dispatch(
            FLEET_ID, ui_state="HOME_PORT", observed_mission=active_mission
        )
        scheduler.confirm_returned(FLEET_ID, returned_mission)

        reader = ScriptedReader(
            clock,
            [returned_snapshot(return_at), returned_snapshot(return_at),
             returned_snapshot(return_at), idle_snapshot()],
        )
        events: list[tuple[str, dict]] = []
        event_sink = lambda name, fields: events.append((name, dict(fields)))
        monitor = FakeMonitor(scheduler, reader, event_sink)
        arbiter = UiOperationArbiter()
        driver = FakeClosedLoopDriver()
        provider = ScriptedSupplyProvider(clock, converge=converge)

        settlement = ContinuousSettlementController(
            scheduler=scheduler,
            state_reader=reader,
            ui_driver=driver,
            ui_arbiter=arbiter,
            event_sink=event_sink,
            allow_ui_actions=True,
            clock=clock,
        )
        resupply = ContinuousResupplyController(
            scheduler=scheduler,
            ui_driver=driver,
            fleet_data_provider=provider,
            ui_arbiter=arbiter,
            event_sink=event_sink,
            allow_ui_actions=resupply_enabled,
            clock=clock,
        )
        dispatch = FakeDispatchController(scheduler, reader, event_sink, arbiter, active_mission)
        supervisor = ContinuousExpeditionSupervisor(
            monitor=monitor,
            dispatch_controller=dispatch,
            settlement_controller=settlement,
            resupply_controller=resupply,
        )
        return supervisor, scheduler, driver, provider, dispatch, events

    def test_returned_fleet_settles_resupplies_and_redispatches_in_one_tick(self):
        supervisor, scheduler, driver, _, dispatch, events = self.build()

        result = supervisor.tick()

        self.assertIsNone(result.paused_reason)
        self.assertEqual(len(result.settlements), 1)
        self.assertEqual(result.settlements[0].fleet_id, FLEET_ID)
        self.assertEqual(len(result.resupplies), 1)
        self.assertFalse(result.resupplies[0].already_supplied)
        self.assertEqual([item.fleet_id for item in result.dispatches], [FLEET_ID])
        self.assertEqual(dispatch.calls, [FLEET_ID])
        self.assertEqual(
            [name for name, _ in driver.calls],
            ["collect_return_result", "claim_bonus_items", "claim_reward", "resupply_fleet"],
        )
        state = scheduler.fleet(FLEET_ID)
        self.assertIs(state.phase, FleetPhase.EXPEDITION_ACTIVE)
        self.assertTrue(state.home_port_verified)
        self.assertIn("resupply_confirmed", [name for name, _ in events])

    def test_resupply_disabled_fails_closed_without_dispatch(self):
        supervisor, scheduler, driver, _, dispatch, _ = self.build(resupply_enabled=False)

        result = supervisor.tick()

        self.assertEqual(result.paused_reason, f"fleet_{FLEET_ID}_resupply_pending")
        self.assertEqual(len(result.settlements), 1)
        self.assertEqual(result.resupplies, ())
        self.assertEqual(dispatch.calls, [])
        self.assertIs(scheduler.fleet(FLEET_ID).phase, FleetPhase.RESUPPLY_REQUIRED)
        self.assertNotIn(("resupply_fleet", FLEET_ID), driver.calls)

    def test_unverified_post_supply_state_blocks_and_never_redispatches(self):
        supervisor, scheduler, _, _, dispatch, events = self.build(converge=False)

        with self.assertRaises(ContinuousResupplyError):
            supervisor.tick()

        self.assertIs(scheduler.fleet(FLEET_ID).phase, FleetPhase.BLOCKED)
        self.assertEqual(dispatch.calls, [])
        self.assertIn("resupply_blocked", [name for name, _ in events])


class SupplyProviderContractTests(unittest.TestCase):
    """The provider's payload must stay consumable by the real preflight."""

    def test_scripted_provider_satisfies_real_preflight_and_verification(self):
        clock = StepClock()
        provider = ScriptedSupplyProvider(clock)
        before = provider()
        plan = build_fleet_supply_plan(
            FLEET_ID,
            before.fleets,
            before.ships,
            before.master_ships,
            before.resources,
            source_freshness_verified=before.source_freshness_verified,
            observed_at=before.observed_at,
            now=clock(),
        )
        self.assertTrue(plan.needs_supply)
        self.assertEqual(plan.fuel_required, 27)

        after = provider()
        self.assertTrue(
            verify_fleet_supply_complete(
                FLEET_ID, after.fleets, after.ships, after.master_ships
            )
        )


if __name__ == "__main__":
    unittest.main()
