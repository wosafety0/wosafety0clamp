"""Decide whether a process may settle a reward chain it found already on screen.

A run can start on one of the three reward screens a previous process left open
(live 2026-11-05: an interrupted settlement, a restart, or a return that
happened while nothing was watching). Those screens advance on a click
anywhere, so the only two things that stand between "resume it" and "walk an
unknown chain blind" are:

1. proving which screen is on display, and
2. proving which fleet it belongs to.

This module owns those two decisions and nothing else. It performs no I/O, no
screen capture and no clicks; the caller passes in one read-only observation
and one screen proof, and gets back an allow/refuse verdict with a reason.

The freshness rules of the continuous monitor are deliberately NOT consulted
here. A reward screen blocks the very navigation that would produce a fresh
port witness, so requiring one would deadlock exactly the case this module
exists to resolve. The settlement controller is what replaces that gate, and it
proves every screen it advances.
"""
from __future__ import annotations

from dataclasses import dataclass

from kancollebot.expeditions.continuous_scheduler import MissionTuple
from kancollebot.expeditions.planner import ExpeditionFleetSnapshot

# The reward chain, in order. Each screen accepts a click anywhere.
REWARD_SCREEN_STATES: tuple[str, ...] = (
    "HOME_PORT_RETURN_NOTICE",
    "RETURN_RESULT",
    "BONUS_ITEMS",
)

# The composited layer is the only layer these three screens live on. Live
# 2026-11-05 the same frame scored HOME_PORT 0.9986 on
# Page.captureScreenshot(fromSurface=True) and HOME_PORT_RETURN_NOTICE 0.9987 on
# the composited one, so a default-layer reading is audit data, never evidence.
COMPOSITED_EVIDENCE = "composited_layer_ooi_fingerprints"


class StartupSettlementRefused(RuntimeError):
    """The visible reward chain cannot be attributed, so nothing may be clicked."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


@dataclass(frozen=True, slots=True)
class StartupScreen:
    """What the composited layer proved about the screen at process start."""

    state: str | None
    evidence: str = COMPOSITED_EVIDENCE
    default_surface_state: str | None = None

    @property
    def is_reward_screen(self) -> bool:
        return self.state in REWARD_SCREEN_STATES

    @property
    def layers_disagree(self) -> bool:
        """True when the default layer would have told a different story."""
        return (
            self.default_surface_state is not None
            and self.default_surface_state != self.state
        )


def select_startup_screen(
    *, composited_state: str, default_surface_state: str | None = None
) -> StartupScreen:
    """Pick the startup screen from the composited layer alone.

    A reward screen counts only when the composited capture proves it. The
    default layer can neither add a reward screen nor remove one: it drops the
    overlay the return notice is drawn on, so trusting it would send the Home
    Port recovery into a chain it cannot see.
    """
    if not isinstance(composited_state, str) or not composited_state:
        raise ValueError("composited_state must be a non-empty classification string")
    state = composited_state if composited_state in REWARD_SCREEN_STATES else None
    return StartupScreen(
        state=state,
        default_surface_state=default_surface_state,
    )


def resolve_recorded_composition(
    *, mission: MissionTuple | None, intent: object | None
) -> tuple[tuple[int, ...], str] | None:
    """The ship-ID set recorded when this fleet was last dispatched.

    The reward screens name the expedition, not the fleet, so the composition
    recorded at dispatch is the only durable fact that ties a result to a fleet.
    The scheduler's mission wins because it is the same record settlement
    credits; a leftover dispatch intent is the fallback for a fleet whose phase
    was lost (blocked, or a restart before adoption). Returns None when neither
    exists, which the caller must treat as "cannot attribute".
    """
    if mission is not None and mission.ship_ids:
        return tuple(mission.ship_ids), "scheduler_mission"
    if intent is not None:
        raw = getattr(intent, "ship_ids", None) or ()
        ship_ids = tuple(int(ship_id) for ship_id in raw)
        if ship_ids:
            mission_id = getattr(intent, "mission_id", "?")
            return ship_ids, f"dispatch_intent_mission_{mission_id}"
    return None


@dataclass(frozen=True, slots=True)
class StartupAttribution:
    """Everything the fleet-attribution check looked at."""

    fleet_id: int
    observed_ship_ids: tuple[int, ...]
    expected_ship_ids: tuple[int, ...] | None
    expected_source: str | None
    other_fleets_awaiting_result: tuple[int, ...] = ()
    fleet_mission_status: int | None = None


@dataclass(frozen=True, slots=True)
class StartupSettlementDecision:
    allowed: bool
    reason: str
    screen: str | None
    attribution: StartupAttribution | None = None
    attribution_source: str | None = None


def _refuse(reason: str, screen: str | None, attribution: StartupAttribution | None) -> StartupSettlementDecision:
    return StartupSettlementDecision(
        allowed=False, reason=reason, screen=screen, attribution=attribution
    )


def evaluate_startup_settlement(
    *,
    fleet_id: int,
    screen: StartupScreen,
    snapshot: ExpeditionFleetSnapshot | None,
    expected_ship_ids: tuple[int, ...] | None,
    expected_ship_ids_source: str | None,
    other_fleets_awaiting_result: tuple[int, ...] = (),
    exclusive_pending_fleet: bool = False,
) -> StartupSettlementDecision:
    """Allow the startup chain only when the screen and the fleet are both proven.

    Every refusal is zero-click by construction: the caller raises on
    ``allowed is False`` before it asks the driver for anything.

    ``exclusive_pending_fleet`` is a second, weaker attribution source for a
    fleet that was dispatched by hand and therefore has no recorded composition
    to compare against: a 帰航 notice exists only for a fleet whose result has
    not been accepted, so when the full Poi snapshot shows exactly one such
    fleet, the proven notice belongs to it. It is accepted only for the notice
    itself - a result or bonus screen can still belong to a fleet whose result
    was already accepted and whose screens were left open.
    """
    if not screen.is_reward_screen:
        return _refuse("no_proven_reward_screen", screen.state, None)
    if snapshot is None:
        return _refuse("poi_reported_no_fleet_record", screen.state, None)

    observed = tuple(snapshot.ship_ids)
    attribution = StartupAttribution(
        fleet_id=fleet_id,
        observed_ship_ids=observed,
        expected_ship_ids=tuple(expected_ship_ids) if expected_ship_ids else None,
        expected_source=expected_ship_ids_source,
        other_fleets_awaiting_result=tuple(other_fleets_awaiting_result),
        fleet_mission_status=snapshot.mission_status,
    )
    if not observed:
        return _refuse("observed_ship_ids_unavailable", screen.state, attribution)
    if snapshot.mission_status == 1:
        # A fleet that is still out cannot own a result screen.
        return _refuse("fleet_is_still_on_expedition", screen.state, attribution)
    if other_fleets_awaiting_result:
        # Two unaccepted results: the screens say which expedition, not which
        # fleet, so one fleet's chain could be credited to the other.
        return _refuse("other_fleets_awaiting_result", screen.state, attribution)
    if not expected_ship_ids:
        if (
            screen.state == "HOME_PORT_RETURN_NOTICE"
            and exclusive_pending_fleet
            and snapshot.mission_status == 2
        ):
            # The notice itself is the attribution: it exists only for a fleet
            # whose result is unaccepted, and this is the only such fleet.
            return StartupSettlementDecision(
                allowed=True,
                reason="exclusive_pending_fleet_attribution",
                screen=screen.state,
                attribution=attribution,
                attribution_source="exclusive_pending_fleet",
            )
        return _refuse("recorded_composition_unavailable", screen.state, attribution)
    if set(observed) != set(expected_ship_ids):
        return _refuse("observed_composition_does_not_match_recorded", screen.state, attribution)
    return StartupSettlementDecision(
        allowed=True,
        reason="screen_proven_and_fleet_attributed",
        screen=screen.state,
        attribution=attribution,
        attribution_source=expected_ship_ids_source,
    )
