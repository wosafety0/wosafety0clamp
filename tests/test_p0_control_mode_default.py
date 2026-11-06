"""P0 Step 1: stateless control is the production default; ``--strict`` opts out.

These are OFFLINE tests. No Poi/CDP connection, no game click, no screenshot
and no calibration asset. They pin three facts:

* the real CLI parser defaults to stateless control and ``--strict`` restores
  the historical ledger-veto mode (and wins if both flags are given);
* the composition root's resolver never lets a truthy test double silently
  decide the control mode;
* the startup settlement chain keeps ALL of its own attribution/witness gates
  in the new default mode. Step 1 changes only the main loop's ledger veto.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import one_expedition_ui_loop as entry
from kancollebot.expeditions.continuous_scheduler import (
    ContinuousExpeditionScheduler,
    UiOperationArbiter,
)
from kancollebot.expeditions.continuous_settlement_controller import (
    ContinuousSettlementController,
)
from kancollebot.expeditions.startup_settlement import (
    StartupSettlementRefused,
    select_startup_screen,
)
from tests.test_continuous_settlement_controller_startup import (
    FLEET,
    OTHER,
    NoNavigationReader,
    SpySettlementUi,
    fleet_snapshot,
    make_observation,
)


class ControlModeCliTests(unittest.TestCase):
    """The real production CLI decides the main-loop control mode."""

    COMMON = ["--authorize-ui-actions", "--fleet-id", "3", "--mission-id", "3"]

    def _parse(self, *extra: str):
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "loop.jsonl"
            with ExitStack() as stack:
                # _main_impl rebinds these module globals; restore them so a
                # later test never writes to a temporary directory that is gone.
                stack.enter_context(patch.object(entry, "LOG_PATH", entry.LOG_PATH))
                stack.enter_context(patch.object(entry, "RUN_ID", entry.RUN_ID))
                stack.enter_context(
                    patch.object(entry, "HOME_REFERENCE_BASE64", None)
                )
                stack.enter_context(patch.object(entry, "_print_event"))
                stack.enter_context(patch.object(entry, "_stop_run_log"))
                run = stack.enter_context(patch.object(entry, "run", return_value=0))
                self.assertEqual(
                    entry._main_impl([
                        *self.COMMON, "--log-path", str(log), *extra,
                    ]),
                    0,
                )
            return run.call_args.args[0]

    def test_no_flag_defaults_to_stateless_control(self):
        args = self._parse()
        self.assertIs(args.stateless, True)
        self.assertIs(args.strict, False)
        self.assertIs(entry._stateless_control_enabled(args), True)

    def test_explicit_stateless_matches_the_default(self):
        args = self._parse("--stateless")
        self.assertIs(entry._stateless_control_enabled(args), True)

    def test_strict_restores_the_ledger_veto_mode(self):
        args = self._parse("--strict")
        self.assertIs(args.strict, True)
        self.assertIs(entry._stateless_control_enabled(args), False)

    def test_strict_wins_when_both_flags_are_given(self):
        args = self._parse("--stateless", "--strict")
        self.assertIs(entry._stateless_control_enabled(args), False)


class ControlModeResolverTests(unittest.TestCase):
    """Strict identity: a MagicMock attribute is never a control decision."""

    def test_a_namespace_without_flags_is_stateless(self):
        self.assertIs(entry._stateless_control_enabled(SimpleNamespace()), True)

    def test_a_magicmock_namespace_without_explicit_flags_is_stateless(self):
        self.assertIs(entry._stateless_control_enabled(MagicMock()), True)

    def test_only_explicit_booleans_opt_out(self):
        self.assertIs(
            entry._stateless_control_enabled(SimpleNamespace(strict=True)), False
        )
        self.assertIs(
            entry._stateless_control_enabled(SimpleNamespace(stateless=False)), False
        )
        args = MagicMock()
        args.strict = MagicMock()  # truthy, but not the boolean True
        self.assertIs(entry._stateless_control_enabled(args), True)


class UnchangedStartupGateTests(unittest.TestCase):
    """The default flip does NOT relax the startup settlement path."""

    def test_startup_chain_still_refuses_with_zero_calls_in_default_mode(self):
        ui = SpySettlementUi(displayed_screen="BONUS_ITEMS")
        observation = make_observation([
            fleet_snapshot(), fleet_snapshot(OTHER)
        ])
        controller = ContinuousSettlementController(
            scheduler=ContinuousExpeditionScheduler(
                dispatch_fleet_ids=(FLEET,), state_store=None
            ),
            state_reader=NoNavigationReader(observation),
            ui_driver=ui,
            ui_arbiter=UiOperationArbiter(),
            event_sink=lambda _name, _fields: None,
            allow_ui_actions=True,
            dispatch_registry=None,
            # Exactly what the default stateless composition root injects.
            audit_gate=None,
            ledger_audit_only=True,
        )
        self.assertIs(controller.ledger_audit_only, True)
        self.assertIsNone(controller.audit_gate)

        with self.assertRaises(StartupSettlementRefused) as caught:
            controller.settle_visible_reward_chain(
                fleet_id=FLEET,
                screen=select_startup_screen(composited_state="BONUS_ITEMS"),
                expected_ship_ids=None,
                expected_ship_ids_source=None,
            )

        self.assertEqual(
            caught.exception.reason, "recorded_composition_unavailable"
        )
        # No screen probe and no reward click: the startup gate ran first.
        self.assertEqual(ui.calls, [])


if __name__ == "__main__":
    unittest.main()
