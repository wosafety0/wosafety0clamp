"""Single source of truth for local KancolleBot runtime defaults."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    all_fleet_ids: tuple[int, ...] = (1, 2, 3, 4)
    monitor_candidate_fleet_ids: tuple[int, ...] = (2, 3)
    production_dispatch_fleet_id: int = 3
    production_mission_id: int = 3
    deferred_fleet_ids: tuple[int, ...] = (4,)
    # Unlocked fleets (present in all_fleet_ids) that the bot must never
    # dispatch on an expedition. Fleet 1 is the flagship sortie fleet and is
    # reserved by design; dispatching it is a logic error, not a configuration
    # grey area, so it is excluded at the policy layer rather than merely left
    # out of the dispatch candidates.
    non_dispatchable_fleet_ids: tuple[int, ...] = (1,)
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
        """Unlocked fleets eligible to be dispatched.

        Derived as ``all_fleet_ids`` minus the deferred (not-yet-unlocked) and
        reserved (``non_dispatchable``) fleets. This is the authoritative set
        that ``build_runtime`` and the production entry gate must validate a
        dispatch target against; it must not be guessed at a call site.
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
if not set(RUNTIME_CONFIG.monitor_candidate_fleet_ids).issubset(set(RUNTIME_CONFIG.dispatchable_fleet_ids)):
    raise RuntimeError("monitor candidates must be a subset of dispatchable_fleet_ids")
