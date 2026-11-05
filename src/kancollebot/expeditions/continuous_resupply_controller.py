"""Single-fleet resupply orchestration for the continuous expedition loop.

Drives the last step of the closed loop:

    RESUPPLY_REQUIRED -> READY

This mirrors ``continuous_settlement_controller``: one shared arbiter, one UI
driver/session, a durable event sink, and live actions disabled unless
explicitly enabled. It never opens transports, never creates game requests, and
never retries an ambiguous supply click.

Why a data provider instead of the expedition state reader: the closed loop's
``ExpeditionFleetSnapshot`` carries fleet-level mission state and ship IDs only,
while a supply cost needs each ship's current and maximum fuel/ammo. That shape
comes from the port snapshot, so the caller injects it. The controller therefore
validates the data it is given rather than assuming where it came from - and it
still refuses a snapshot whose freshness was not witnessed, or whose source is
not Poi Redux.

Resupply is deliberately *single fleet*. ``build_full_supply_plan`` guards the
UI button that supplies every unlocked fleet at once and therefore demands that
all of them be idle; in a continuous loop another fleet is almost always under
way, so that plan can never be built. ``build_fleet_supply_plan`` carries the
narrower contract and the narrower claim.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from kancollebot.adapters.poi.full_supply import (
    FleetSupplyPlan,
    FullSupplyPreflightError,
    build_fleet_supply_plan,
    verify_fleet_supply_complete,
)
from kancollebot.expeditions.continuous_dispatch_controller import UiStateEvidence
from kancollebot.expeditions.continuous_scheduler import (
    ContinuousExpeditionScheduler,
    FleetPhase,
    UiOperationArbiter,
)
from kancollebot.runtime_config import RUNTIME_CONFIG


class ContinuousResupplyError(RuntimeError):
    """The observed state or the supply transaction cannot safely continue."""


class ContinuousResupplyUiDriver(Protocol):
    """One shared visible-UI driver that can supply a single fleet.

    ``resupply_fleet`` must verify every checkpoint and return only after two
    consecutive in-memory verifications of the post-supply Home Port state. It
    must not send game API/server requests.
    """

    def resupply_fleet(self, *, fleet_id: int) -> UiStateEvidence:
        """Supply one idle fleet; return the post-supply HOME_PORT evidence."""
        ...


@dataclass(frozen=True, slots=True)
class FleetSupplyObservation:
    """One port snapshot shaped for `build_fleet_supply_plan`.

    ``fleets``/``ships``/``master_ships``/``resources`` mirror the Poi port
    payload; the freshness fields are carried rather than inferred so the
    controller can refuse an unwitnessed or stale snapshot.
    """

    fleets: Any
    ships: Any
    master_ships: Any
    resources: Any
    observed_at: datetime
    source_freshness_verified: bool
    source: str = "poi-redux:port"

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not self.source:
            raise ValueError("source must be a non-empty string")


@dataclass(frozen=True, slots=True)
class ResupplyExecution:
    """What one resupply attempt did, and what it cost."""

    fleet_id: int
    fuel_required: int
    ammo_required: int
    evidence: str | None
    already_supplied: bool


class ContinuousResupplyController:
    """Supply one fleet that the scheduler has parked in RESUPPLY_REQUIRED.

    Instantiate exactly one controller per Poi/CDP session and share its
    ``UiOperationArbiter`` with every other UI workflow.
    """

    def __init__(
        self,
        *,
        scheduler: ContinuousExpeditionScheduler,
        ui_driver: ContinuousResupplyUiDriver,
        fleet_data_provider: Callable[[], FleetSupplyObservation],
        ui_arbiter: UiOperationArbiter,
        event_sink: Callable[[str, Mapping[str, object]], None],
        allow_ui_actions: bool = False,
        max_state_age: timedelta = timedelta(seconds=30),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not callable(fleet_data_provider):
            raise ValueError("fleet_data_provider must be callable")
        if not callable(event_sink):
            raise ValueError("a durable event_sink is required")
        if max_state_age < timedelta(0):
            raise ValueError("max_state_age must not be negative")
        self.scheduler = scheduler
        self.ui_driver = ui_driver
        self.fleet_data_provider = fleet_data_provider
        self.ui_arbiter = ui_arbiter
        self.event_sink = event_sink
        self.allow_ui_actions = allow_ui_actions
        self.max_state_age = max_state_age
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def resupply_one_needed(self, fleet_id: int) -> ResupplyExecution:
        """Bring one fleet from RESUPPLY_REQUIRED to READY, or fail closed.

        A fleet that already needs nothing is confirmed without any click: the
        loop may have observed it fully supplied between ticks, and pressing the
        button then would spend a real action to change nothing.
        """
        if not self.allow_ui_actions:
            raise ContinuousResupplyError(
                "UI actions are disabled; explicit enablement is required"
            )
        if fleet_id not in self.scheduler.dispatch_fleet_ids:
            raise ContinuousResupplyError(
                f"Fleet {fleet_id} is not a configured dispatch fleet"
            )
        # Being allowed to DISPATCH a fleet does not mean we can safely
        # RESUPPLY it: a real supply click needs a locatable tab and a way to
        # prove which tab is selected. Fleet 4 is calibrated structurally (tab
        # region derived from the 45 px strip geometry, template-free highlight
        # check) but has not been re-verified on the production path, so its
        # resupply is labelled ``structural_only`` in the log and relies on the
        # post-action Poi cross-check to catch a wrong-fleet selection.
        if fleet_id not in RUNTIME_CONFIG.resupply_enabled_fleet_ids:
            raise ContinuousResupplyError(
                f"Fleet {fleet_id} has no calibrated supply visual gate; resupplyable "
                f"fleets are {list(RUNTIME_CONFIG.resupply_enabled_fleet_ids)}. "
                "Refusing to click an unlocalizable supply control. This fleet can be "
                "dispatched, but its closed-loop resupply needs real-client calibration."
            )
        calibration_level = RUNTIME_CONFIG.resupply_calibration_level(fleet_id)

        with self.ui_arbiter.operation(f"continuous-resupply-fleet-{fleet_id}"):
            current = self.scheduler.fleet(fleet_id)
            if current.phase is not FleetPhase.RESUPPLY_REQUIRED:
                raise ContinuousResupplyError(
                    f"Fleet {fleet_id} is not awaiting resupply: {current.phase.value}"
                )

            plan = self._plan(fleet_id)
            if not plan.needs_supply:
                self.scheduler.confirm_resupply(fleet_id, fully_supplied=True)
                self.event_sink(
                    "resupply_already_complete",
                    {"fleet_id": fleet_id, "fuel_required": 0, "ammo_required": 0},
                )
                return ResupplyExecution(
                    fleet_id=fleet_id,
                    fuel_required=0,
                    ammo_required=0,
                    evidence=None,
                    already_supplied=True,
                )

            self.event_sink(
                "resupply_action_started",
                {
                    "fleet_id": fleet_id,
                    "calibration_level": calibration_level,
                    # True only for a fleet whose supply screen has a captured
                    # fingerprint template; a structural-only fleet is proved
                    # by relative brightness and by the Poi check below.
                    "fingerprint_template_available": calibration_level == "live_verified",
                    "fuel_required": plan.fuel_required,
                    "ammo_required": plan.ammo_required,
                },
            )
            evidence = self.ui_driver.resupply_fleet(fleet_id=fleet_id)
            self._require_home_port_evidence(evidence)
            action_at = self.clock()

            after = self._read(newer_than=action_at)
            if not verify_fleet_supply_complete(
                fleet_id, after.fleets, after.ships, after.master_ships
            ):
                self._block_after_ambiguous(fleet_id)
                raise ContinuousResupplyError(
                    f"Fleet {fleet_id} did not verify as fully supplied after the "
                    f"supply action; blocked for manual reconciliation"
                )

            self.scheduler.confirm_resupply(fleet_id, fully_supplied=True)
            self.event_sink(
                "resupply_confirmed",
                {
                    "fleet_id": fleet_id,
                    "fuel_required": plan.fuel_required,
                    "ammo_required": plan.ammo_required,
                    "evidence": evidence.evidence,
                },
            )
            return ResupplyExecution(
                fleet_id=fleet_id,
                fuel_required=plan.fuel_required,
                ammo_required=plan.ammo_required,
                evidence=evidence.evidence,
                already_supplied=False,
            )

    def _plan(self, fleet_id: int) -> FleetSupplyPlan:
        observation = self._read()
        try:
            return build_fleet_supply_plan(
                fleet_id,
                observation.fleets,
                observation.ships,
                observation.master_ships,
                observation.resources,
                source_freshness_verified=observation.source_freshness_verified,
                observed_at=observation.observed_at,
                now=self.clock(),
                max_age=self.max_state_age,
            )
        except FullSupplyPreflightError as exc:
            raise ContinuousResupplyError(
                f"Fleet {fleet_id} supply preflight failed: {exc}"
            ) from exc

    def _read(self, *, newer_than: datetime | None = None) -> FleetSupplyObservation:
        observation = self.fleet_data_provider()
        if not isinstance(observation, FleetSupplyObservation):
            raise ContinuousResupplyError(
                "fleet_data_provider did not return a FleetSupplyObservation"
            )
        if not observation.source.startswith("poi-redux:"):
            raise ContinuousResupplyError("supply observation must originate from Poi Redux")
        if newer_than is not None and observation.observed_at <= newer_than:
            raise ContinuousResupplyError(
                "supply observation did not cross the UI action time boundary"
            )
        return observation

    @staticmethod
    def _require_home_port_evidence(evidence: UiStateEvidence) -> None:
        if not isinstance(evidence, UiStateEvidence):
            raise ContinuousResupplyError("UI driver did not return structured evidence")
        if (
            evidence.state != "HOME_PORT"
            or evidence.consecutive_states != ("HOME_PORT", "HOME_PORT")
        ):
            raise ContinuousResupplyError(
                f"Expected HOME_PORT two consecutive frames after supply; "
                f"observed {evidence.consecutive_states!r}"
            )

    def _block_after_ambiguous(self, fleet_id: int) -> None:
        try:
            self.scheduler.block(
                fleet_id,
                "continuous resupply stopped after ambiguous observation; "
                "manual reconciliation required",
            )
        except Exception:
            pass
        try:
            self.event_sink(
                "resupply_blocked",
                {
                    "fleet_id": fleet_id,
                    "reason": "ambiguous observation; automatic retry forbidden",
                },
            )
        except Exception:
            pass
