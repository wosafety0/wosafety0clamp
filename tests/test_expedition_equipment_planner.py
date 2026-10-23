"""Offline tests for read-only expedition equipment planning."""

import unittest

from kancollebot.core.expedition_equipment_planner import (
    EquipmentInstance,
    EquipmentSlotRequirement,
    plan_expedition_equipment,
)


class ExpeditionEquipmentPlannerTests(unittest.TestCase):
    def test_assigns_unique_instances_deterministically(self):
        requirements = [
            EquipmentSlotRequirement(10, 0,  drum_model := 44, preferred_stars=2),
            EquipmentSlotRequirement(11, 0, drum_model, preferred_stars=5),
        ]
        inventory = [
            EquipmentInstance(100, drum_model, 2, 10, 0),
            EquipmentInstance(101, drum_model, 5, 11, 0),
        ]
        result = plan_expedition_equipment(requirements, inventory, target_ship_ids=[10, 11])
        self.assertTrue(result.eligible, result.reason)
        self.assertEqual([x.equipment_id for x in result.assignments], [100, 101])

    def test_never_borrows_from_ship_outside_target_fleet(self):
        result = plan_expedition_equipment(
            [EquipmentSlotRequirement(10, 0, 44)],
            [EquipmentInstance(100, 44, 0, 99, 0)],
            target_ship_ids=[10],
        )
        self.assertFalse(result.eligible)

    def test_reinforcement_slot_requires_eligibility(self):
        req = EquipmentSlotRequirement(10, 5, 44, reinforcement_slot=True)
        result = plan_expedition_equipment(
            [req], [EquipmentInstance(100, 44, 0, reinforcement_eligible_ship_ids=(11,))],
            target_ship_ids=[10],
        )
        self.assertFalse(result.eligible)

    def test_reinforcement_slot_accepts_eligible_ship(self):
        req = EquipmentSlotRequirement(10, 5, 44, reinforcement_slot=True)
        result = plan_expedition_equipment(
            [req], [EquipmentInstance(100, 44, 0, reinforcement_eligible_ship_ids=(10,))],
            target_ship_ids=[10],
        )
        self.assertTrue(result.eligible, result.reason)
        self.assertEqual(result.assignments[0].equipment_id, 100)

    def test_duplicate_slot_requirement_fails_closed(self):
        req = EquipmentSlotRequirement(10, 0, 44)
        result = plan_expedition_equipment([req, req], [EquipmentInstance(100, 44, 0)],
                                           target_ship_ids=[10])
        self.assertFalse(result.eligible)

    def test_duplicate_inventory_id_fails_closed(self):
        inventory = [EquipmentInstance(100, 44, 0), EquipmentInstance(100, 45, 0)]
        result = plan_expedition_equipment([EquipmentSlotRequirement(10, 0, 44)],
                                           inventory, target_ship_ids=[10])
        self.assertFalse(result.eligible)

    def test_single_instance_cannot_fill_two_slots(self):
        inventory = [EquipmentInstance(100, 44, 0)]
        reqs = [EquipmentSlotRequirement(10, 0, 44), EquipmentSlotRequirement(10, 1, 44)]
        result = plan_expedition_equipment(reqs, inventory, target_ship_ids=[10])
        self.assertFalse(result.eligible)

    def test_unknown_protected_state_not_inferred(self):
        # A source ship outside the target fleet is never considered movable.
        inventory = [EquipmentInstance(100, 44, 0, 99, 0)]
        result = plan_expedition_equipment([EquipmentSlotRequirement(10, 0, 44)],
                                           inventory, target_ship_ids=[10],
                                           protected_ship_ids=[99])
        self.assertFalse(result.eligible)


if __name__ == "__main__":
    unittest.main()
