"""Single source of truth for local KancolleBot runtime defaults."""
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
    # Candidate ceiling for the continuous monitor. Fleet 4 is now dispatchable
    # in principle; the account's unlocked set decides whether it participates
    # in a given run.
    monitor_candidate_fleet_ids: tuple[int, ...] = (2, 3, 4)
    production_dispatch_fleet_id: int = 3
    production_mission_id: int = 3
    # Static deferral is gone: a fleet that has not been unlocked on THIS
    # account is derived at runtime from the observed fleet set and placed in
    # the scheduler's deferred group for that run, not hardcoded here.
    deferred_fleet_ids: tuple[int, ...] = ()
    # Unlocked fleets (present in all_fleet_ids) that the bot must never
    # dispatch on an expedition. Fleet 1 is the flagship sortie fleet and is
    # reserved by design; dispatching it is a logic error, not a configuration
    # grey area, so it is excluded at the policy layer rather than merely left
    # out of the dispatch candidates.
    non_dispatchable_fleet_ids: tuple[int, ...] = (1,)
    # Fleets with a calibrated, unambiguous visual gate for real resupply
    # clicks (tab region, checkbox fingerprints, UI state enum). Fleet 4 has a
    # dispatch tab but NO verified supply-tab region or checkbox fingerprints,
    # so it must not be resupplied by the closed loop until that evidence is
    # captured on a real client.
    resupply_verified_fleet_ids: tuple[int, ...] = (2, 3)
    poi_endpoint: str = "http://127.0.0.1:12450/mcp"
    observer_timeout_seconds: float = 130.0
    max_state_age_seconds: float = 30.0
    monitor_poll_seconds: float = 5.0
    refresh_wait_seconds: float = 45.0
    refresh_poll_seconds: float = 0.5
    refresh_cooldown_seconds: float = 30.0

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
    def dispatchable_fleet_ids(self) -> tuple[int, ...]:
        """POLICY ceiling of fleets the bot may ever dispatch: (2, 3, 4).

        Derived as ``all_fleet_ids`` minus the deferred and reserved
        (``non_dispatchable``) fleets. This is authoritative for *permission*,
        but it says nothing about which fleets a given account has unlocked.
        ``build_runtime`` and the production entry gate must additionally
        resolve the observed unlocked set through ``fleet_availability`` before
        any fleet participates in a run; never guess that at a call site.
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
