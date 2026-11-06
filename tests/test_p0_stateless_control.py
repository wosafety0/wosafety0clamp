"""P0 stateless control: the ledger is an audit trail, never a veto.

These are behaviour-level regressions for the two production controllers that
owns the click chain. The architecture-level proof (same composition root,
same supervisor, visual gate unchanged) lives in
``test_one_expedition_production_integration.py``.

Both directions are pinned: stateless mode continues where the old chain
refused, and strict-ledger mode keeps every historical fail-closed refusal.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kancollebot.expeditions.continuous_dispatch_controller import (
    ContinuousDispatchController,
)
from kancollebot.expeditions.continuous_scheduler import (
    ContinuousExpeditionScheduler,
    FleetPhase,
    UiOperationArbiter,
)
from kancollebot.expeditions.continuous_settlement_controller import (
    ContinuousSettlementController,
    ContinuousSettlementError,
)
from kancollebot.expeditions.dispatch_registry import SqliteDispatchRegistry
from tests.test_settlement_global_drain_barrier import (
    RecordingUi,
    StepClock,
    TwoFleetReader,
    persist,
    prepare_two_returns,
)


class StatelessLedgerAuditOnlyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "dispatch.sqlite3"
        self.registry = SqliteDispatchRegistry(self.path, task_kind="expedition")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def build(self, *, registry=True, ledger_audit_only=True):
        clock = StepClock()
        reader = TwoFleetReader(clock)
        ui = RecordingUi(probe=True)
        scheduler = ContinuousExpeditionScheduler(
            state_store=None, dispatch_fleet_ids=(2, 3)
        )
        prepare_two_returns(scheduler)
        events: list[tuple[str, dict]] = []
        controller = ContinuousSettlementController(
            scheduler=scheduler,
            state_reader=reader,
            ui_driver=ui,
            ui_arbiter=UiOperationArbiter(),
            event_sink=lambda name, fields: events.append((name, dict(fields))),
            allow_ui_actions=True,
            clock=clock,
            dispatch_registry=self.registry if registry else None,
            ledger_audit_only=ledger_audit_only,
        )
        return controller, ui, scheduler, events

    def test_stateless_settles_without_any_ledger(self):
        """Old `no dispatch ledger is wired` refusal becomes an audit row."""
        persist(self.registry, 2)
        persist(self.registry, 3)
        controller, ui, scheduler, events = self.build(registry=False)

        execution = controller.settle_one_returned(2, pages_drained_globally=True)

        self.assertEqual(execution.fleet_id, 2)
        names = [name for name, _ in events]
        self.assertIn("other_returns_audit_only", names)
        self.assertIn("settlement_pages_drained_globally", names)
        self.assertEqual(scheduler.fleet(2).phase, FleetPhase.RESUPPLY_REQUIRED)
        # Only the read-only screen probe: no blind or duplicate reward click.
        self.assertEqual([name for name, _ in ui.calls], ["settlement_screen"])

    def test_strict_ledger_mode_still_refuses_without_a_ledger(self):
        """The historical fail-closed contract is preserved verbatim."""
        controller, ui, _scheduler, _events = self.build(
            registry=False, ledger_audit_only=False
        )

        with self.assertRaisesRegex(
            ContinuousSettlementError, "no dispatch ledger is wired"
        ):
            controller.settle_one_returned(2, pages_drained_globally=True)

        self.assertEqual([name for name, _ in ui.calls], ["settlement_screen"])

    def test_stateless_ignores_other_returns_when_no_reward_screens_remain(self):
        """No drain flag: the old barrier must not strand a half-settled fleet."""
        persist(self.registry, 2)
        persist(self.registry, 3)
        controller, ui, scheduler, events = self.build()

        execution = controller.settle_one_returned(2)

        self.assertEqual(execution.fleet_id, 2)
        names = [name for name, _ in events]
        self.assertIn("other_returns_audit_only", names)
        self.assertIn("settlement_closed_without_reward_screens", names)
        self.assertNotIn("settlement_unattributed", names)
        self.assertEqual(scheduler.fleet(2).phase, FleetPhase.RESUPPLY_REQUIRED)
        # Every click, if any, went through a named calibrated UI control.
        self.assertTrue(
            all(
                name in (
                    "collect_return_result", "claim_bonus_items",
                    "claim_reward", "settlement_screen",
                )
                for name, _ in ui.calls
            )
        )


class StatelessDispatchLedgerWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "dispatch.sqlite3"
        self.registry = SqliteDispatchRegistry(self.path, task_kind="expedition")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def build(self, *, ledger_audit_only: bool):
        events: list[tuple[str, dict]] = []
        controller = object.__new__(ContinuousDispatchController)
        controller.dispatch_registry = self.registry
        controller.ledger_audit_only = ledger_audit_only
        controller.dispatch_source = "supervisor"
        controller.clock = lambda: datetime.now(timezone.utc)
        controller.event_sink = lambda name, fields: events.append(
            (name, dict(fields))
        )
        return controller, events

    def test_a_failed_ledger_write_is_audit_only(self):
        controller, events = self.build(ledger_audit_only=True)
        intent = SimpleNamespace(fleet_id=2, mission_id=3, ship_ids=(1, 2, 3))

        with patch.object(
            SqliteDispatchRegistry, "record_intent",
            side_effect=RuntimeError("simulated disk full"),
        ):
            controller._persist_dispatch_identity("dispatch-stateless", intent)

        names = [name for name, _ in events]
        self.assertIn("dispatch_ledger_write_failed_audit_only", names)
        self.assertEqual(
            next(fields for name, fields in events
                 if name == "dispatch_ledger_write_failed_audit_only")[
                "dispatch_blocked"
            ],
            False,
        )

    def test_strict_mode_still_propagates_a_failed_ledger_write(self):
        controller, events = self.build(ledger_audit_only=False)
        intent = SimpleNamespace(fleet_id=2, mission_id=3, ship_ids=(1, 2, 3))

        with patch.object(
            SqliteDispatchRegistry, "record_intent",
            side_effect=RuntimeError("simulated disk full"),
        ):
            with self.assertRaises(RuntimeError):
                controller._persist_dispatch_identity("dispatch-strict", intent)

        self.assertEqual(events, [])

    def test_a_failed_confirmation_write_is_audit_only(self):
        controller, events = self.build(ledger_audit_only=True)
        mission = SimpleNamespace(final_time_ms=1_700_000_000_000)

        with patch.object(
            SqliteDispatchRegistry, "record_confirmed",
            side_effect=RuntimeError("simulated disk full"),
        ):
            controller._confirm_dispatch_identity("dispatch-stateless", mission)

        self.assertIn(
            "dispatch_ledger_write_failed_audit_only",
            [name for name, _ in events],
        )


if __name__ == "__main__":
    unittest.main()
