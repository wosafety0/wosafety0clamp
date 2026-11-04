from __future__ import annotations

import unittest

from kancollebot.expeditions.fleet_availability import (
    FleetAvailabilityError,
    normalize_unlocked_fleet_ids,
    resolve_fleet_availability,
)


class NormalizeUnlockedFleetIdsTests(unittest.TestCase):
    def test_accepts_accounts_with_two_three_or_four_unlocked_fleets(self):
        for unlocked in ((1, 2), (1, 2, 3), (1, 2, 3, 4)):
            with self.subTest(unlocked=unlocked):
                self.assertEqual(normalize_unlocked_fleet_ids(unlocked), unlocked)

    def test_rejects_empty_and_non_integer_ids(self):
        for bad in ((), "12", (1, True), (1, "2"), (0, 1), (1, -2)):
            with self.subTest(bad=bad):
                with self.assertRaises(FleetAvailabilityError):
                    normalize_unlocked_fleet_ids(bad)

    def test_rejects_duplicate_non_contiguous_and_out_of_policy_sets(self):
        for bad in ((1, 1), (1, 3), (2, 3), (1, 2, 3, 4, 5)):
            with self.subTest(bad=bad):
                with self.assertRaises(FleetAvailabilityError):
                    normalize_unlocked_fleet_ids(bad)

    def test_out_of_policy_observation_names_the_policy_ceiling(self):
        with self.assertRaisesRegex(FleetAvailabilityError, "policy ceiling"):
            normalize_unlocked_fleet_ids((1, 2, 3, 4, 5))


class ResolveFleetAvailabilityTests(unittest.TestCase):
    def test_candidate_set_is_trimmed_to_unlocked_fleets(self):
        availability = resolve_fleet_availability((1, 2), (2, 3, 4))
        self.assertEqual(availability.unlocked_fleet_ids, (1, 2))
        self.assertEqual(availability.effective_dispatch_fleet_ids, (2,))
        self.assertEqual(availability.requested_but_not_unlocked, (3, 4))
        self.assertEqual(availability.locked_fleet_ids, (3, 4))

    def test_three_fleet_account_drops_fleet_4_from_the_candidate_set(self):
        availability = resolve_fleet_availability((1, 2, 3), (2, 3, 4))
        self.assertEqual(availability.effective_dispatch_fleet_ids, (2, 3))
        self.assertEqual(availability.requested_but_not_unlocked, (4,))
        self.assertEqual(availability.locked_fleet_ids, (4,))

    def test_four_fleet_account_dispatches_fleet_4(self):
        availability = resolve_fleet_availability(
            (1, 2, 3, 4), (4,), strict=True
        )
        self.assertEqual(availability.effective_dispatch_fleet_ids, (4,))
        self.assertEqual(availability.requested_but_not_unlocked, ())
        self.assertEqual(availability.locked_fleet_ids, ())

    def test_explicit_target_not_unlocked_fails_closed(self):
        for target in (3, 4):
            with self.subTest(target=target):
                with self.assertRaisesRegex(
                    FleetAvailabilityError, "not unlocked on this account"
                ):
                    resolve_fleet_availability(
                        (1, 2), (target,), strict=True
                    )

    def test_fleet_1_is_never_permitted_by_policy(self):
        with self.assertRaisesRegex(FleetAvailabilityError, "not permitted by policy"):
            resolve_fleet_availability((1, 2, 3, 4), (1,), strict=True)

    def test_unknown_target_is_never_permitted_by_policy(self):
        with self.assertRaisesRegex(FleetAvailabilityError, "not permitted by policy"):
            resolve_fleet_availability((1, 2, 3, 4), (5,), strict=True)

    def test_no_requested_fleet_unlocked_fails_closed(self):
        with self.assertRaisesRegex(FleetAvailabilityError, "none of the requested"):
            resolve_fleet_availability((1, 2), (3, 4))

    def test_empty_request_is_rejected(self):
        with self.assertRaisesRegex(FleetAvailabilityError, "must not be empty"):
            resolve_fleet_availability((1, 2, 3, 4), ())


if __name__ == "__main__":
    unittest.main()
