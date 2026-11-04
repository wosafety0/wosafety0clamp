"""Smoke tests for the live dispatch tool's CLI contract.

The tool drives the real game and reads fleet state over CDP, so it cannot be
exercised offline. These tests only assert the module imports cleanly and that
argparse enforces the required arguments -- it must refuse to run without an
explicit --authorize-ui-actions and without both ids.
"""
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from live_dispatch_expedition import _build_parser  # noqa: E402


class ArgumentContractTests(unittest.TestCase):
    def test_refuses_without_required_arguments(self):
        for argv in ([], ["--fleet-id", "2"], ["--fleet-id", "2", "--mission-id", "1"]):
            with self.assertRaises(SystemExit):
                _build_parser().parse_args(argv)

    def test_parses_full_required_arguments(self):
        ns = _build_parser().parse_args(
            ["--fleet-id", "2", "--mission-id", "1", "--authorize-ui-actions"]
        )
        self.assertEqual(ns.fleet_id, 2)
        self.assertEqual(ns.mission_id, 1)
        self.assertTrue(ns.authorize_ui_actions)
        self.assertFalse(ns.commit)

    def test_commit_is_opt_in(self):
        ns = _build_parser().parse_args(
            ["--fleet-id", "2", "--mission-id", "1", "--authorize-ui-actions", "--commit"]
        )
        self.assertTrue(ns.commit)

    def test_default_log_path_lives_under_tempdir(self):
        ns = _build_parser().parse_args(
            ["--fleet-id", "2", "--mission-id", "1", "--authorize-ui-actions"]
        )
        self.assertEqual(ns.log_path.parent, Path(tempfile.gettempdir()))


if __name__ == "__main__":
    unittest.main()
