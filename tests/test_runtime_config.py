from __future__ import annotations

import unittest

from kancollebot.runtime_config import RUNTIME_CONFIG


class RuntimeConfigTests(unittest.TestCase):
    def test_production_target_is_explicit_and_in_monitor_set(self):
        self.assertEqual(RUNTIME_CONFIG.production_dispatch_fleet_id, 3)
        self.assertEqual(RUNTIME_CONFIG.production_mission_id, 3)
        self.assertIn(RUNTIME_CONFIG.production_dispatch_fleet_id, RUNTIME_CONFIG.monitor_candidate_fleet_ids)

    def test_monitor_and_production_fleet_sets_are_distinct(self):
        self.assertEqual(RUNTIME_CONFIG.monitor_candidate_fleet_ids, (2, 3))
        self.assertEqual(RUNTIME_CONFIG.all_fleet_ids, (1, 2, 3))
        self.assertEqual(RUNTIME_CONFIG.deferred_fleet_ids, (4,))

    def test_fleet_1_is_reserved_and_not_dispatchable(self):
        self.assertEqual(RUNTIME_CONFIG.non_dispatchable_fleet_ids, (1,))
        self.assertIn(1, RUNTIME_CONFIG.all_fleet_ids)
        self.assertNotIn(1, RUNTIME_CONFIG.dispatchable_fleet_ids)
        self.assertEqual(RUNTIME_CONFIG.dispatchable_fleet_ids, (2, 3))

    def test_monitor_candidates_are_a_subset_of_dispatchable_fleets(self):
        self.assertTrue(
            set(RUNTIME_CONFIG.monitor_candidate_fleet_ids).issubset(
                set(RUNTIME_CONFIG.dispatchable_fleet_ids)
            )
        )

    def test_runtime_paths_are_rooted_in_repository_and_databases_are_separate(self):
        self.assertEqual(RUNTIME_CONFIG.monitor_database.parent, RUNTIME_CONFIG.dispatch_database.parent)
        self.assertNotEqual(RUNTIME_CONFIG.monitor_database, RUNTIME_CONFIG.dispatch_database)
        self.assertEqual(RUNTIME_CONFIG.observer_script.name, "poi_port_response_observer_20261029.js")


if __name__ == "__main__":
    unittest.main()
