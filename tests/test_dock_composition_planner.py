"""Offline tests for the dock composition planner; no Poi or UI access."""

from __future__ import annotations

import unittest

from kancollebot.expeditions.dock_composition_planner import (
    CompositionActionKind,
    DockCompositionDecision,
    DockInventoryError,
    DockShip,
    dock_ships_from_poi_payloads,
    plan_dock_composition,
)
from kancollebot.expeditions.mission_eligibility import (
    FleetCompositionSnapshot,
    MissionCompositionRequirement,
)


# Ship type IDs mirror the game's api_stype values used in these scenarios.
DESTROYER = 2
LIGHT_CRUISER = 3
HEAVY_CRUISER = 6
SEAPLANE_TENDER = 16


def _fleet(ids, levels, types):
    return FleetCompositionSnapshot(tuple(ids), tuple(levels), tuple(types))


def _requirement(minimum_ships=3, flagship_level=3, allowed_types=None):
    return MissionCompositionRequirement(
        mission_id=3,
        minimum_ships=minimum_ships,
        minimum_flagship_level=flagship_level,
        allowed_ship_types=allowed_types,
    )


def _dock(*entries):
    return tuple(DockShip(*entry) for entry in entries)


class NoChangeTests(unittest.TestCase):
    def test_eligible_fleet_needs_no_change(self):
        fleet = _fleet([1, 2, 3], [10, 10, 10], [DESTROYER, DESTROYER, DESTROYER])
        plan = plan_dock_composition(fleet, _requirement(), _dock((9, 90, DESTROYER, 5)))
        self.assertIs(plan.decision, DockCompositionDecision.NO_CHANGE_NEEDED)
        self.assertEqual(plan.actions, ())
        self.assertIs(plan.resulting_composition, fleet)
        self.assertFalse(plan.actionable)


class AddShipTests(unittest.TestCase):
    def test_adds_ships_to_reach_minimum_count(self):
        fleet = _fleet([1, 2], [10, 10], [DESTROYER, DESTROYER])
        plan = plan_dock_composition(
            fleet, _requirement(minimum_ships=3), _dock((9, 90, DESTROYER, 5))
        )
        self.assertIs(plan.decision, DockCompositionDecision.PLAN_AVAILABLE)
        self.assertEqual([a.kind for a in plan.actions], [CompositionActionKind.ADD_SHIP])
        self.assertEqual(plan.actions[0].ship_id, 9)
        self.assertEqual(plan.actions[0].master_id, 90)
        self.assertEqual(plan.resulting_composition.ship_ids, (1, 2, 9))

    def test_prefers_lowest_level_then_lowest_id(self):
        fleet = _fleet([1, 2], [10, 10], [DESTROYER, DESTROYER])
        dock = _dock((9, 90, DESTROYER, 7), (5, 50, DESTROYER, 4), (7, 70, DESTROYER, 4))
        plan = plan_dock_composition(fleet, _requirement(minimum_ships=3), dock)
        self.assertEqual(plan.actions[0].ship_id, 5)

    def test_adds_exactly_the_missing_count(self):
        fleet = _fleet([1], [10], [DESTROYER])
        dock = _dock(
            (9, 90, DESTROYER, 5), (8, 80, DESTROYER, 5), (7, 70, DESTROYER, 5)
        )
        plan = plan_dock_composition(fleet, _requirement(minimum_ships=3), dock)
        self.assertEqual(
            [a.ship_id for a in plan.actions], [7, 8]
        )
        self.assertEqual(len(plan.resulting_composition.ship_ids), 3)


class ShipTypeTests(unittest.TestCase):
    def test_removes_disallowed_type_and_adds_allowed(self):
        fleet = _fleet([1, 2, 3], [10, 10, 10], [DESTROYER, DESTROYER, SEAPLANE_TENDER])
        plan = plan_dock_composition(
            fleet,
            _requirement(allowed_types=(DESTROYER, LIGHT_CRUISER)),
            _dock((9, 90, DESTROYER, 5)),
        )
        self.assertIs(plan.decision, DockCompositionDecision.PLAN_AVAILABLE)
        self.assertEqual(
            [(a.kind, a.ship_id, a.slot) for a in plan.actions],
            [(CompositionActionKind.REMOVE_SHIP, 3, 2), (CompositionActionKind.ADD_SHIP, 9, None)],
        )
        self.assertNotIn(3, plan.resulting_composition.ship_ids)

    def test_keeps_extra_ships_beyond_minimum(self):
        fleet = _fleet([1, 2, 3, 4], [10, 10, 10, 10], [DESTROYER] * 4)
        plan = plan_dock_composition(fleet, _requirement(minimum_ships=3), _dock())
        self.assertIs(plan.decision, DockCompositionDecision.NO_CHANGE_NEEDED)

    def test_no_allowed_type_in_dock_is_blocked(self):
        fleet = _fleet([1, 2], [10, 10], [DESTROYER, DESTROYER])
        plan = plan_dock_composition(
            fleet,
            _requirement(minimum_ships=3, allowed_types=(DESTROYER,)),
            _dock((9, 90, HEAVY_CRUISER, 5)),
        )
        self.assertIs(
            plan.decision, DockCompositionDecision.BLOCKED_NO_ALLOWED_TYPE_CANDIDATE
        )
        self.assertIsNone(plan.resulting_composition)


class FlagshipTests(unittest.TestCase):
    def test_reorders_existing_qualifying_ship_to_flagship(self):
        fleet = _fleet([1, 2, 3], [1, 12, 10], [DESTROYER] * 3)
        plan = plan_dock_composition(fleet, _requirement(), _dock())
        self.assertIs(plan.decision, DockCompositionDecision.PLAN_AVAILABLE)
        self.assertEqual(
            [(a.kind, a.ship_id) for a in plan.actions],
            [(CompositionActionKind.SET_FLAGSHIP, 2)],
        )
        self.assertEqual(plan.resulting_composition.ship_ids, (2, 1, 3))
        self.assertEqual(plan.resulting_composition.ship_levels[0], 12)

    def test_existing_qualifying_flagship_is_kept_when_types_change(self):
        fleet = _fleet([1, 2, 3], [20, 10, 10], [SEAPLANE_TENDER, DESTROYER, DESTROYER])
        plan = plan_dock_composition(
            fleet,
            _requirement(allowed_types=(DESTROYER,)),
            _dock((9, 90, DESTROYER, 5)),
        )
        self.assertIs(plan.decision, DockCompositionDecision.PLAN_AVAILABLE)
        self.assertEqual(plan.resulting_composition.ship_ids[0], 2)
        self.assertNotIn(CompositionActionKind.SET_FLAGSHIP, [a.kind for a in plan.actions])

    def test_adds_dock_flagship_at_slot_zero(self):
        fleet = _fleet([1, 2, 3], [1, 1, 1], [DESTROYER] * 3)
        plan = plan_dock_composition(
            fleet, _requirement(), _dock((9, 90, DESTROYER, 4), (8, 80, DESTROYER, 9))
        )
        self.assertIs(plan.decision, DockCompositionDecision.PLAN_AVAILABLE)
        add = next(a for a in plan.actions if a.kind is CompositionActionKind.ADD_SHIP)
        # The flagship slot takes the strongest qualifying dock ship.
        self.assertEqual((add.ship_id, add.slot), (8, 0))
        self.assertEqual(plan.resulting_composition.ship_ids[0], 8)
        self.assertEqual(len(plan.resulting_composition.ship_ids), 3)
        # The fleet already met the minimum, so the weakest hull is swapped out
        # instead of growing the fleet to four ships.
        self.assertIn(
            (CompositionActionKind.REMOVE_SHIP, 1, 0),
            [(a.kind, a.ship_id, a.slot) for a in plan.actions],
        )

    def test_no_qualifying_flagship_anywhere_is_blocked(self):
        fleet = _fleet([1, 2, 3], [1, 1, 1], [DESTROYER] * 3)
        plan = plan_dock_composition(
            fleet, _requirement(flagship_level=5), _dock((9, 90, DESTROYER, 2))
        )
        self.assertIs(
            plan.decision, DockCompositionDecision.BLOCKED_NO_QUALIFYING_FLAGSHIP
        )

    def test_full_fleet_makes_room_for_dock_flagship(self):
        fleet = _fleet([1, 2, 3, 4, 5, 6], [1, 8, 9, 10, 11, 12], [DESTROYER] * 6)
        plan = plan_dock_composition(
            fleet, _requirement(minimum_ships=6, flagship_level=20), _dock((9, 90, DESTROYER, 30))
        )
        self.assertIs(plan.decision, DockCompositionDecision.PLAN_AVAILABLE)
        self.assertEqual(len(plan.resulting_composition.ship_ids), 6)
        self.assertEqual(plan.resulting_composition.ship_ids[0], 9)
        self.assertNotIn(1, plan.resulting_composition.ship_ids)
        self.assertIn(
            (CompositionActionKind.REMOVE_SHIP, 1, 0),
            [(a.kind, a.ship_id, a.slot) for a in plan.actions],
        )


class InsufficientDockTests(unittest.TestCase):
    def test_too_few_available_ships_is_blocked(self):
        fleet = _fleet([1], [10], [DESTROYER])
        plan = plan_dock_composition(
            fleet, _requirement(minimum_ships=4), _dock((9, 90, DESTROYER, 5))
        )
        self.assertIs(
            plan.decision, DockCompositionDecision.BLOCKED_INSUFFICIENT_DOCK_SHIPS
        )
        self.assertIsNone(plan.resulting_composition)


class ValidationTests(unittest.TestCase):
    def test_dock_ship_rejects_non_positive_fields(self):
        for kwargs in (
            dict(ship_id=0, master_id=1, ship_type=2, level=1),
            dict(ship_id=1, master_id=0, ship_type=2, level=1),
            dict(ship_id=1, master_id=1, ship_type=0, level=1),
            dict(ship_id=1, master_id=1, ship_type=2, level=0),
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(DockInventoryError):
                DockShip(**kwargs)

    def test_dock_ship_rejects_bool(self):
        with self.assertRaises(DockInventoryError):
            DockShip(True, 1, 2, 1)

    def test_dock_overlapping_the_fleet_is_rejected(self):
        fleet = _fleet([1, 2], [10, 10], [DESTROYER, DESTROYER])
        with self.assertRaisesRegex(DockInventoryError, "already assigned"):
            plan_dock_composition(fleet, _requirement(), _dock((2, 20, DESTROYER, 5)))

    def test_duplicate_dock_ids_are_rejected(self):
        fleet = _fleet([1], [10], [DESTROYER])
        with self.assertRaisesRegex(DockInventoryError, "unique"):
            plan_dock_composition(
                fleet, _requirement(), _dock((9, 90, DESTROYER, 5), (9, 91, DESTROYER, 6))
            )

    def test_wrong_planner_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            plan_dock_composition(object(), _requirement(), ())
        with self.assertRaises(ValueError):
            plan_dock_composition(_fleet([1], [10], [DESTROYER]), object(), ())


class PayloadBuilderTests(unittest.TestCase):
    def _payloads(self):
        fleets = [{"api_id": 1, "api_ship": [101, 102, -1, -1, -1, -1]}]
        ships = {
            "101": {"api_id": 101, "api_ship_id": 11, "api_lv": 10},
            "102": {"api_id": 102, "api_ship_id": 12, "api_lv": 4},
            "103": {"api_id": 103, "api_ship_id": 13, "api_lv": 7},
            "104": {"api_id": 104, "api_ship_id": 14, "api_lv": 2},
        }
        masters = {
            "11": {"api_stype": DESTROYER},
            "12": {"api_stype": DESTROYER},
            "13": {"api_stype": LIGHT_CRUISER},
            "14": {"api_stype": HEAVY_CRUISER},
        }
        return fleets, ships, masters

    def test_returns_only_unassigned_ships(self):
        fleets, ships, masters = self._payloads()
        dock = dock_ships_from_poi_payloads(fleets, ships, masters)
        self.assertEqual([ship.ship_id for ship in dock], [103, 104])
        self.assertEqual((dock[0].master_id, dock[0].ship_type, dock[0].level), (13, LIGHT_CRUISER, 7))

    def test_missing_master_record_fails_closed(self):
        fleets, ships, masters = self._payloads()
        del masters["13"]
        with self.assertRaisesRegex(DockInventoryError, "master ship record 13"):
            dock_ships_from_poi_payloads(fleets, ships, masters)

    def test_missing_ship_type_fails_closed(self):
        fleets, ships, masters = self._payloads()
        masters["13"] = {}
        with self.assertRaisesRegex(DockInventoryError, "api_stype"):
            dock_ships_from_poi_payloads(fleets, ships, masters)

    def test_invalid_level_fails_closed(self):
        fleets, ships, masters = self._payloads()
        ships["103"]["api_lv"] = None
        with self.assertRaisesRegex(DockInventoryError, "api_lv"):
            dock_ships_from_poi_payloads(fleets, ships, masters)

    def test_non_numeric_ship_keys_are_rejected(self):
        fleets, ships, masters = self._payloads()
        ships["x"] = {}
        with self.assertRaisesRegex(DockInventoryError, "numeric IDs"):
            dock_ships_from_poi_payloads(fleets, ships, masters)

    def test_builder_output_drives_a_real_plan(self):
        fleets, ships, masters = self._payloads()
        dock = dock_ships_from_poi_payloads(fleets, ships, masters)
        fleet = _fleet([101, 102], [10, 4], [DESTROYER, DESTROYER])
        plan = plan_dock_composition(fleet, _requirement(minimum_ships=3), dock)
        self.assertIs(plan.decision, DockCompositionDecision.PLAN_AVAILABLE)
        self.assertEqual(plan.resulting_composition.ship_ids, (101, 102, 104))


class FailClosedVerificationTests(unittest.TestCase):
    def test_every_returned_plan_verifies_eligible(self):
        scenarios = [
            (_fleet([1, 2], [10, 10], [DESTROYER, DESTROYER]), _requirement(minimum_ships=3), _dock((9, 90, DESTROYER, 5))),
            (_fleet([1, 2, 3], [1, 12, 10], [DESTROYER] * 3), _requirement(), _dock()),
            (_fleet([1, 2, 3], [1, 1, 1], [DESTROYER] * 3), _requirement(), _dock((9, 90, DESTROYER, 4))),
            (_fleet([1, 2, 3], [10, 10, 10], [DESTROYER, DESTROYER, SEAPLANE_TENDER]), _requirement(allowed_types=(DESTROYER,)), _dock((9, 90, DESTROYER, 5))),
        ]
        for index, (fleet, requirement, dock) in enumerate(scenarios):
            with self.subTest(index=index):
                plan = plan_dock_composition(fleet, requirement, dock)
                if plan.decision is DockCompositionDecision.PLAN_AVAILABLE:
                    from kancollebot.expeditions.mission_eligibility import (
                        MissionEligibilityDecision,
                        evaluate_mission_composition,
                    )

                    self.assertIs(
                        evaluate_mission_composition(
                            plan.resulting_composition, requirement
                        ).decision,
                        MissionEligibilityDecision.ELIGIBLE,
                    )


if __name__ == "__main__":
    unittest.main()
