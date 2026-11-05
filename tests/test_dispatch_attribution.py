"""P0-3: attribution of one returned expedition to exactly one dispatch.

Zero matches and several matches are the same failure: the reward screen says
which expedition, not which attempt, so neither may be credited. These tests
pin both directions down, plus the rule that an unproven dispatch can never
own a result.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from kancollebot.expeditions.dispatch_attribution import (
    UNATTRIBUTED,
    attribute_dispatch,
)
from kancollebot.expeditions.dispatch_registry import (
    DispatchIdentity,
    new_dispatch_id,
)

BASE = datetime(2026, 11, 6, 2, 0, tzinfo=timezone.utc)
FLEET = 4
MISSION = 1
SHIPS = (16374, 13350, 6, 5747)


def record(*, status="confirmed", fleet_id=FLEET, mission_id=MISSION,
           ship_ids=SHIPS, confirmed_at=None, dispatch_id=None):
    return DispatchIdentity(
        dispatch_id=dispatch_id or new_dispatch_id(),
        fleet_id=fleet_id,
        mission_id=mission_id,
        ship_ids=tuple(ship_ids),
        intent_persisted_at=BASE,
        status=status,
        confirmed_at=(
            confirmed_at
            if confirmed_at is not None
            else (BASE + timedelta(seconds=10) if status == "confirmed" else None)
        ),
        settled_at=(BASE + timedelta(hours=1) if status == "settled" else None),
        detail="test",
    )


class DispatchAttributionTests(unittest.TestCase):
    def test_one_matching_instance_is_attributed(self):
        item = record()
        verdict = attribute_dispatch(
            fleet_id=FLEET, mission_id=MISSION, ship_ids=SHIPS, records=[item]
        )
        self.assertTrue(verdict.allowed)
        self.assertEqual(verdict.dispatch_id, item.dispatch_id)
        self.assertEqual(verdict.reason, "single_matching_dispatch_instance")
        self.assertFalse(verdict.unattributed)

    def test_no_record_at_all_is_unattributed(self):
        verdict = attribute_dispatch(
            fleet_id=FLEET, mission_id=MISSION, ship_ids=SHIPS, records=[]
        )
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.reason, UNATTRIBUTED)
        self.assertTrue(verdict.unattributed)
        self.assertIsNone(verdict.dispatch_id)

    def test_a_different_ship_set_is_unattributed(self):
        verdict = attribute_dispatch(
            fleet_id=FLEET,
            mission_id=MISSION,
            ship_ids=SHIPS,
            records=[record(ship_ids=(1, 2))],
        )
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.reason, UNATTRIBUTED)

    def test_a_different_mission_is_unattributed(self):
        verdict = attribute_dispatch(
            fleet_id=FLEET,
            mission_id=MISSION,
            ship_ids=SHIPS,
            records=[record(mission_id=131)],
        )
        self.assertFalse(verdict.allowed)

    def test_two_identical_confirmed_dispatches_are_unattributed(self):
        """The dangerous case: same fleet, mission and ships, twice."""
        records = [
            record(confirmed_at=BASE + timedelta(minutes=1)),
            record(confirmed_at=BASE + timedelta(minutes=50)),
        ]
        verdict = attribute_dispatch(
            fleet_id=FLEET,
            mission_id=MISSION,
            ship_ids=SHIPS,
            records=records,
            observed_at=BASE + timedelta(hours=2),
        )
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.reason, UNATTRIBUTED)
        self.assertEqual(verdict.candidate_count, 2)
        self.assertEqual(
            set(verdict.considered_dispatch_ids),
            {item.dispatch_id for item in records},
        )

    def test_a_settled_instance_does_not_compete_with_a_new_one(self):
        settled = record(status="settled")
        active = record(confirmed_at=BASE + timedelta(minutes=30))
        verdict = attribute_dispatch(
            fleet_id=FLEET,
            mission_id=MISSION,
            ship_ids=SHIPS,
            records=[settled, active],
            observed_at=BASE + timedelta(hours=2),
        )
        self.assertTrue(verdict.allowed)
        self.assertEqual(verdict.dispatch_id, active.dispatch_id)

    def test_a_pending_or_unknown_instance_never_owns_a_result(self):
        for status in ("pending", "unknown"):
            with self.subTest(status=status):
                verdict = attribute_dispatch(
                    fleet_id=FLEET,
                    mission_id=MISSION,
                    ship_ids=SHIPS,
                    records=[record(status=status)],
                )
                self.assertFalse(verdict.allowed)
                self.assertEqual(verdict.reason, UNATTRIBUTED)

    def test_a_record_confirmed_after_the_observation_is_not_used(self):
        verdict = attribute_dispatch(
            fleet_id=FLEET,
            mission_id=MISSION,
            ship_ids=SHIPS,
            records=[record(confirmed_at=BASE + timedelta(hours=5))],
            observed_at=BASE + timedelta(hours=1),
        )
        self.assertFalse(verdict.allowed)

    def test_ship_order_does_not_change_the_identity(self):
        verdict = attribute_dispatch(
            fleet_id=FLEET,
            mission_id=MISSION,
            ship_ids=tuple(reversed(SHIPS)),
            records=[record()],
        )
        self.assertTrue(verdict.allowed)

    def test_an_empty_observation_is_a_refusal_not_a_wildcard(self):
        verdict = attribute_dispatch(
            fleet_id=FLEET, mission_id=MISSION, ship_ids=(), records=[record()]
        )
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.reason, UNATTRIBUTED)

    def test_event_fields_never_invent_a_candidate(self):
        verdict = attribute_dispatch(
            fleet_id=FLEET, mission_id=MISSION, ship_ids=SHIPS, records=[]
        )
        fields = verdict.as_event_fields()
        self.assertEqual(fields["attribution_result"], UNATTRIBUTED)
        self.assertIsNone(fields["dispatch_id"])
        self.assertEqual(fields["candidate_count"], 0)


if __name__ == "__main__":
    unittest.main()
