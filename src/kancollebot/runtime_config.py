"""Single source of truth for local KancolleBot runtime defaults.

SCOPE WARNING: this project is not expedition-only. ``all_fleet_ids`` and the
endpoint/timing fields are task-neutral, but every fleet-permission field below
(``monitor_candidate_fleet_ids``, ``production_*``, ``deferred_*``,
``non_dispatchable_*``, ``resupply_verified_*`` and the derived
``dispatchable_fleet_ids``) is the policy of ONE task: EXPEDITION. Combat,
sparkle, composition and quest automation must declare their own policy through
``kancollebot.task_scope`` instead of reading these globals.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    # POLICY CEILING, not an account fact. The game has at most four fleets,
    # but a real account unlocks 2/3/4 progressively. Which fleets actually
    # exist must come from the observed Poi fleet set (see fleet_availability).
    all_fleet_ids: tuple[int, ...] = (1, 2, 3, 4)
    # Which TASK the fleet-permission fields below belong to. Kept as a plain
    # string to avoid a runtime_config -> task_scope import cycle; task_scope
    # validates it on lookup. Changing this is a policy change, not a config tweak.
    dispatch_policy_task_kind: str = "expedition"
    # EXPEDITION-only: candidate ceiling for the continuous monitor. Fleet 4 is
    # dispatchable in principle; the account's unlocked set decides whether it
    # participates in a given run.
    monitor_candidate_fleet_ids: tuple[int, ...] = (2, 3, 4)
    # EXPEDITION-only: production target and its mission id.
    production_dispatch_fleet_id: int = 3
    production_mission_id: int = 3
    # Static deferral is gone: a fleet that has not been unlocked on THIS
    # account is derived at runtime from the observed fleet set and placed in
    # the scheduler's deferred group for that run, not hardcoded here.
    deferred_fleet_ids: tuple[int, ...] = ()
    # EXPEDITION-only. Fleet 1 is reserved because the game does not offer it
    # on the expedition tab strip and it is the flagship SORTIE/COMBAT fleet.
    # This is a per-task policy, NOT a global fact about Fleet 1: combat
    # automation must use its own declared policy and may legitimately use
    # Fleet 1. Dispatching Fleet 1 on an expedition is still a logic error.
    non_dispatchable_fleet_ids: tuple[int, ...] = (1,)
    # EXPEDITION closed-loop only: fleets with a calibrated, unambiguous visual
    # gate for real resupply clicks (tab region, checkbox fingerprints, UI state
    # enum). Fleet 4 has a supply tab that is locatable and a template-free
    # selection check, but no captured fingerprint template, so it is NOT in
    # this set: membership here is a claim about live evidence.
    resupply_verified_fleet_ids: tuple[int, ...] = (2, 3)
    # EXPEDITION closed-loop only: fleets whose supply *chain* is calibrated in
    # code - the tab has a bounded region, the locator accepts its index, and
    # the driver can prove which tab is selected (by template, or for a fleet
    # without one, by relative brightness). Fleet 4 was calibrated from the
    # dispatch tab strip geometry on 2026-11-06 and its supply screen was driven
    # successfully by hand; the production path has NOT been re-verified on the
    # real client, which is exactly why it lives here and not above. A resupply
    # of a fleet in this set but not in ``resupply_verified_fleet_ids`` is
    # logged with ``calibration_level="structural_only"``, and its post-action
    # Poi cross-check is what catches a wrong-fleet selection.
    resupply_structurally_calibrated_fleet_ids: tuple[int, ...] = (2, 3, 4)
    poi_endpoint: str = "http://127.0.0.1:12450/mcp"
    observer_timeout_seconds: float = 130.0
    max_state_age_seconds: float = 30.0
    monitor_poll_seconds: float = 5.0
    refresh_wait_seconds: float = 45.0
    refresh_poll_seconds: float = 0.5
    refresh_cooldown_seconds: float = 30.0
    # Task-neutral: the unified run log. Caps live here and nowhere else so a
    # cleanup decision can never be made against a second, drifting copy of the
    # limits. Retention is three days by the operator's explicit requirement;
    # raising it is a disk-space decision, not a logging-quality one.
    run_log_retention_days: int = 3
    run_log_max_file_bytes: int = 8 * 1024 * 1024
    run_log_max_total_bytes: int = 64 * 1024 * 1024
    run_log_snapshot_name: str = "current_state.json"

    @property
    def observer_script(self) -> Path:
        return REPOSITORY_ROOT / "tools" / "hand_sandbox" / "poi_port_response_observer_20261029.js"

    @property
    def monitor_database(self) -> Path:
        return REPOSITORY_ROOT / "logs" / "continuous_expedition.sqlite3"

    @property
    def dispatch_database(self) -> Path:
        return REPOSITORY_ROOT / "logs" / "one_expedition_production.sqlite3"

    @property
    def dispatch_log_path(self) -> Path:
        return REPOSITORY_ROOT / "logs" / "one_expedition_ui_loop.jsonl"

    @property
    def run_log_directory(self) -> Path:
        """Project-owned directory that is the ONLY target of log cleanup.

        ``logs/`` also holds the action journal, the dispatch databases and
        calibration captures; keeping the run log in its own subdirectory means
        the cleanup rules never need a deny list of files they must not touch.
        """
        return REPOSITORY_ROOT / "logs" / "run"

    @property
    def run_log_snapshot_path(self) -> Path:
        return self.run_log_directory / self.run_log_snapshot_name

    @property
    def resupply_enabled_fleet_ids(self) -> tuple[int, ...]:
        """Fleets the closed loop may actually resupply: verified ∪ calibrated.

        The union is deliberate and narrow: a verified fleet is always
        calibrated (checked at import), so this is exactly the calibrated set.
        It exists as a named property so call sites never have to decide which
        of the two sets is "the" gate.
        """
        return tuple(
            sorted(
                set(self.resupply_verified_fleet_ids)
                | set(self.resupply_structurally_calibrated_fleet_ids)
            )
        )

    def resupply_calibration_level(self, fleet_id: int) -> str:
        """How well this fleet's supply chain is proven: live or structural only."""
        if fleet_id in self.resupply_verified_fleet_ids:
            return "live_verified"
        if fleet_id in self.resupply_structurally_calibrated_fleet_ids:
            return "structural_only"
        return "unavailable"

    @property
    def dispatchable_fleet_ids(self) -> tuple[int, ...]:
        """EXPEDITION policy ceiling of fleets that may be dispatched: (2, 3, 4).

        Derived as ``all_fleet_ids`` minus the deferred and reserved
        (``non_dispatchable``) fleets. This is authoritative for *expedition
        permission* only; other task kinds must use
        ``kancollebot.task_scope.fleet_policy_for`` and must not read this
        property. It also says nothing about which fleets a given account has
        unlocked: ``build_runtime`` and the production entry gate must
        additionally resolve the observed unlocked set through
        ``fleet_availability`` before any fleet participates in a run; never
        guess that at a call site.
        """
        reserved = set(self.deferred_fleet_ids) | set(self.non_dispatchable_fleet_ids)
        return tuple(
            fleet_id for fleet_id in self.all_fleet_ids if fleet_id not in reserved
        )


RUNTIME_CONFIG = RuntimeConfig()

if RUNTIME_CONFIG.production_dispatch_fleet_id not in RUNTIME_CONFIG.all_fleet_ids:
    raise RuntimeError("Production dispatch fleet must exist in all_fleet_ids")
if RUNTIME_CONFIG.production_dispatch_fleet_id not in RUNTIME_CONFIG.monitor_candidate_fleet_ids:
    raise RuntimeError("Production dispatch fleet must be included in monitor candidates")
if len(set(RUNTIME_CONFIG.all_fleet_ids)) != len(RUNTIME_CONFIG.all_fleet_ids):
    raise RuntimeError("all_fleet_ids must be unique")
if len(set(RUNTIME_CONFIG.monitor_candidate_fleet_ids)) != len(RUNTIME_CONFIG.monitor_candidate_fleet_ids):
    raise RuntimeError("monitor_candidate_fleet_ids must be unique")
if not set(RUNTIME_CONFIG.non_dispatchable_fleet_ids).issubset(set(RUNTIME_CONFIG.all_fleet_ids)):
    raise RuntimeError("non_dispatchable_fleet_ids must be a subset of all_fleet_ids")
if not set(RUNTIME_CONFIG.deferred_fleet_ids).issubset(set(RUNTIME_CONFIG.all_fleet_ids)):
    raise RuntimeError("deferred_fleet_ids must be a subset of all_fleet_ids")
if not set(RUNTIME_CONFIG.monitor_candidate_fleet_ids).issubset(set(RUNTIME_CONFIG.dispatchable_fleet_ids)):
    raise RuntimeError("monitor candidates must be a subset of dispatchable_fleet_ids")
if set(RUNTIME_CONFIG.deferred_fleet_ids) & set(RUNTIME_CONFIG.non_dispatchable_fleet_ids):
    raise RuntimeError("deferred and non_dispatchable fleet ids must be disjoint")
if set(RUNTIME_CONFIG.deferred_fleet_ids) & set(RUNTIME_CONFIG.monitor_candidate_fleet_ids):
    raise RuntimeError("deferred and monitor candidate fleet ids must be disjoint")
if set(RUNTIME_CONFIG.non_dispatchable_fleet_ids) & set(RUNTIME_CONFIG.monitor_candidate_fleet_ids):
    raise RuntimeError("non_dispatchable and monitor candidate fleet ids must be disjoint")
if not set(RUNTIME_CONFIG.resupply_verified_fleet_ids).issubset(
    set(RUNTIME_CONFIG.dispatchable_fleet_ids)
):
    raise RuntimeError("resupply_verified_fleet_ids must be a subset of dispatchable_fleet_ids")
if not set(RUNTIME_CONFIG.resupply_structurally_calibrated_fleet_ids).issubset(
    set(RUNTIME_CONFIG.dispatchable_fleet_ids)
):
    raise RuntimeError(
        "resupply_structurally_calibrated_fleet_ids must be a subset of dispatchable_fleet_ids"
    )
if not set(RUNTIME_CONFIG.resupply_verified_fleet_ids).issubset(
    set(RUNTIME_CONFIG.resupply_structurally_calibrated_fleet_ids)
):
    # A fleet cannot be live-verified without being calibrated in code; the
    # reverse (calibrated but not yet live-verified) is the normal case.
    raise RuntimeError(
        "resupply_verified_fleet_ids must be a subset of "
        "resupply_structurally_calibrated_fleet_ids"
    )
if RUNTIME_CONFIG.run_log_retention_days < 1:
    raise RuntimeError("run_log_retention_days must be at least 1")
if RUNTIME_CONFIG.run_log_max_file_bytes < 1024:
    raise RuntimeError("run_log_max_file_bytes must be at least 1024")
if RUNTIME_CONFIG.run_log_max_total_bytes < RUNTIME_CONFIG.run_log_max_file_bytes:
    raise RuntimeError("run_log_max_total_bytes must be at least run_log_max_file_bytes")
if not RUNTIME_CONFIG.run_log_snapshot_name.endswith(".json"):
    raise RuntimeError("run_log_snapshot_name must end with .json")
