import unittest

from kancollebot.adapters.poi.cdp_fleet_state import PoiFleetMissionState, PoiShipSupplyState
from kancollebot.expeditions.mission_eligibility import MissionCompositionRequirement
from kancollebot.expeditions.expedition_preflight import FleetPreflightDecision, evaluate_fleet_expedition_preflight


def ship(ship_id, level=4, ship_type=2, fuel=15, ammo=20, max_fuel=15, max_ammo=20):
    return PoiShipSupplyState(ship_id, fuel, ammo, max_fuel, max_ammo, level, ship_type)


def fleet(fleet_id=2, mission=(0, 0, 0, 0), ship_ids=(1, 2), supply=None):
    if supply is None:
        supply = tuple(ship(ship_id) for ship_id in ship_ids)
    return PoiFleetMissionState(fleet_id, f"第{fleet_id}艦隊", mission, tuple(ship_ids), tuple(supply))


class FleetExpeditionPreflightTests(unittest.TestCase):
    def setUp(self):
        self.mission_1 = MissionCompositionRequirement(1, 2, 1)
        self.mission_3 = MissionCompositionRequirement(3, 3, 3)

    def test_fleet_2_mission_1_is_ready_when_idle_eligible_and_fully_supplied(self):
        current = fleet(2, ship_ids=(1, 4, 8, 25), supply=(ship(1, 8), ship(4), ship(8), ship(25)))
        self.assertEqual(evaluate_fleet_expedition_preflight(current, self.mission_1).decision, FleetPreflightDecision.READY)

    def test_fleet_3_mission_3_is_blocked_by_current_flagship_level(self):
        current = fleet(3, ship_ids=(6, 52, 32, 63), supply=(
            ship(6, 2, fuel=11, ammo=9, max_fuel=15, max_ammo=15),
            ship(52, 1, fuel=11, ammo=9, max_fuel=15, max_ammo=15),
            ship(32, 1, fuel=11, ammo=9, max_fuel=15, max_ammo=15),
            ship(63, 1, fuel=11, ammo=9, max_fuel=15, max_ammo=15),
        ))
        plan = evaluate_fleet_expedition_preflight(current, self.mission_3)
        self.assertEqual(plan.decision, FleetPreflightDecision.BLOCKED_COMPOSITION)
        self.assertIn("below required level 3", plan.reason)
        self.assertEqual(plan.unsupplied_ship_ids, (6, 52, 32, 63))

    def test_fleet_3_becomes_eligible_after_flagship_change(self):
        current = fleet(3, ship_ids=(4, 6, 52, 32), supply=(ship(4, 4), ship(6, 2), ship(52, 1), ship(32, 1)))
        self.assertEqual(evaluate_fleet_expedition_preflight(current, self.mission_3).decision, FleetPreflightDecision.READY)

    def test_active_fleet_is_blocked_before_supply_checks(self):
        current = fleet(mission=(1, 1, 123456, 0))
        self.assertEqual(evaluate_fleet_expedition_preflight(current, self.mission_1).decision, FleetPreflightDecision.BLOCKED_BUSY)

    def test_missing_supply_member_blocks_snapshot(self):
        current = fleet(ship_ids=(1, 2), supply=(ship(1),))
        self.assertEqual(evaluate_fleet_expedition_preflight(current, self.mission_1).decision, FleetPreflightDecision.BLOCKED_SUPPLY_SNAPSHOT)

    def test_duplicate_supply_member_blocks_snapshot(self):
        current = fleet(ship_ids=(1, 2), supply=(ship(1), ship(1)))
        self.assertEqual(evaluate_fleet_expedition_preflight(current, self.mission_1).decision, FleetPreflightDecision.BLOCKED_SUPPLY_SNAPSHOT)

    def test_missing_composition_metadata_blocks(self):
        current = fleet(ship_ids=(1, 2), supply=(PoiShipSupplyState(1, 15, 20, 15, 20), ship(2)))
        self.assertEqual(evaluate_fleet_expedition_preflight(current, self.mission_1).decision, FleetPreflightDecision.BLOCKED_COMPOSITION)

    def test_unsupplied_member_blocks_default_policy(self):
        current = fleet(ship_ids=(1, 2), supply=(ship(1, ammo=19), ship(2)))
        plan = evaluate_fleet_expedition_preflight(current, self.mission_1)
        self.assertEqual(plan.decision, FleetPreflightDecision.BLOCKED_UNSUPPLIED)
        self.assertEqual(plan.unsupplied_ship_ids, (1,))

    def test_missing_supply_values_block_default_policy(self):
        current = fleet(ship_ids=(1, 2), supply=(PoiShipSupplyState(1, None, 20, 15, 20, 4, 2), ship(2)))
        self.assertEqual(evaluate_fleet_expedition_preflight(current, self.mission_1).decision, FleetPreflightDecision.BLOCKED_UNSUPPLIED)

    def test_full_supply_policy_can_be_disabled_explicitly(self):
        current = fleet(ship_ids=(1, 2), supply=(ship(1, ammo=19), ship(2)))
        plan = evaluate_fleet_expedition_preflight(current, self.mission_1, require_full_supply=False)
        self.assertEqual(plan.decision, FleetPreflightDecision.READY)

    def test_rejects_non_boolean_supply_policy(self):
        with self.assertRaisesRegex(ValueError, "boolean"):
            evaluate_fleet_expedition_preflight(fleet(), self.mission_1, require_full_supply=1)


if __name__ == "__main__":
    unittest.main()
