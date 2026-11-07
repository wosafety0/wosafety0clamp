"""P0 R1: static guard for the package dependency direction.

Uses the AST, not a text grep: comments/docstrings that *mention* ``expeditions``
must not be reported, and ``__pycache__/*.pyc`` binaries must never be scanned.
The guard is deliberately exercised against a synthetic violation too, so a
silently-passing guard cannot be mistaken for proof.
"""
from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

# Only the leaf ``runtime_config`` and other core modules may be imported by
# core. Adapters may use core / adapters / calibration / safety, but never a
# task implementation. task_runtime may use core / task_scope / runtime_config,
# but never a concrete task or an adapter.
RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("kancollebot/core", ("kancollebot.adapters", "kancollebot.expeditions",
                          "kancollebot.task_runtime", "kancollebot.calibration")),
    ("kancollebot/adapters", ("kancollebot.expeditions", "kancollebot.task_runtime")),
    ("kancollebot/task_runtime", ("kancollebot.expeditions", "kancollebot.adapters")),
)


def _module_name(path: Path, root: Path) -> tuple[str, bool]:
    parts = list(path.relative_to(root).with_suffix("").parts)
    is_package = parts[-1] == "__init__"
    if is_package:
        parts.pop()
    return ".".join(parts), is_package


def _absolute_target(module: str, is_package: bool, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    base = module if is_package else module.rpartition(".")[0]
    parts = base.split(".")
    up = node.level - 1
    if up:
        parts = parts[:-up] if up <= len(parts) else []
    prefix = ".".join(parts)
    return f"{prefix}.{node.module}" if node.module else prefix


def find_forbidden_imports(
    root: Path, rules: tuple[tuple[str, tuple[str, ...]], ...] = RULES
) -> list[str]:
    """Return ``file:line: target`` strings for every forbidden import."""
    violations: list[str] = []
    for scope, forbidden in rules:
        scope_dir = root / scope
        if not scope_dir.is_dir():
            continue
        for path in sorted(scope_dir.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            module, is_package = _module_name(path, root)
            # utf-8-sig tolerates a pre-existing BOM in some source files; the
            # guard must report dependency violations, not encoding trivia.
            source = path.read_text(encoding="utf-8-sig")
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    targets = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    targets = [_absolute_target(module, is_package, node)]
                else:
                    continue
                for target in targets:
                    if any(target == bad or target.startswith(bad + ".") for bad in forbidden):
                        violations.append(
                            f"{path.relative_to(root)}:{node.lineno}: {target}"
                        )
    return violations


class ImportDirectionGuardTests(unittest.TestCase):
    def test_production_tree_has_no_reverse_dependencies(self):
        self.assertEqual(find_forbidden_imports(SRC), [])

    def test_guard_actually_detects_a_violation(self):
        # A vacuous guard (bad glob / wrong root / swallowed parse error) would
        # pass the test above; prove it fails on a real forbidden import.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "kancollebot" / "adapters" / "poi"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "bad.py").write_text(
                "from kancollebot.expeditions.planner import ExpeditionFleetSnapshot\n",
                encoding="utf-8",
            )
            hits = find_forbidden_imports(root)
        self.assertEqual(len(hits), 1)
        self.assertIn("kancollebot.expeditions.planner", hits[0])

    def test_guard_ignores_docstring_mentions_and_relative_imports_within_scope(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "kancollebot" / "adapters" / "poi"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "ok.py").write_text(
                '"""This adapter never imports kancollebot.expeditions."""\n'
                "from .sibling import helper\n"
                "from ..ui_operation_lock import UiOperationArbiter\n"
                "from kancollebot.core.observation_contracts import UiStateEvidence\n",
                encoding="utf-8",
            )
            hits = find_forbidden_imports(root)
        self.assertEqual(hits, [])

    def test_guard_detects_relative_reverse_import(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "kancollebot" / "adapters" / "poi"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "bad.py").write_text(
                "from ...expeditions.planner import ExpeditionFleetSnapshot\n",
                encoding="utf-8",
            )
            hits = find_forbidden_imports(root)
        self.assertEqual(len(hits), 1)
        self.assertIn("kancollebot.expeditions.planner", hits[0])

    def test_legacy_names_are_aliases_of_the_same_core_objects(self):
        """Pure relocation: no second class, so isinstance/equality still hold."""
        from kancollebot import core
        from kancollebot.core import fleet_availability as core_fa
        from kancollebot.core import observation_contracts as core_oc
        from kancollebot.core import task_attribution
        from kancollebot.expeditions import (
            continuous_dispatch_controller as cdc,
            dock_composition_planner as dcp,
            fleet_availability as exp_fa,
            mission_eligibility as me,
            planner,
        )
        from kancollebot.expeditions import continuous_scheduler as cs
        from kancollebot.adapters.poi import ui_operation_lock as lock

        self.assertIs(planner.ExpeditionFleetSnapshot, core_oc.ObservedFleetSnapshot)
        self.assertIs(cdc.UiStateEvidence, core_oc.UiStateEvidence)
        self.assertIs(me.FleetCompositionSnapshot, core_oc.FleetCompositionSnapshot)
        self.assertIs(dcp.DockShip, core_oc.DockShip)
        self.assertIs(dcp.DockInventoryError, core_oc.DockInventoryError)
        self.assertIs(dcp.dock_ships_from_poi_payloads, core_oc.dock_ships_from_poi_payloads)
        self.assertIs(exp_fa.FleetAvailabilityError, core_fa.FleetAvailabilityError)
        self.assertIs(exp_fa.normalize_unlocked_fleet_ids, core_fa.normalize_unlocked_fleet_ids)
        self.assertIs(cs.UiOperationArbiter, lock.UiOperationArbiter)
        # The task-specific policy resolver stays in the expedition package.
        self.assertTrue(callable(exp_fa.resolve_fleet_availability))
        self.assertTrue(callable(task_attribution.apply_trusted_task_attribution))
        self.assertIsNotNone(core)

    def test_adapters_use_core_shapes_and_keep_object_identity(self):
        """Adapters import neutral shapes; the objects are the same classes."""
        from kancollebot.adapters.poi import (
            cdp_ui_driver,
            continuous_expedition_ui,
            dock_reader,
            expedition_reader,
            expedition_state_v2,
            visible_ui_refresh,
        )
        from kancollebot.core import observation_contracts as core_oc
        from kancollebot.adapters.poi import ui_operation_lock as lock

        for module in (cdp_ui_driver, continuous_expedition_ui, visible_ui_refresh):
            self.assertIs(module.UiStateEvidence, core_oc.UiStateEvidence)
        self.assertIs(dock_reader.DockShip, core_oc.DockShip)
        self.assertIs(dock_reader.FleetCompositionSnapshot, core_oc.FleetCompositionSnapshot)
        for module in (expedition_reader, expedition_state_v2):
            self.assertIs(module.ObservedFleetSnapshot, core_oc.ObservedFleetSnapshot)
        self.assertIs(visible_ui_refresh.UiOperationArbiter, lock.UiOperationArbiter)

    def test_shared_capability_inventory_was_not_expanded_to_fake_decoupling(self):
        """R1 was solved by moving code, not by relabelling more things shared."""
        from kancollebot.task_scope import CapabilityBoundary, SHARED_CAPABILITIES

        expected = {
            "poi_ui_mutex": CapabilityBoundary.TASK_NEUTRAL,
            "visual_dependency_gate": CapabilityBoundary.TASK_NEUTRAL,
            "append_only_event_sink": CapabilityBoundary.TASK_NEUTRAL,
            "fleet_snapshot": CapabilityBoundary.NEEDS_DECOUPLING,
            "resupply_preflight": CapabilityBoundary.NEEDS_DECOUPLING,
            "supply_tab_coordinates": CapabilityBoundary.NEEDS_DECOUPLING,
            "expedition_composition_checks": CapabilityBoundary.TASK_SPECIFIC,
            "expedition_dispatch_transaction": CapabilityBoundary.TASK_SPECIFIC,
        }
        self.assertEqual(
            {item.name: item.boundary for item in SHARED_CAPABILITIES}, expected
        )

    def test_only_expedition_remains_implemented(self):
        """No combat/sparkle/composition/quests task was enabled by this work."""
        from kancollebot.task_scope import IMPLEMENTED_TASK_KINDS, TaskKind

        self.assertEqual(IMPLEMENTED_TASK_KINDS, frozenset({TaskKind.EXPEDITION}))


if __name__ == "__main__":
    unittest.main()
