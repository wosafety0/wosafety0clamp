"""Offline tests for the single-fleet resupply controller."""
from datetime import datetime, timedelta, timezone
import unittest

from kancollebot.expeditions.continuous_dispatch_controller import UiStateEvidence
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


BASE = datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc)
FLEET = 2
SHIPS = (21, 22)


def sample_fleets():
    """Fleet 2 idle with a deficit; everything else may be anywhere."""
    return [
        {"api_id": 1, "api_mission": [0, 0, 0, 0], "api_ship": [11, -1]},
        {"api_id": 2, "api_mission": [0, 0, 0, 0], "api_ship": [21, 22]},
        {"api_id": 3, "api_mission": [1, 3, 0, 0], "api_ship": [31]},
    ]


def sample_ships(*, filled):
    return {
        "11": {"api_id": 11, "api_ship_id": 101, "api_fuel": 10, "api_bull": 10},
        "21": {"api_id": 21, "api_ship_id": 102, "api_fuel": 10 if filled else 4,
               "api_bull": 10 if filled else 6},
        "22": {"api_id": 22, "api_ship_id": 103, "api_fuel": 10 if filled else 7,
               "api_bull": 10 if filled else 5},
        "31": {"api_id": 31, "api_ship_id": 104, "api_fuel": 2, "api_bull": 2},
    }


MASTERS = {
    "101": {"api_fuel_max": 10, "api_bull_max": 10},
    "102": {"api_fuel_max": 10, "api_bull_max": 10},
    "103": {"api_fuel_max": 10, "api_bull_max": 10},
    "104": {"api_fuel_max": 10, "api_bull_max": 10},
}
RESOURCES = [1000, 1000, 0, 0, 0, 0, 0, 0]


def observation(*, filled, at=BASE, fleets=None, verified=True):
    return FleetSupplyObservation(
        fleets=fleets if fleets is not None else sample_fleets(),
        ships=sample_ships(filled=filled),
        master_ships=MASTERS,
        resources=RESOURCES,
        observed_at=at,
        source_freshness_verified=verified,
    )


def to_resupply_required(scheduler):
    active = MissionTuple(1, 3, 1_000_000, 0, SHIPS)
    returned = MissionTuple(2, 3, 1_000_000, 0, SHIPS)
    scheduler.mark_ready(FLEET)
    scheduler.begin_dispatch(FLEET)
    scheduler.confirm_dispatch(FLEET, active)
    scheduler.confirm_home_port_after_dispatch(
        FLEET, ui_state="HOME_PORT", observed_mission=active
    )
    scheduler.confirm_returned(FLEET, returned)
    scheduler.confirm_return_result(FLEET, ui_state="RETURN_RESULT")
    scheduler.confirm_bonus_items(FLEET, ui_state="BONUS_ITEMS")
    scheduler.confirm_reward_claimed(FLEET, claimed=True)


class Provider:
    def __init__(self, items):
        self._items = list(items)
        self.calls = 0

    def __call__(self):
        item = self._items[min(self.calls, len(self._items) - 1)]
        self.calls += 1
        return item


class FakeUi:
    def __init__(self, state="HOME_PORT"):
        self.calls = []
        self._state = state

    def resupply_fleet(self, *, fleet_id):
        self.calls.append(fleet_id)
        return UiStateEvidence(
            state=self._state,
            consecutive_states=(self._state, self._state),
            evidence=f"{self._state}|two-frames",
        )


def build(provider, ui, *, allow=True, scheduler=None):
    scheduler = scheduler or ContinuousExpeditionScheduler()
    to_resupply_required(scheduler)
    events = []
    controller = ContinuousResupplyController(
        scheduler=scheduler,
        ui_driver=ui,
        fleet_data_provider=provider,
        ui_arbiter=UiOperationArbiter(),
        event_sink=lambda name, fields: events.append((name, dict(fields))),
        allow_ui_actions=allow,
        clock=lambda: BASE,
    )
    return controller, scheduler, events


class ResupplyControllerTests(unittest.TestCase):
    def test_ui_actions_are_disabled_by_default(self):
        ui = FakeUi()
        controller, _, _ = build(
            Provider([observation(filled=True)]), ui, allow=False
        )
        with self.assertRaisesRegex(ContinuousResupplyError, "disabled"):
            controller.resupply_one_needed(FLEET)
        self.assertEqual(ui.calls, [])

    def test_a_fleet_outside_the_dispatch_set_is_refused(self):
        ui = FakeUi()
        controller, _, _ = build(Provider([observation(filled=True)]), ui)
        with self.assertRaisesRegex(ContinuousResupplyError, "not a configured"):
            controller.resupply_one_needed(1)

    def test_fleet_4_can_be_dispatched_but_not_resupplied_without_a_visual_gate(self):
        """Promoting Fleet 4 to dispatchable must not invent supply evidence."""
        ui = FakeUi()
        scheduler = ContinuousExpeditionScheduler(
            dispatch_fleet_ids=(2, 3, 4),
            read_only_fleet_ids=(1,),
            deferred_fleet_ids=(),
        )
        controller, _, _ = build(
            Provider([observation(filled=True)]), ui, scheduler=scheduler
        )
        self.assertIn(4, scheduler.dispatch_fleet_ids)
        with self.assertRaisesRegex(
            ContinuousResupplyError, "no verified supply visual gate"
        ):
            controller.resupply_one_needed(4)
        self.assertEqual(ui.calls, [])

    def test_a_fleet_not_awaiting_resupply_is_refused(self):
        ui = FakeUi()
        controller, scheduler, _ = build(Provider([observation(filled=True)]), ui)
        scheduler.confirm_resupply(FLEET, fully_supplied=True)
        with self.assertRaisesRegex(ContinuousResupplyError, "not awaiting resupply"):
            controller.resupply_one_needed(FLEET)
        self.assertEqual(ui.calls, [])

    def test_a_fleet_that_needs_nothing_is_confirmed_without_a_click(self):
        ui = FakeUi()
        controller, scheduler, events = build(Provider([observation(filled=True)]), ui)
        result = controller.resupply_one_needed(FLEET)
        self.assertTrue(result.already_supplied)
        self.assertIsNone(result.evidence)
        self.assertEqual(ui.calls, [])
        self.assertIs(scheduler.fleet(FLEET).phase, FleetPhase.READY)
        self.assertEqual([name for name, _ in events], ["resupply_already_complete"])

    def test_a_successful_supply_verifies_and_becomes_ready(self):
        ui = FakeUi()
        provider = Provider(
            [observation(filled=False), observation(filled=True, at=BASE + timedelta(seconds=1))]
        )
        controller, scheduler, events = build(provider, ui)
        result = controller.resupply_one_needed(FLEET)
        self.assertFalse(result.already_supplied)
        self.assertEqual(result.fuel_required, 9)   # (10-4) + (10-7)
        self.assertEqual(result.ammo_required, 9)   # (10-6) + (10-5)
        self.assertEqual(ui.calls, [FLEET])
        self.assertIs(scheduler.fleet(FLEET).phase, FleetPhase.READY)
        self.assertEqual([name for name, _ in events], ["resupply_confirmed"])

    def test_a_supply_that_did_not_take_effect_blocks_the_fleet(self):
        ui = FakeUi()
        provider = Provider(
            [observation(filled=False), observation(filled=False, at=BASE + timedelta(seconds=1))]
        )
        controller, scheduler, events = build(provider, ui)
        with self.assertRaisesRegex(ContinuousResupplyError, "did not verify"):
            controller.resupply_one_needed(FLEET)
        self.assertIs(scheduler.fleet(FLEET).phase, FleetPhase.BLOCKED)
        self.assertEqual([name for name, _ in events], ["resupply_blocked"])

    def test_an_expedition_on_the_target_fleet_fails_the_preflight(self):
        ui = FakeUi()
        fleets = sample_fleets()
        fleets[1]["api_mission"] = [1, 3, 0, 0]
        controller, scheduler, _ = build(
            Provider([observation(filled=False, fleets=fleets)]), ui
        )
        with self.assertRaisesRegex(ContinuousResupplyError, "preflight failed"):
            controller.resupply_one_needed(FLEET)
        self.assertEqual(ui.calls, [])
        self.assertIs(scheduler.fleet(FLEET).phase, FleetPhase.RESUPPLY_REQUIRED)

    def test_a_stale_observation_fails_the_preflight(self):
        ui = FakeUi()
        controller, _, _ = build(
            Provider([observation(filled=False, at=BASE - timedelta(minutes=5))]), ui
        )
        with self.assertRaisesRegex(ContinuousResupplyError, "preflight failed"):
            controller.resupply_one_needed(FLEET)
        self.assertEqual(ui.calls, [])

    def test_a_snapshot_from_another_source_is_refused(self):
        ui = FakeUi()
        bad = FleetSupplyObservation(
            fleets=sample_fleets(),
            ships=sample_ships(filled=False),
            master_ships=MASTERS,
            resources=RESOURCES,
            observed_at=BASE,
            source_freshness_verified=True,
            source="scraped:guess",
        )
        controller, _, _ = build(Provider([bad]), ui)
        with self.assertRaisesRegex(ContinuousResupplyError, "must originate from Poi Redux"):
            controller.resupply_one_needed(FLEET)

    def test_an_observation_that_predates_the_click_is_refused(self):
        ui = FakeUi()
        # Both snapshots carry the same timestamp, so the post-click read never
        # crossed the action boundary and cannot prove anything.
        provider = Provider([observation(filled=False), observation(filled=True)])
        controller, scheduler, _ = build(provider, ui)
        with self.assertRaisesRegex(ContinuousResupplyError, "time boundary"):
            controller.resupply_one_needed(FLEET)
        self.assertEqual(ui.calls, [FLEET])
        self.assertIs(scheduler.fleet(FLEET).phase, FleetPhase.RESUPPLY_REQUIRED)

    def test_ambiguous_ui_evidence_is_refused(self):
        ui = FakeUi(state="EXPEDITION_LIST")
        provider = Provider(
            [observation(filled=False), observation(filled=True, at=BASE + timedelta(seconds=1))]
        )
        controller, scheduler, _ = build(provider, ui)
        with self.assertRaisesRegex(ContinuousResupplyError, "Expected HOME_PORT"):
            controller.resupply_one_needed(FLEET)
        self.assertEqual(ui.calls, [FLEET])
        self.assertIs(scheduler.fleet(FLEET).phase, FleetPhase.RESUPPLY_REQUIRED)


if __name__ == "__main__":
    unittest.main()
