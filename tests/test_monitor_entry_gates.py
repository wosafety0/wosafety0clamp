"""P0 R4: the REAL monitor CLI gate, not just build_runtime() in isolation.

Both production entries must pass an observed unlocked-fleet set through the
SAME task-neutral normalizer. Ambiguous input must fail closed before any Poi
observer, game session or click is opened.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import run_continuous_expedition_monitor as monitor  # noqa: E402
from kancollebot.core import fleet_availability as core_fa  # noqa: E402
from kancollebot.expeditions import fleet_availability as exp_fa  # noqa: E402
from kancollebot.task_scope import TaskKind  # noqa: E402


def fake_runtime():
    scheduler = SimpleNamespace(
        dispatch_fleet_ids=(2, 3, 4),
        read_only_fleet_ids=(1,),
        deferred_fleet_ids=(),
    )
    return SimpleNamespace(
        monitor=object(),
        scheduler=scheduler,
        provider=SimpleNamespace(close=lambda **kwargs: None),
        unresolved_dispatch_fleets=(),
        unlocked_fleet_ids=None,
        fleet_availability_verified=False,
        ui_refresh_enabled=False,
    )


class MonitorCliGateTests(unittest.TestCase):
    def run_cli(self, argv):
        """Run the real CLI with build_runtime/run_monitor stubbed out."""
        runtime = fake_runtime()
        with (
            patch.object(monitor, "build_runtime", return_value=runtime) as build,
            patch.object(monitor, "run_monitor", return_value=0) as run,
            patch.object(monitor, "_stdout", lambda line: None),
        ):
            code = monitor._main_impl(argv)
        return code, build, run

    def test_explicit_unlocked_set_is_normalized_and_passed_to_build_runtime(self):
        code, build, run = self.run_cli(["--once", "--unlocked-fleet-ids", "1,2,3"])
        self.assertEqual(code, 0)
        build.assert_called_once()
        run.assert_called_once()
        kwargs = build.call_args.kwargs
        self.assertEqual(kwargs["unlocked_fleet_ids"], (1, 2, 3))
        self.assertIs(kwargs["fleet_availability_strict"], False)
        self.assertIs(kwargs["task_kind"], TaskKind.EXPEDITION)
        self.assertIs(kwargs["enable_ui_refresh"], False)

    def test_strict_mode_is_forwarded_when_requested(self):
        code, build, _ = self.run_cli(
            ["--once", "--unlocked-fleet-ids", "1,2,3,4", "--fleet-availability-strict"]
        )
        self.assertEqual(code, 0)
        self.assertIs(build.call_args.kwargs["fleet_availability_strict"], True)
        self.assertEqual(build.call_args.kwargs["unlocked_fleet_ids"], (1, 2, 3, 4))

    def test_default_read_only_run_remains_unverified(self):
        code, build, _ = self.run_cli(["--once"])
        self.assertEqual(code, 0)
        kwargs = build.call_args.kwargs
        self.assertIsNone(kwargs["unlocked_fleet_ids"])
        self.assertIs(kwargs["fleet_availability_strict"], False)

    def test_ambiguous_sets_fail_closed_before_build_runtime(self):
        for value in ("", "1,3", "1,2,2", "x", "0", "1,2,5"):
            with self.subTest(value=value):
                with patch.object(monitor, "build_runtime") as build, \
                     patch.object(monitor, "run_monitor") as run:
                    with self.assertRaises(SystemExit) as ctx:
                        monitor._main_impl(["--once", "--unlocked-fleet-ids", value])
                    self.assertEqual(ctx.exception.code, 2)
                build.assert_not_called()
                run.assert_not_called()

    def test_strict_requires_an_observed_set(self):
        with patch.object(monitor, "build_runtime") as build:
            with self.assertRaises(SystemExit) as ctx:
                monitor._main_impl(["--once", "--fleet-availability-strict"])
        self.assertEqual(ctx.exception.code, 2)
        build.assert_not_called()

    def test_require_verified_flag_refuses_unverified_startup(self):
        with patch.object(monitor, "build_runtime") as build:
            with self.assertRaises(SystemExit) as ctx:
                monitor._main_impl(["--once", "--require-verified-fleet-availability"])
        self.assertEqual(ctx.exception.code, 2)
        build.assert_not_called()

    def test_require_verified_flag_accepts_an_observed_set(self):
        code, build, _ = self.run_cli(
            ["--once", "--require-verified-fleet-availability",
             "--unlocked-fleet-ids", "1,2,3"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(build.call_args.kwargs["unlocked_fleet_ids"], (1, 2, 3))

    def test_both_entries_share_one_normalizer(self):
        """R4: dispatch-once's probe and monitor's parser cannot diverge."""
        self.assertIs(
            exp_fa.normalize_unlocked_fleet_ids,
            core_fa.normalize_unlocked_fleet_ids,
        )
        self.assertIs(exp_fa.FleetAvailabilityError, core_fa.FleetAvailabilityError)
        self.assertEqual(
            core_fa.parse_unlocked_fleet_ids("1,2,3"),
            core_fa.normalize_unlocked_fleet_ids((1, 2, 3)),
        )
        for bad in ("", "1,3", "1,1", "abc", "1,2,3,4,5"):
            with self.subTest(bad=bad):
                with self.assertRaises(core_fa.FleetAvailabilityError):
                    core_fa.parse_unlocked_fleet_ids(bad)


if __name__ == "__main__":
    unittest.main()
