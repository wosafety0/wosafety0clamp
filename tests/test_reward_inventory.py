import unittest
from datetime import datetime, timedelta, timezone

from kancollebot.core.reward_inventory import (
    REWARD_INVENTORY_BASELINE_EVENT,
    RewardInventoryError,
    RewardInventorySnapshot,
    latest_reward_inventory_baseline_for_fleet,
    parse_reward_inventory_snapshot,
    reward_inventory_snapshot_fields,
    verify_reward_inventory_delta,
)

BASE = datetime(2026, 11, 6, 4, 0, 0, tzinfo=timezone.utc)


def baseline():
    return RewardInventorySnapshot(
        observed_at=BASE - timedelta(seconds=10),
        resources=(100, 200, 300, 400, 5, 6, 7, 8),
        useitems=((6, 1), (10, 2)),
    )


def credited():
    return RewardInventorySnapshot(
        observed_at=BASE + timedelta(seconds=3),
        resources=(110, 220, 330, 440, 5, 6, 7, 8),
        useitems=((6, 3), (10, 2)),
    )


class RewardInventoryTests(unittest.TestCase):
    def test_exact_resource_and_useitem_delta_is_accepted(self):
        verdict = verify_reward_inventory_delta(
            before=baseline(),
            after=credited(),
            expected_material=(10, 20, 30, 40),
            expected_items=((1, 6, 2),),
            requested_at=BASE,
            response_received_at=BASE + timedelta(seconds=1),
        )
        self.assertTrue(verdict.allowed)
        self.assertEqual(verdict.reason, "reward_inventory_matches_result_receipt")
        self.assertEqual(verdict.resource_deltas, (10, 20, 30, 40, 0, 0, 0, 0))
        self.assertEqual(verdict.useitem_deltas, ((6, 2), (10, 0)))

    def test_resource_mismatch_is_rejected(self):
        after = RewardInventorySnapshot(
            observed_at=BASE + timedelta(seconds=3),
            resources=(109, 220, 330, 440, 5, 6, 7, 8),
            useitems=((6, 3), (10, 2)),
        )
        verdict = verify_reward_inventory_delta(
            before=baseline(), after=after,
            expected_material=(10, 20, 30, 40),
            expected_items=((1, 6, 2),),
            requested_at=BASE,
            response_received_at=BASE + timedelta(seconds=1),
        )
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.reason, "reward_resource_delta_mismatch_at_index_0")

    def test_useitem_mismatch_is_rejected(self):
        after = RewardInventorySnapshot(
            observed_at=BASE + timedelta(seconds=3),
            resources=credited().resources,
            useitems=((6, 2), (10, 2)),
        )
        verdict = verify_reward_inventory_delta(
            before=baseline(), after=after,
            expected_material=(10, 20, 30, 40),
            expected_items=((1, 6, 2),),
            requested_at=BASE,
            response_received_at=BASE + timedelta(seconds=1),
        )
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.reason, "reward_useitem_delta_mismatch_for_id_6")

    def test_baseline_must_precede_natural_result_request(self):
        after = credited()
        too_late = RewardInventorySnapshot(
            observed_at=BASE + timedelta(seconds=1),
            resources=baseline().resources,
            useitems=baseline().useitems,
        )
        verdict = verify_reward_inventory_delta(
            before=too_late, after=after,
            expected_material=(10, 20, 30, 40),
            expected_items=((1, 6, 2),),
            requested_at=BASE,
            response_received_at=BASE + timedelta(seconds=2),
        )
        self.assertEqual(verdict.reason, "inventory_baseline_not_before_result_request")

    def test_post_snapshot_must_follow_result_response(self):
        too_early = RewardInventorySnapshot(
            observed_at=BASE + timedelta(milliseconds=500),
            resources=credited().resources,
            useitems=credited().useitems,
        )
        verdict = verify_reward_inventory_delta(
            before=baseline(), after=too_early,
            expected_material=(10, 20, 30, 40),
            expected_items=((1, 6, 2),),
            requested_at=BASE,
            response_received_at=BASE + timedelta(seconds=1),
        )
        self.assertEqual(verdict.reason, "inventory_read_predates_result_response")

    def test_eight_material_receipt_compares_all_resource_slots(self):
        after = RewardInventorySnapshot(
            observed_at=BASE + timedelta(seconds=3),
            resources=(101, 202, 303, 404, 6, 7, 8, 9),
            useitems=baseline().useitems,
        )
        verdict = verify_reward_inventory_delta(
            before=baseline(), after=after,
            expected_material=(1, 2, 3, 4, 1, 1, 1, 1),
            expected_items=(),
            requested_at=BASE,
            response_received_at=BASE + timedelta(seconds=1),
        )
        self.assertTrue(verdict.allowed)

    def test_no_reward_result_requires_unchanged_inventory(self):
        verdict = verify_reward_inventory_delta(
            before=baseline(),
            after=RewardInventorySnapshot(
                observed_at=BASE + timedelta(seconds=3),
                resources=baseline().resources,
                useitems=baseline().useitems,
            ),
            expected_material=(0, 0, 0, 0),
            expected_items=(),
            requested_at=BASE,
            response_received_at=BASE + timedelta(seconds=1),
        )
        self.assertTrue(verdict.allowed)

    def test_baseline_event_round_trip_and_latest_per_fleet(self):
        fields = reward_inventory_snapshot_fields(baseline(), fleet_id=3, dispatch_id="dispatch-3")
        parsed = parse_reward_inventory_snapshot(fields)
        self.assertEqual(parsed, baseline())
        events = [
            {"event": REWARD_INVENTORY_BASELINE_EVENT, "fields": fields},
            {"event": REWARD_INVENTORY_BASELINE_EVENT, "fields": {
                **fields, "fleet_id": 2,
            }},
        ]
        self.assertEqual(latest_reward_inventory_baseline_for_fleet(events, 3, "dispatch-3"), baseline())
        self.assertIsNone(latest_reward_inventory_baseline_for_fleet(events, 4, "dispatch-3"))

    def test_baseline_loader_is_scoped_to_exact_dispatch_id(self):
        fields = reward_inventory_snapshot_fields(
            baseline(), fleet_id=3, dispatch_id="dispatch-3"
        )
        self.assertIsNone(
            latest_reward_inventory_baseline_for_fleet(
                [{"event": REWARD_INVENTORY_BASELINE_EVENT, "fields": fields}],
                3,
                "dispatch-other",
            )
        )

    def test_malformed_latest_baseline_does_not_fall_back_to_older_snapshot(self):
        valid = reward_inventory_snapshot_fields(
            baseline(), fleet_id=3, dispatch_id="dispatch-3"
        )
        malformed = {**valid, "resources": [1, 2]}
        events = [
            {"event": REWARD_INVENTORY_BASELINE_EVENT, "fields": valid},
            {"event": REWARD_INVENTORY_BASELINE_EVENT, "fields": malformed},
        ]
        self.assertIsNone(
            latest_reward_inventory_baseline_for_fleet(events, 3, "dispatch-3")
        )

    def test_duplicate_or_malformed_useitem_rows_are_rejected(self):
        fields = reward_inventory_snapshot_fields(baseline(), fleet_id=3, dispatch_id="dispatch-3")
        fields["useitems"] = [
            {"api_id": 6, "api_count": 1},
            {"api_id": 6, "api_count": 2},
        ]
        with self.assertRaises(RewardInventoryError):
            parse_reward_inventory_snapshot(fields)


if __name__ == "__main__":
    unittest.main()
