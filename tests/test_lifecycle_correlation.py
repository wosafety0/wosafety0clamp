"""Stage 5: one settlement round and one supply round are separate lifecycles.

Two rows with the same ``fleet_id`` and the same mission are indistinguishable
from one another without a per-round identity, which is exactly the merge the
handover forbids. The boundary already exists (``ui_arbiter.operation``), so no
new concept is introduced: each round binds its own ``correlation_id`` and tags
its rows with a ``lifecycle_kind``.

The tests also pin what is *not* attached: no ``dispatch_id``. A return can be
settled by a process that never saw its dispatch, so writing one into the row
would claim a causal link the process cannot know. The durable link between the
two is the dispatch ledger.
"""
from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from kancollebot.expeditions.continuous_dispatch_controller import UiStateEvidence
from kancollebot.expeditions.continuous_resupply_controller import (
    ContinuousResupplyController,
    FleetSupplyObservation,
)
from kancollebot.expeditions.continuous_scheduler import (
    ContinuousExpeditionScheduler,
    MissionTuple,
    UiOperationArbiter,
)
from kancollebot.expeditions.continuous_settlement_controller import (
    ContinuousSettlementController,
)
from kancollebot.expeditions.startup_settlement import select_startup_screen


BASE = datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc)
FLEET = 2
SHIPS = (21, 22)


class CorrelationSink:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []
        self._bound: dict[str, object] = {}

    def __call__(self, event, fields):
        row = {"event": event}
        row.update(dict(fields))
        row.update(self._bound)
        self.rows.append(row)

    @contextmanager
    def correlation(self, **updates):
        saved = dict(self._bound)
        for key, value in updates.items():
            if value is None:
                self._bound.pop(key, None)
            else:
                self._bound[key] = value
        try:
            yield
        finally:
            self._bound = saved

    def rows_for(self, event: str) -> list[dict[str, object]]:
        return [row for row in self.rows if row["event"] == event]

    def one(self, event: str) -> dict[str, object]:
        rows = self.rows_for(event)
        assert len(rows) == 1, f"expected one {event}, got {rows}"
        return rows[0]


# -- resupply: a real controller, a real scheduler --------------------------


def _fleets():
    return [
        {"api_id": 1, "api_mission": [0, 0, 0, 0], "api_ship": [11, -1]},
        {"api_id": 2, "api_mission": [0, 0, 0, 0], "api_ship": [21, 22]},
        {"api_id": 3, "api_mission": [1, 3, 0, 0], "api_ship": [31]},
    ]


def _ships(*, filled):
    return {
        "11": {"api_id": 11, "api_ship_id": 101, "api_fuel": 10, "api_bull": 10},
        "21": {"api_id": 21, "api_ship_id": 102, "api_fuel": 10 if filled else 4,
               "api_bull": 10 if filled else 6},
        "22": {"api_id": 22, "api_ship_id": 103, "api_fuel": 10 if filled else 7,
               "api_bull": 10 if filled else 5},
        "31": {"api_id": 31, "api_ship_id": 104, "api_fuel": 2, "api_bull": 2},
    }


_MASTERS = {
    "101": {"api_fuel_max": 10, "api_bull_max": 10},
    "102": {"api_fuel_max": 10, "api_bull_max": 10},
    "103": {"api_fuel_max": 10, "api_bull_max": 10},
    "104": {"api_fuel_max": 10, "api_bull_max": 10},
}


def _observation(*, filled, at=BASE):
    return FleetSupplyObservation(
        fleets=_fleets(),
        ships=_ships(filled=filled),
        master_ships=_MASTERS,
        resources=[1000, 1000, 0, 0, 0, 0, 0, 0],
        observed_at=at,
        source_freshness_verified=True,
    )


class _Provider:
    def __init__(self, items):
        self._items = list(items)
        self.calls = 0

    def __call__(self):
        item = self._items[min(self.calls, len(self._items) - 1)]
        self.calls += 1
        return item


class _FakeUi:
    def __init__(self, state="HOME_PORT"):
        self.calls: list[int] = []
        self._state = state

    def resupply_fleet(self, *, fleet_id):
        self.calls.append(fleet_id)
        return UiStateEvidence(
            state=self._state,
            consecutive_states=(self._state, self._state),
            evidence=f"{self._state}|two-frames",
        )


def _to_resupply_required(scheduler):
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


def _supply_a_fleet():
    sink = CorrelationSink()
    scheduler = ContinuousExpeditionScheduler()
    _to_resupply_required(scheduler)
    provider = _Provider(
        [_observation(filled=False), _observation(filled=True, at=BASE + timedelta(seconds=1))]
    )
    controller = ContinuousResupplyController(
        scheduler=scheduler,
        ui_driver=_FakeUi(),
        fleet_data_provider=provider,
        ui_arbiter=UiOperationArbiter(),
        event_sink=sink,
        allow_ui_actions=True,
        clock=lambda: BASE,
    )
    controller.resupply_one_needed(FLEET)
    return sink


class ResupplyLifecycleTests(unittest.TestCase):
    def test_every_row_of_one_supply_shares_its_correlation_id(self):
        sink = _supply_a_fleet()

        started = sink.one("resupply_action_started")
        confirmed = sink.one("resupply_confirmed")

        self.assertEqual(started["lifecycle_kind"], "resupply")
        self.assertIsNotNone(started["correlation_id"])
        self.assertEqual(started["correlation_id"], confirmed["correlation_id"])
        # A supply happens after the dispatch's reward chain is closed, so it
        # must not borrow that dispatch's identity.
        self.assertNotIn("dispatch_id", started)
        self.assertNotIn("dispatch_id", confirmed)

    def test_two_supply_rounds_get_two_correlation_ids(self):
        first = _supply_a_fleet().one("resupply_action_started")
        second = _supply_a_fleet().one("resupply_action_started")

        self.assertNotEqual(first["correlation_id"], second["correlation_id"])


# -- settlement -------------------------------------------------------------


def _settlement_controller(*, allow_ui_actions=True):
    sink = CorrelationSink()
    controller = ContinuousSettlementController(
        scheduler=ContinuousExpeditionScheduler(),
        # A state reader that cannot answer: the lifecycle must still be opened
        # and closed around the attempt, exactly like the previous round.
        state_reader=object(),
        ui_driver=_FakeUi(),
        ui_arbiter=UiOperationArbiter(),
        event_sink=sink,
        allow_ui_actions=allow_ui_actions,
        clock=lambda: BASE,
    )
    return controller, sink


class SettlementLifecycleTests(unittest.TestCase):
    def test_each_round_opens_its_own_lifecycle(self):
        controller, sink = _settlement_controller()

        for _ in range(2):
            with self.assertRaises(Exception):
                controller.settle_one_returned(FLEET)

        started = sink.rows_for("settlement_started")
        self.assertEqual(len(started), 2)
        self.assertEqual(started[0]["lifecycle_kind"], "settlement")
        self.assertNotEqual(started[0]["correlation_id"], started[1]["correlation_id"])
        for row in started:
            self.assertIsNotNone(row["correlation_id"])
            self.assertNotIn("dispatch_id", row)
            self.assertIs(row["game_api_requests"], False)

    def test_the_start_row_records_whether_a_global_drain_preceded_it(self):
        controller, sink = _settlement_controller()

        with self.assertRaises(Exception):
            controller.settle_one_returned(FLEET, pages_drained_globally=True)

        self.assertIs(sink.one("settlement_started")["pages_drained_globally"], True)


class StartupSettlementLifecycleTests(unittest.TestCase):
    def _screen(self):
        return select_startup_screen(composited_state="BONUS_ITEMS")

    def test_the_startup_entry_opens_its_own_lifecycle(self):
        controller, sink = _settlement_controller()

        with self.assertRaises(Exception):
            controller.settle_visible_reward_chain(
                fleet_id=FLEET,
                screen=self._screen(),
                expected_ship_ids=SHIPS,
                expected_ship_ids_source="dispatch_intent_mission_1",
            )

        entry = sink.one("startup_settlement_entry")
        self.assertEqual(entry["lifecycle_kind"], "settlement")
        self.assertEqual(entry["entry"], "visible_reward_chain")
        self.assertEqual(entry["fleet_id"], FLEET)
        # The screen label is the proven state, never a dataclass repr.
        self.assertEqual(entry["screen"], "BONUS_ITEMS")
        self.assertIsNotNone(entry["correlation_id"])
        self.assertNotIn("dispatch_id", entry)
        self.assertIs(entry["game_api_requests"], False)
        self.assertIs(entry["automatic_retry"], False)

    def test_two_startup_rounds_get_two_correlation_ids(self):
        first_controller, first_sink = _settlement_controller()
        second_controller, second_sink = _settlement_controller()

        for controller, sink in (
            (first_controller, first_sink),
            (second_controller, second_sink),
        ):
            with self.assertRaises(Exception):
                controller.settle_visible_reward_chain(
                    fleet_id=FLEET,
                    screen=self._screen(),
                    expected_ship_ids=SHIPS,
                    expected_ship_ids_source="dispatch_intent_mission_1",
                )

        self.assertNotEqual(
            first_sink.one("startup_settlement_entry")["correlation_id"],
            second_sink.one("startup_settlement_entry")["correlation_id"],
        )

    def test_a_refusal_before_the_lifecycle_still_states_its_reason(self):
        controller, sink = _settlement_controller(allow_ui_actions=False)

        with self.assertRaisesRegex(Exception, "UI actions are disabled"):
            controller.settle_visible_reward_chain(
                fleet_id=FLEET,
                screen=self._screen(),
                expected_ship_ids=SHIPS,
                expected_ship_ids_source="dispatch_intent_mission_1",
            )

        refused = sink.one("startup_settlement_refused")
        self.assertEqual(refused["failed_check"], "ui_actions_disabled")
        self.assertIn("UI actions are disabled", str(refused["rejection_reason"]))
        self.assertEqual(refused["recovery_result"], "refused")
        self.assertIs(refused["automatic_retry"], False)
        # No chain was opened, so no row may claim it started.
        self.assertEqual(sink.rows_for("startup_settlement_started"), [])
        self.assertEqual(sink.rows_for("startup_settlement_entry"), [])


if __name__ == "__main__":
    unittest.main()
