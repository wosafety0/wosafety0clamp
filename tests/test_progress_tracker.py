import copy
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("progress_tracker", ROOT / "tools" / "progress_tracker.py")
tracker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tracker)


class ProgressTrackerTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads((ROOT / "progress" / "project_plan.json").read_text(encoding="utf-8"))

    def test_plan_validates(self):
        tracker.validate(self.plan)

    def test_implementation_is_not_acceptance(self):
        s = tracker.summarize(self.plan)
        self.assertGreater(s["work_progress_percent"], 0)
        self.assertEqual(s["acceptance_progress_percent"], 0)

    def test_real_capture_blocker_is_reported(self):
        s = tracker.summarize(self.plan)
        self.assertEqual(s["blocked_count"], 1)
        self.assertEqual(s["blocked"][0]["id"], "A3")

    def test_duplicate_ids_fail(self):
        bad = copy.deepcopy(self.plan)
        bad["nodes"].append(copy.deepcopy(bad["nodes"][-1]))
        with self.assertRaisesRegex(tracker.PlanError, "duplicate"):
            tracker.validate(bad)

    def test_verified_requires_pass_evidence(self):
        bad = copy.deepcopy(self.plan)
        n = next(x for x in bad["nodes"] if x["id"] == "A1")
        n["status"] = "VERIFIED"
        with self.assertRaisesRegex(tracker.PlanError, "PASS evidence"):
            tracker.validate(bad)

    def test_child_weights_sum_to_100(self):
        bad = copy.deepcopy(self.plan)
        next(x for x in bad["nodes"] if x["id"] == "A1")["weight"] = 16
        with self.assertRaisesRegex(tracker.PlanError, "sum to 100"):
            tracker.validate(bad)

    def test_dependency_cycle_fails(self):
        bad = copy.deepcopy(self.plan)
        next(x for x in bad["nodes"] if x["id"] == "A3")["dependencies"] = ["A4"]
        with self.assertRaisesRegex(tracker.PlanError, "cycle"):
            tracker.validate(bad)

    def test_verified_cannot_bypass_dependency(self):
        bad = copy.deepcopy(self.plan)
        n = next(x for x in bad["nodes"] if x["id"] == "A4")
        n["status"] = "VERIFIED"
        n["evidence"] = [{"type":"test","status":"PASS","ref":"https://example.invalid"}]
        with self.assertRaisesRegex(tracker.PlanError, "dependency is not VERIFIED"):
            tracker.validate(bad)


if __name__ == "__main__":
    unittest.main()
