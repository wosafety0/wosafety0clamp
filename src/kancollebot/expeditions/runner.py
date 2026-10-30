"""Single-cycle expedition coordinator behind an injected POI adapter.

The module is testable offline. It never implements POI transport itself, and
live dispatch remains disabled unless an explicit SafetyPolicy permits it.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from kancollebot.core.observed_state import ObservedState
from kancollebot.expeditions.action_journal import ExpeditionActionJournal, JournalError
from kancollebot.expeditions.planner import ExpeditionFleetSnapshot
from kancollebot.expeditions.rotation import FleetRotationPlan, select_next_expedition_fleet
from kancollebot.safety.action_preflight import authorize_planned_action
from kancollebot.safety.policy import SafetyPolicy


class ExpeditionAdapter(Protocol):
    """Minimal adapter contract; a concrete POI implementation is still required."""

    def read_fleets(self) -> ObservedState[tuple[ExpeditionFleetSnapshot, ...]]:
        """Return a timestamped observation from the game client."""
        ...

    def dispatch_expedition(self, *, fleet_id: int, mission_id: int) -> None:
        """Submit one expedition dispatch to the game client."""
        ...


@dataclass(frozen=True, slots=True)
class ExpeditionCycleResult:
    """Outcome of one coordinator tick, not a claim about server acceptance."""

    status: str
    rotation: FleetRotationPlan | None
    selected_fleet_id: int | None
    detail: str


class ExpeditionRunner:
    """Coordinate one dispatch attempt per observed state, with fail-closed gates.

    A runner instance is intentionally single-process. Its in-memory duplicate
    guard is not a durable transaction log and must not be treated as one.
    """

    def __init__(self, adapter: ExpeditionAdapter, *, journal: ExpeditionActionJournal | None = None) -> None:
        self._adapter = adapter
        self._journal = journal
        self._attempted_keys: set[tuple[int, int, datetime]] = set()
        self._stopped = False

    def stop(self) -> None:
        """Prevent subsequent dispatch attempts through this runner instance."""
        self._stopped = True

    def run_once(
        self,
        *,
        mission_id: int,
        now: datetime,
        max_state_age: timedelta,
        policy: SafetyPolicy,
        confirmed: bool = False,
        preferred_fleet_ids: tuple[int, ...] = (),
    ) -> ExpeditionCycleResult:
        """Read state, choose a fleet, and optionally request one dispatch.

        The safety policy is checked immediately before the adapter call. A
        duplicate key is recorded before dispatch to prefer at-most-once
        behavior if the adapter times out after the server accepted the action.
        """
        if mission_id < 1:
            raise ValueError("mission_id must be positive")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        if max_state_age < timedelta(0):
            raise ValueError("max_state_age must not be negative")
        if self._stopped:
            return ExpeditionCycleResult("stopped", None, None, "Runner is stopped.")

        state = self._adapter.read_fleets()
        rotation = select_next_expedition_fleet(
            state.payload,
            now=now,
            preferred_fleet_ids=preferred_fleet_ids,
        )
        fleet_id = rotation.selected_fleet_id
        if fleet_id is None:
            return ExpeditionCycleResult(
                "no_ready_fleet", rotation, None, rotation.reason
            )

        key = (fleet_id, mission_id, state.observed_at)
        if key in self._attempted_keys:
            return ExpeditionCycleResult(
                "duplicate_blocked",
                rotation,
                fleet_id,
                "A dispatch attempt already exists for this fleet, mission, and observation.",
            )

        try:
            authorize_planned_action(
                "dispatch_expedition",
                state,
                now=now,
                max_state_age=max_state_age,
                policy=policy,
                confirmed=confirmed,
            )
        except (ValueError, PermissionError) as exc:
            return ExpeditionCycleResult(
                "blocked_by_preflight", rotation, fleet_id, str(exc)
            )

        if policy.allow_live_actions and self._journal is None:
            return ExpeditionCycleResult(
                "journal_unavailable", rotation, fleet_id,
                "A durable action journal is required for live dispatch.",
            )

        # Reserve before any external action. A failed journal write must block
        # dispatch, and an ambiguous attempt must never be retried automatically.
        if self._journal is not None:
            try:
                if not self._journal.reserve(fleet_id, mission_id, state.observed_at):
                    return ExpeditionCycleResult(
                        "duplicate_blocked", rotation, fleet_id,
                        "An unresolved journal record already blocks this fleet or this observation.",
                    )
            except (OSError, sqlite3.Error, JournalError) as exc:
                return ExpeditionCycleResult(
                    "journal_unavailable", rotation, fleet_id, type(exc).__name__,
                )
        self._attempted_keys.add(key)
        try:
            self._adapter.dispatch_expedition(fleet_id=fleet_id, mission_id=mission_id)
        except Exception as exc:
            if self._journal is not None:
                try:
                    self._journal.transition(
                        fleet_id, mission_id, state.observed_at,
                        state="unknown", detail="Adapter raised during dispatch",
                    )
                except (OSError, sqlite3.Error, JournalError):
                    pass
            return ExpeditionCycleResult(
                "dispatch_outcome_unknown", rotation, fleet_id,
                "Adapter raised after dispatch began; do not retry automatically: "
                + type(exc).__name__,
            )

        if self._journal is not None:
            try:
                self._journal.transition(
                    fleet_id, mission_id, state.observed_at,
                    state="submitted", detail="Dispatch call returned; acceptance unverified",
                )
            except (OSError, sqlite3.Error, JournalError):
                return ExpeditionCycleResult(
                    "journal_unavailable", rotation, fleet_id,
                    "Dispatch returned but journal update failed; do not retry.",
                )

        # A fresh observation is required; a successful adapter return alone
        # cannot establish server acceptance.
        try:
            after = self._adapter.read_fleets()
            if after.observed_at <= state.observed_at:
                raise ValueError("post-dispatch observation is not newer")
            if not after.source_freshness_verified or after.source != state.source:
                raise ValueError("post-dispatch source freshness or identity is unverified")
            observed = next(
                (fleet for fleet in after.payload if fleet.fleet_id == fleet_id),
                None,
            )
            accepted = observed is not None and observed.mission_id == mission_id
        except Exception:
            accepted = False

        if not accepted:
            if self._journal is not None:
                try:
                    self._journal.transition(
                        fleet_id, mission_id, state.observed_at,
                        state="unknown", detail="Post-dispatch observation did not confirm mission",
                    )
                except (OSError, sqlite3.Error, JournalError):
                    pass
            return ExpeditionCycleResult(
                "dispatch_outcome_unknown", rotation, fleet_id,
                "Dispatch returned but fresh game state did not confirm the mission.",
            )

        if self._journal is not None:
            try:
                self._journal.transition(
                    fleet_id, mission_id, state.observed_at,
                    state="confirmed", detail="Fresh game state reports requested mission",
                )
            except (OSError, sqlite3.Error, JournalError):
                return ExpeditionCycleResult(
                    "journal_unavailable", rotation, fleet_id,
                    "Mission observed but durable confirmation could not be saved.",
                )
        return ExpeditionCycleResult(
            "dispatch_confirmed", rotation, fleet_id,
            "A newer game-state observation reports the requested mission.",
        )
