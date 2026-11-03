from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from kancollebot.loop.resupply import (
    ResupplyReadinessError,
    evaluate_resupply_readiness,
    verify_resupply_complete,
)

UTC = timezone.utc
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
OBSERVED_AT = NOW - timedelta(seconds=5)
MAX_AGE = timedelta(seconds=30)


def _masters() -> dict[str, dict[str, int]]:
    return {str(mid): {"api_fuel_max": 15, "api_bull_max": 15} for mid in range(11, 17)}


def _ships(*, supplied: bool) -> dict[str, dict[str, int]]:
    fuel = 15 if supplied else 10
    bull = 15 if supplied else 10
    return {
        str(sid): {"api_id": sid, "api_ship_id": 10 + sid, "api_fuel": fuel, "api_bull": bull}
        for sid in range(1, 7)
    }


def _fleets(*, idle: bool = True) -> list[dict[str, object]]:
    mission = [0, 0, 0, 0] if idle else [1, 3, 1_000_000, 0]
    return [
        {"api_id": fid, "api_mission": list(mission), "api_ship": [2 * fid - 1, 2 * fid]}
        for fid in (1, 2,3)
    ]


class EvaluateResupplyReadinessTests(unittest.TestCase):
    def test_feasible_plan_is_built_from_fresh_idle_observation(self):
        result = evaluate_resupply_readiness(
            _fleets(), _ships(supplied=False), _masters(), [1000, 1000],
            observed_at=OBSERVED_AT, now=NOW, max_age=MAX_AGE,
        )
        self.assertTrue(result.feasible)
        self.assertIsNotNone(result.plan)
        self.assertEqual(result.plan.fuel_required, 30)
        self.assertEqual(result.plan.ammo_required, 30)
        self.assertEqual(result.plan.fuel_available, 1000)
        self.assertEqual(result.plan.ammo_available, 1000)

    def test_non_idle_fleet_makes_plan_infeasible_not_raised(self):
        result = evaluate_resupply_readiness(
            _fleets(idle=False), _ships(supplied=False), _masters(), [1000, 1000],
            observed_at=OBSERVED_AT, now=NOW, max_age=MAX_AGE,
        )
        self.assertFalse(result.feasible)
        self.assertIsNone(result.plan)
        self.assertIn("not idle", result.reason)

    def test_insufficient_resources_make_plan_infeasible(self):
        result = evaluate_resupply_readiness(
            _fleets(), _ships(supplied=False), _masters(), [10, 10],
            observed_at=OBSERVED_AT, now=NOW, max_age=MAX_AGE,
        )
        self.assertFalse(result.feasible)
        self.assertIsNone(result.plan)
        self.assertIn("insufficient", result.reason)

    def test_stale_witness_makes_plan_infeasible(self):
        stale = NOW - timedelta(seconds=60)
        result = evaluate_resupply_readiness(
            _fleets(), _ships(supplied=False), _masters(), [1000, 1000],
            observed_at=stale, now=NOW, max_age=MAX_AGE,
        )
        self.assertFalse(result.feasible)
        self.assertIsNone(result.plan)
        self.assertIn("stale", result.reason)

    def test_negative_max_age_is_rejected_as_input_error(self):
        with self.assertRaises(ResupplyReadinessError):
            evaluate_resupply_readiness(
                _fleets(), _ships(supplied=False), _masters(), [1000, 1000],
                observed_at=OBSERVED_AT, now=NOW, max_age=timedelta(seconds=-1),
            )


class VerifyResupplyCompleteTests(unittest.TestCase):
    def test_all_ships_supplied_reports_complete(self):
        plan = evaluate_resupply_readiness(
            _fleets(), _ships(supplied=False), _masters(), [1000, 1000],
            observed_at=OBSERVED_AT, now=NOW, max_age=MAX_AGE,
        ).plan
        self.assertIsNotNone(plan)
        supplied = _ships(supplied=True)
        self.assertTrue(verify_resupply_complete(plan, _fleets(), supplied, _masters()))

    def test_any_undersupplied_ship_reports_incomplete(self):
        plan = evaluate_resupply_readiness(
            _fleets(), _ships(supplied=False), _masters(), [1000, 1000],
            observed_at=OBSERVED_AT, now=NOW, max_age=MAX_AGE,
        ).plan
        self.assertIsNotNone(plan)
        partial = _ships(supplied=True)
        partial["1"]["api_fuel"] = 10
        self.assertFalse(verify_resupply_complete(plan, _fleets(), partial, _masters()))


if __name__ == "__main__":
    unittest.main()
