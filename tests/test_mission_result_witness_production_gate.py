from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kancollebot.core.observed_state import ObservedState
from kancollebot.core.reward_inventory import RewardInventorySnapshot
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
from kancollebot.expeditions.mission_result_witness import parse_observer_witness
from kancollebot.expeditions.dispatch_registry import (
    DispatchIdentity,
    SqliteDispatchRegistry,
    new_dispatch_id,
)
from kancollebot.expeditions.planner import ExpeditionFleetSnapshot
from kancollebot.expeditions.startup_settlement import (
    StartupSettlementRefused,
    select_startup_screen,
)

BASE = datetime(2026, 11, 6, 4, 0, 0, tzinfo=timezone.utc)
FLEET = 3
MISSION = 3
SHIPS = (3, 4, 5)
RETURN_AT = BASE + timedelta(minutes=20)
FINAL_MS = int(RETURN_AT.timestamp() * 1000)
CONFIRMED_AT = BASE - timedelta(minutes=1)


def snapshot(*, returned=True):
    return ExpeditionFleetSnapshot(
        fleet_id=FLEET,
        ship_ids=SHIPS,
        mission_id=MISSION if returned else None,
        return_at=RETURN_AT if returned else None,
        mission_status=2 if returned else 0,
        reserved=0,
        ship_levels=(1, 1, 1),
        ship_types=(1, 1, 1),
    )


class StepClock:
    def __init__(self):
        self.now = BASE

    def __call__(self):
        self.now += timedelta(seconds=1)
        return self.now


class PairedReader:
    def __init__(self, clock):
        self.clock = clock
        self.idle = False

    def read_fleets(self):
        return ObservedState(
            (snapshot(returned=not self.idle),),
            self.clock.now + timedelta(milliseconds=500),
            "poi-redux:info.fleets",
            source_freshness_verified=True,
        )


class RecordingUi:
    def __init__(self, order, reader, displayed_screen="HOME_PORT"):
        self.order = order
        self.reader = reader
        self.displayed_screen = displayed_screen
        self.calls = []

    @staticmethod
    def evidence(state):
        return UiStateEvidence(state, (state, state), "two_consecutive_frames")

    def settlement_screen(self, *, fleet_id):
        self.order.append("settlement_screen")
        return self.displayed_screen

    def collect_return_result(self, *, fleet_id):
        self.order.append("collect_return_result")
        self.calls.append("collect_return_result")
        return self.evidence("RETURN_RESULT")

    def claim_bonus_items(self, *, fleet_id):
        self.order.append("claim_bonus_items")
        self.calls.append("claim_bonus_items")
        return self.evidence("BONUS_ITEMS")

    def claim_reward(self, *, fleet_id):
        self.order.append("claim_reward")
        self.calls.append("claim_reward")
        self.reader.idle = True
        return self.evidence("HOME_PORT")


class FakeInventoryProvider:
    def __init__(self, snapshots):
        self.snapshots = list(snapshots)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if not self.snapshots:
            raise AssertionError("unexpected extra reward inventory read")
        return self.snapshots.pop(0)


def inventory_baseline():
    return RewardInventorySnapshot(
        observed_at=BASE - timedelta(seconds=10),
        resources=(100, 200, 300, 400, 5, 6, 7, 8),
        useitems=((6, 1), (10, 2)),
    )


def inventory_credited():
    return RewardInventorySnapshot(
        observed_at=BASE + timedelta(seconds=3),
        resources=(110, 220, 330, 440, 5, 6, 7, 8),
        useitems=((6, 3), (10, 2)),
    )


class FakeCaptureSession:
    def __init__(self, order, payload):
        self.order = order
        self.payload = payload
        self.cancelled = False

    def wait(self):
        self.order.append("witness_wait")
        return self.payload

    def cancel(self):
        self.cancelled = True


class FakeMissionResultObserver:
    last_status = "not_started"

    def __init__(self, order, payload):
        self.order = order
        self.payload = payload
        self.session = FakeCaptureSession(order, payload)

    def arm(self):
        self.order.append("observer_armed")
        self.last_status = "armed"
        return self.session


def result_payload(*, receipt_status="complete", clear_result=2):
    return {
        "schemaVersion": 1,
        "source": {
            "kind": "cdp-network-mission-result",
            "path": "/kcsapi/api_req_mission/result",
            "host": "w14h.kancolle-server.com",
            "httpStatus": 200,
            "requestedAt": "2026-11-06T04:00:00.000Z",
            "responseReceivedAt": "2026-11-06T04:00:01.000Z",
        },
        "data": {
            "apiDeckId": FLEET,
            "apiQuestName": "練習航海",
            "apiClearResult": clear_result,
            "apiShipId": list(SHIPS),
            "apiRewardReceipt": {
                "status": receipt_status,
                "material": [10, 20, 30, 40],
                "useitemFlags": [1, 0],
                "items": [{"slot": 1, "useitemId": 6, "useitemCount": 2}],
            },
        },
        "safety": {
            "gameInputSent": False,
            "gameApiRequestInitiated": False,
            "runtimeEvaluateUsed": False,
            "rawRequestBodySaved": False,
            "rawResponseBodySaved": False,
        },
    }


class MissionResultProductionGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.registry = SqliteDispatchRegistry(
            Path(self.temp.name) / "dispatch.sqlite3", task_kind="expedition"
        )
        identity = DispatchIdentity(
            dispatch_id=new_dispatch_id(),
            fleet_id=FLEET,
            mission_id=MISSION,
            ship_ids=SHIPS,
            intent_persisted_at=BASE - timedelta(minutes=2),
            detail="production witness gate test",
        )
        self.dispatch_id = identity.dispatch_id
        self.registry.record_intent(identity)
        self.registry.record_confirmed(
            identity.dispatch_id,
            confirmed_at=CONFIRMED_AT,
            quest_name="練習航海",
            final_time_ms=FINAL_MS,
        )

    def tearDown(self):
        self.temp.cleanup()

    def build(self, *, receipt_status="complete", clear_result=2, with_registry=True):
        order = []
        clock = StepClock()
        reader = PairedReader(clock)
        ui = RecordingUi(order, reader)
        observer = FakeMissionResultObserver(
            order, result_payload(receipt_status=receipt_status, clear_result=clear_result)
        )
        inventory_provider = FakeInventoryProvider(
            [inventory_baseline(), inventory_credited()]
        )
        scheduler = ContinuousExpeditionScheduler(
            state_store=None, dispatch_fleet_ids=(FLEET,)
        )
        active = MissionTuple(1, MISSION, FINAL_MS, 0, SHIPS)
        returned = MissionTuple(2, MISSION, FINAL_MS, 0, SHIPS)
        scheduler.mark_ready(FLEET)
        scheduler.begin_dispatch(FLEET)
        scheduler.confirm_dispatch(FLEET, active)
        scheduler.confirm_home_port_after_dispatch(
            FLEET, ui_state="HOME_PORT", observed_mission=active
        )
        scheduler.confirm_returned(FLEET, returned)
        events = []
        controller = ContinuousSettlementController(
            scheduler=scheduler,
            state_reader=reader,
            ui_driver=ui,
            ui_arbiter=UiOperationArbiter(),
            event_sink=lambda name, fields: events.append((name, dict(fields))),
            allow_ui_actions=True,
            clock=clock,
            dispatch_registry=self.registry if with_registry else None,
            mission_result_observer=observer,
            mission_result_witness_loader=lambda fleet_id: None,
            require_mission_result_witness=True,
            mission_name_resolver=lambda mission_id: "????",
            reward_inventory_provider=inventory_provider,
            reward_inventory_baseline_loader=lambda fleet_id, dispatch_id: (
                inventory_baseline() if dispatch_id == self.dispatch_id else None
            ),
        )
        return controller, ui, scheduler, observer, order, events, inventory_provider

    def test_observer_is_armed_before_result_click_and_witness_before_next_click(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, ui, scheduler, observer, order, events, inventory_provider = self.build()
            execution = controller.settle_one_returned(FLEET)
        self.assertLess(order.index("observer_armed"), order.index("collect_return_result"))
        self.assertLess(order.index("collect_return_result"), order.index("witness_wait"))
        self.assertLess(order.index("witness_wait"), order.index("claim_bonus_items"))
        self.assertEqual(ui.calls, ["collect_return_result", "claim_bonus_items", "claim_reward"])
        self.assertTrue(execution.witness_verdict.allowed)
        self.assertTrue(execution.inventory_verdict.allowed)
        self.assertEqual(inventory_provider.calls, 2)
        self.assertEqual(scheduler.fleet(FLEET).phase, FleetPhase.RESUPPLY_REQUIRED)
        event_names = [name for name, _ in events]
        self.assertIn("mission_result_observed", event_names)
        self.assertIn("mission_result_witness_verified", event_names)

    def test_incomplete_receipt_stops_before_bonus_screen_click(self):
        controller, ui, scheduler, observer, order, events, inventory_provider = self.build(
            receipt_status="incomplete"
        )
        with self.assertRaisesRegex(ContinuousSettlementError, "witness_reward_receipt_incomplete"):
            controller.settle_one_returned(FLEET)
        self.assertEqual(ui.calls, ["collect_return_result"])
        self.assertEqual(scheduler.fleet(FLEET).phase, FleetPhase.RETURN_RESULT)
        self.assertIn("mission_result_observed", [name for name, _ in events])
        self.assertNotIn("settlement_bonus_items_confirmed", [name for name, _ in events])

    def test_startup_result_screen_without_persisted_witness_refuses_zero_clicks(self):
        order = []
        clock = StepClock()
        reader = PairedReader(clock)
        reader.idle = True
        ui = RecordingUi(order, reader, displayed_screen="RETURN_RESULT")
        observer = FakeMissionResultObserver(order, result_payload())
        inventory_provider = FakeInventoryProvider(
            [inventory_baseline(), inventory_credited()]
        )
        scheduler = ContinuousExpeditionScheduler(
            state_store=None, dispatch_fleet_ids=(FLEET,)
        )
        events = []
        controller = ContinuousSettlementController(
            scheduler=scheduler,
            state_reader=reader,
            ui_driver=ui,
            ui_arbiter=UiOperationArbiter(),
            event_sink=lambda name, fields: events.append((name, dict(fields))),
            allow_ui_actions=True,
            clock=clock,
            dispatch_registry=self.registry,
            mission_result_observer=observer,
            mission_result_witness_loader=lambda fleet_id: None,
            require_mission_result_witness=True,
            mission_name_resolver=lambda mission_id: "????",
            reward_inventory_provider=inventory_provider,
            reward_inventory_baseline_loader=lambda fleet_id, dispatch_id: (
                inventory_baseline() if dispatch_id == self.dispatch_id else None
            ),
        )
        with self.assertRaisesRegex(
            StartupSettlementRefused, "no durable mission-result witness"
        ):
            controller.settle_visible_reward_chain(
                fleet_id=FLEET,
                screen=select_startup_screen(composited_state="RETURN_RESULT"),
                expected_ship_ids=SHIPS,
                expected_ship_ids_source="dispatch_registry",
            )
        self.assertEqual(ui.calls, [])
        self.assertEqual(order, ["settlement_screen"])
        self.assertEqual(scheduler.fleet(FLEET).phase, FleetPhase.IDLE)

    def test_startup_result_screen_resumes_only_with_matching_witness_and_inventory(self):
        order = []
        clock = StepClock()
        reader = PairedReader(clock)
        reader.idle = True
        ui = RecordingUi(order, reader, displayed_screen="RETURN_RESULT")
        observer = FakeMissionResultObserver(order, result_payload())
        inventory_provider = FakeInventoryProvider([inventory_credited()])
        scheduler = ContinuousExpeditionScheduler(
            state_store=None, dispatch_fleet_ids=(FLEET,)
        )
        witness = parse_observer_witness(result_payload())
        controller = ContinuousSettlementController(
            scheduler=scheduler,
            state_reader=reader,
            ui_driver=ui,
            ui_arbiter=UiOperationArbiter(),
            event_sink=lambda name, fields: None,
            allow_ui_actions=True,
            clock=clock,
            dispatch_registry=self.registry,
            mission_result_observer=observer,
            mission_result_witness_loader=lambda fleet_id: witness,
            require_mission_result_witness=True,
            mission_name_resolver=lambda mission_id: "????",
            reward_inventory_provider=inventory_provider,
            reward_inventory_baseline_loader=lambda fleet_id, dispatch_id: (
                inventory_baseline() if dispatch_id == self.dispatch_id else None
            ),
        )
        result = controller.settle_visible_reward_chain(
            fleet_id=FLEET,
            screen=select_startup_screen(composited_state="RETURN_RESULT"),
            expected_ship_ids=SHIPS,
            expected_ship_ids_source="dispatch_registry",
        )
        self.assertTrue(result.witness_verdict.allowed)
        self.assertTrue(result.inventory_verdict.allowed)
        self.assertEqual(inventory_provider.calls, 1)
        self.assertEqual(ui.calls, ["claim_bonus_items", "claim_reward"])
        self.assertEqual(order, ["settlement_screen", "claim_bonus_items", "claim_reward"])

    def test_startup_return_notice_arms_observer_before_first_click(self):
        order = []
        clock = StepClock()
        reader = PairedReader(clock)
        ui = RecordingUi(order, reader, displayed_screen="HOME_PORT_RETURN_NOTICE")
        observer = FakeMissionResultObserver(order, result_payload())
        inventory_provider = FakeInventoryProvider(
            [inventory_baseline(), inventory_credited()]
        )
        scheduler = ContinuousExpeditionScheduler(
            state_store=None, dispatch_fleet_ids=(FLEET,)
        )
        events = []
        controller = ContinuousSettlementController(
            scheduler=scheduler,
            state_reader=reader,
            ui_driver=ui,
            ui_arbiter=UiOperationArbiter(),
            event_sink=lambda name, fields: events.append((name, dict(fields))),
            allow_ui_actions=True,
            clock=clock,
            dispatch_registry=self.registry,
            mission_result_observer=observer,
            mission_result_witness_loader=lambda fleet_id: None,
            require_mission_result_witness=True,
            mission_name_resolver=lambda mission_id: "????",
            reward_inventory_provider=inventory_provider,
            reward_inventory_baseline_loader=lambda fleet_id, dispatch_id: (
                inventory_baseline() if dispatch_id == self.dispatch_id else None
            ),
        )
        result = controller.settle_visible_reward_chain(
            fleet_id=FLEET,
            screen=select_startup_screen(composited_state="HOME_PORT_RETURN_NOTICE"),
            expected_ship_ids=SHIPS,
            expected_ship_ids_source="dispatch_registry",
        )
        self.assertLess(order.index("observer_armed"), order.index("collect_return_result"))
        self.assertLess(order.index("collect_return_result"), order.index("witness_wait"))
        self.assertLess(order.index("witness_wait"), order.index("claim_bonus_items"))
        self.assertTrue(result.witness_verdict.allowed)
        self.assertEqual(
            ui.calls,
            ["collect_return_result", "claim_bonus_items", "claim_reward"],
        )

    def test_missing_dispatch_registry_refuses_before_arming_or_clicking(self):
        controller, ui, scheduler, observer, order, events, inventory_provider = self.build(with_registry=False)
        with self.assertRaisesRegex(ContinuousSettlementError, "exactly one attributed confirmed dispatch"):
            controller.settle_one_returned(FLEET)
        self.assertEqual(order, [])
        self.assertEqual(ui.calls, [])
        self.assertEqual(scheduler.fleet(FLEET).phase, FleetPhase.RETURNED_PENDING_UI)


if __name__ == "__main__":
    unittest.main()
