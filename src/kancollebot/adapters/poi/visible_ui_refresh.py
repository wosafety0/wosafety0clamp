"""Visible, non-mutating Poi UI navigation used to obtain a natural port witness.

This adapter only navigates HOME_PORT -> SORTIE_MENU -> EXPEDITION_LIST ->
HOME_PORT. It never selects a mission/fleet, dispatches, collects rewards,
resupplies, reloads the page, or creates a game-server request. Every click is
single-shot and requires two-frame state evidence before the next click.
"""
from __future__ import annotations

import inspect
import math
import re
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from kancollebot.adapters.poi.cdp_input import RecordedPoint, home_port_point
from kancollebot.adapters.poi.ui_operation_lock import UiOperationArbiter
from kancollebot.core.observation_contracts import UiStateEvidence
from kancollebot.core.return_refresh import (
    ReturnRefreshCoordinator,
    ReturnRefreshOutcome,
    ReturnRefreshRefused,
)


@dataclass(frozen=True, slots=True)
class FleetReturnView:
    """The three facts return detection is allowed to look at, per fleet.

    Deliberately narrow: the coordinator must not be able to conclude
    anything from the rest of a port payload, and an unknown field must read
    as "no evidence" rather than as a default that looks like a return.
    """

    fleet_id: int
    mission_status: int | None
    mission_id: int | None
    source_freshness_verified: bool
    observed_at: datetime | None


HOME_PORT = home_port_point()
SORTIE = RecordedPoint(280, 425, "sortie")
EXPEDITION_MODE = RecordedPoint(1000, 360, "expedition-mode")


def _safe_cdp_diagnostic(exc: BaseException) -> dict[str, object]:
    """Extract only allowlisted CDP metadata; never persist arbitrary exception text."""
    current: BaseException | None = exc
    while current is not None:
        message = str(current)
        patterns = (
            (r"^CDP command failed: ([A-Za-z][A-Za-z0-9.]*) \(code=(-?\d+)\)$", "cdp_error_response"),
            (r"^CDP command failed: ([A-Za-z][A-Za-z0-9.]*)$", "cdp_error_response"),
            (r"^CDP command outcome is unknown for ([A-Za-z][A-Za-z0-9.]*)", "cdp_command_outcome_unknown"),
            (r"^CDP reader failed while awaiting ([A-Za-z][A-Za-z0-9.]*)", "cdp_reader_failed"),
            (r"^CDP transport closed while awaiting ([A-Za-z][A-Za-z0-9.]*)$", "cdp_transport_closed"),
            (r"^CDP returned an invalid result for ([A-Za-z][A-Za-z0-9.]*)$", "cdp_invalid_result"),
        )
        for pattern, category in patterns:
            match = re.match(pattern, message)
            if match:
                result: dict[str, object] = {
                    "diagnostic_category": category,
                    "cdp_method": match.group(1),
                }
                if match.lastindex == 2:
                    result["cdp_error_code"] = int(match.group(2))
                return result
        if message == "CDP transport is closed or unusable":
            return {"diagnostic_category": "cdp_transport_unusable"}
        current = current.__cause__ or current.__context__
    return {"diagnostic_category": "unclassified"}


class VisibleUiRefreshError(RuntimeError):
    """A visible navigation checkpoint failed; no automatic click retry is allowed."""


class VisibleClickInput(Protocol):
    def click(self, point: RecordedPoint) -> None:
        """Send one visible mouse click after validating the current viewport."""
        ...


class FleetStateReader(Protocol):
    def read_fleets(self) -> Any:
        """Return one normalized Poi fleet observation."""
        ...


class VisibleUiPortRefreshDriver:
    """Refresh Poi's current port data through the already-reviewed visible UI path."""

    _STEPS = (
        ("open_sortie", SORTIE, "SORTIE_MENU"),
        ("open_expedition_list", EXPEDITION_MODE, "EXPEDITION_LIST"),
        ("return_home_port", HOME_PORT, "HOME_PORT"),
    )

    def __init__(
        self,
        *,
        game_input: VisibleClickInput,
        verify_state: Callable[[str], UiStateEvidence],
        ui_arbiter: UiOperationArbiter,
        event_sink: Callable[[str, Mapping[str, object]], None],
        sleep: Callable[[float], None] = time.sleep,
        settle_seconds: float = 1.0,
    ) -> None:
        if not callable(getattr(game_input, "click", None)):
            raise ValueError("game_input must provide click(point)")
        if not callable(verify_state) or not isinstance(ui_arbiter, UiOperationArbiter):
            raise ValueError("a state verifier and shared UI arbiter are required")
        if not callable(event_sink) or not callable(sleep):
            raise ValueError("event_sink and sleep must be callable")
        if not math.isfinite(settle_seconds) or settle_seconds < 0:
            raise ValueError("settle_seconds must be finite and non-negative")
        self._input = game_input
        self._verify_state = verify_state
        self._arbiter = ui_arbiter
        self._event_sink = event_sink
        self._sleep = sleep
        self._settle_seconds = settle_seconds
        # Audit context for the navigation currently in flight. Set only for the
        # duration of refresh_port_state so every step it emits can be joined to
        # the refresh_id and origin of the caller that asked for it.
        self._refresh_context: dict[str, object] | None = None
        self.last_refresh_stats: dict[str, object] | None = None

    def _confirm(self, expected: str, *, phase: str) -> UiStateEvidence:
        evidence = self._verify_state(expected)
        if not isinstance(evidence, UiStateEvidence):
            raise VisibleUiRefreshError("UI verifier returned no structured two-frame evidence")
        if (
            evidence.state != expected
            or evidence.consecutive_states != (expected, expected)
        ):
            raise VisibleUiRefreshError(
                f"Expected two consecutive {expected} states during {phase}; "
                f"observed {evidence.consecutive_states!r}"
            )
        return evidence

    def _emit(self, event: str, fields: Mapping[str, object]) -> None:
        """Emit one refresh event, stamped with the in-flight audit context."""
        if self._refresh_context:
            fields = {**fields, **self._refresh_context}
        self._event_sink(event, fields)

    def refresh_port_state(
        self,
        *,
        refresh_id: str | None = None,
        origin: str | None = None,
    ) -> UiStateEvidence:
        """Perform the exact three-click navigation once; never retry an ambiguous click.

        ``refresh_id`` and ``origin`` are audit-only: they never change what is
        clicked, how often, or what counts as a verified screen. They exist so a
        single refresh can be traced back to the caller that asked for it -
        without them every navigation looks identical in the event log, which is
        how 27 navigations once hid behind two dispatches.
        """
        if refresh_id is not None and (not isinstance(refresh_id, str) or not refresh_id.strip()):
            raise ValueError("refresh_id must be a non-empty string when given")
        if origin is not None and (not isinstance(origin, str) or not origin.strip()):
            raise ValueError("origin must be a non-empty string when given")
        clicks_attempted = 0
        clicks_completed = 0
        current_state = "UNKNOWN"
        self._refresh_context = {
            key: value
            for key, value in (("refresh_id", refresh_id), ("refresh_origin", origin))
            if value
        }
        self.last_refresh_stats = {
            "refresh_id": refresh_id,
            "origin": origin,
            "clicks_attempted": 0,
            "clicks_completed": 0,
            "final_state": "UNKNOWN",
            "steps": [action for action, _, _ in self._STEPS],
            "outcome": "started",
        }
        try:
            with self._arbiter.operation("visible-poi-port-refresh"):
                initial = self._confirm("HOME_PORT", phase="refresh precondition")
                current_state = initial.state
                self._emit("ui_refresh_started", {
                    "initial_state": current_state,
                    "evidence": initial.evidence,
                    "game_actions_enabled": False,
                })
                final_evidence = initial
                for index, (action, point, expected_after) in enumerate(self._STEPS, start=1):
                    self._emit("ui_refresh_action_precondition", {
                        "step": index,
                        "action": action,
                        "target": point.label,
                        "expected_state": current_state,
                        "clicks_attempted": clicks_attempted,
                        "clicks_completed": clicks_completed,
                    })
                    clicks_attempted += 1
                    self._emit("ui_refresh_click_attempted", {
                        "step": index,
                        "action": action,
                        "target": point.label,
                        "clicks_attempted": clicks_attempted,
                    })
                    self._input.click(point)
                    clicks_completed += 1
                    self._emit("ui_refresh_click_sent", {
                        "step": index,
                        "action": action,
                        "target": point.label,
                        "clicks_sent": clicks_completed,
                    })
                    if self._settle_seconds:
                        self._sleep(self._settle_seconds)
                    self._emit("ui_refresh_wait_completed", {
                        "step": index,
                        "action": action,
                        "wait_seconds": self._settle_seconds,
                    })
                    try:
                        final_evidence = self._confirm(
                            expected_after, phase=f"after {action}"
                        )
                    except VisibleUiRefreshError:
                        # A click whose precondition is *re-proven* afterwards
                        # demonstrably did nothing - the client is still on the
                        # screen we started from. That is not an ambiguous
                        # outcome, so one repeat is safe; anything else is
                        # ambiguous and stays forbidden (live 2026-11-05: a
                        # 出撃 click was swallowed during a transition and ended
                        # a run that had nothing else wrong with it).
                        # The wait matters: a click sent while the client is
                        # still fading between screens is swallowed, and the
                        # proof window is shorter than the fade. Live
                        # 2026-11-05 the port's fade outlasted the proof, the
                        # re-proof then failed on a half-rendered frame, and
                        # the whole refresh was declared ambiguous.
                        if self._settle_seconds:
                            self._sleep(self._settle_seconds * 2)
                            self._emit("ui_refresh_transition_settle_wait", {
                                "step": index,
                                "action": action,
                                "wait_seconds": self._settle_seconds * 2,
                                "clicks_sent": 0,
                            })
                        self._confirm(current_state, phase=f"{action} had no effect")
                        self._emit("ui_refresh_click_repeated", {
                            "step": index,
                            "action": action,
                            "target": point.label,
                            "state_after_first_click": current_state,
                            "reason": "precondition re-proven after the click",
                        })
                        clicks_attempted += 1
                        self._input.click(point)
                        clicks_completed += 1
                        if self._settle_seconds:
                            self._sleep(self._settle_seconds)
                        final_evidence = self._confirm(
                            expected_after, phase=f"after repeated {action}"
                        )
                    current_state = final_evidence.state
                    self._emit("ui_refresh_state_confirmed", {
                        "step": index,
                        "action": action,
                        "state": current_state,
                        "consecutive_states": final_evidence.consecutive_states,
                        "evidence": final_evidence.evidence,
                    })
                self._emit("ui_refresh_completed", {
                    "sequence": ["HOME_PORT", "SORTIE_MENU", "EXPEDITION_LIST", "HOME_PORT"],
                    "clicks_sent": clicks_completed,
                    "final_state": current_state,
                })
                self.last_refresh_stats = {
                    "refresh_id": refresh_id,
                    "origin": origin,
                    "clicks_attempted": clicks_attempted,
                    "clicks_completed": clicks_completed,
                    "final_state": current_state,
                    "steps": [action for action, _, _ in self._STEPS],
                    "outcome": "completed",
                }
                return final_evidence
        except Exception as exc:
            try:
                self._emit("ui_refresh_blocked", {
                    "clicks_attempted": clicks_attempted,
                    "clicks_completed": clicks_completed,
                    "outcome_ambiguous": clicks_attempted > clicks_completed,
                    "last_confirmed_state": current_state,
                    "error_type": type(exc).__name__,
                    **_safe_cdp_diagnostic(exc),
                    "reason": "checkpoint failed or UI action outcome is ambiguous; automatic retry forbidden",
                })
            except Exception:
                pass
            self.last_refresh_stats = {
                "refresh_id": refresh_id,
                "origin": origin,
                "clicks_attempted": clicks_attempted,
                "clicks_completed": clicks_completed,
                "final_state": current_state,
                "steps": [action for action, _, _ in self._STEPS],
                "outcome": "blocked",
                "error_type": type(exc).__name__,
            }
            if isinstance(exc, VisibleUiRefreshError):
                raise
            raise VisibleUiRefreshError(
                f"Visible Poi refresh stopped after {clicks_attempted} click attempt(s): {type(exc).__name__}"
            ) from exc
        finally:
            self._refresh_context = None


class FreshnessRefreshingStateReader:
    """Obtain a fresh witness through visible navigation when a caller asks.

    Reading is separate from refreshing. :meth:`read_fleets` is pure observation
    and never touches the game; :meth:`ensure_fresh` is the only method that may
    navigate, and it requires the caller to name itself. A single refresh sends
    at most one navigation sequence, and subsequent requests respect the
    cooldown. If no matching natural response arrives before the bounded wait
    expires, the last unverified observation is returned unchanged so the
    continuous monitor remains fail-closed.
    """

    def __init__(
        self,
        reader: FleetStateReader,
        refresh_driver: VisibleUiPortRefreshDriver,
        *,
        max_wait_seconds: float = 45.0,
        poll_interval_seconds: float = 0.5,
        refresh_cooldown_seconds: float = 30.0,
        event_sink: Callable[[str, Mapping[str, object]], None] | None = None,
        refresh_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        values = (max_wait_seconds, poll_interval_seconds, refresh_cooldown_seconds)
        if any(not math.isfinite(value) for value in values):
            raise ValueError("refresh timing values must be finite")
        if max_wait_seconds <= 0 or poll_interval_seconds <= 0 or refresh_cooldown_seconds < 0:
            raise ValueError("refresh wait/poll must be positive and cooldown non-negative")
        if not callable(getattr(reader, "read_fleets", None)):
            raise ValueError("reader must provide read_fleets()")
        if not callable(getattr(refresh_driver, "refresh_port_state", None)):
            raise ValueError("refresh_driver must provide refresh_port_state()")
        if not callable(monotonic) or not callable(sleep):
            raise ValueError("monotonic and sleep must be callable")
        if event_sink is not None and not callable(event_sink):
            raise ValueError("event_sink must be callable when given")
        if refresh_id_factory is not None and not callable(refresh_id_factory):
            raise ValueError("refresh_id_factory must be callable when given")
        if clock is not None and not callable(clock):
            raise ValueError("clock must be callable when given")
        self._reader = reader
        self._refresh_driver = refresh_driver
        self._max_wait_seconds = max_wait_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._refresh_cooldown_seconds = refresh_cooldown_seconds
        self._monotonic = monotonic
        self._sleep = sleep
        self._refresh_lock = threading.Lock()
        self._last_refresh_at: float | None = None
        self._event_sink = event_sink
        self._refresh_id_factory = refresh_id_factory or (
            lambda: f"rfr-{uuid.uuid4().hex[:12]}"
        )
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        # Observation-round token. Every refresh the reader is about to send
        # re-checks that no newer round (or a shutdown) started while the
        # caller was waiting for the lock; a stale round must not click.
        self._generation = 0
        self._return_refresh = ReturnRefreshCoordinator(
            clock=self._clock,
            monotonic=self._monotonic,
            sleep=self._sleep,
            event_sink=event_sink,
            min_refresh_interval_seconds=refresh_cooldown_seconds,
        )
        # A driver built before the audit fields existed still takes no keyword
        # arguments; asking it for audit fields would break a caller that only
        # wants the navigation.
        self._driver_takes_audit = FreshnessRefreshingStateReader._accepts_audit(
            refresh_driver.refresh_port_state
        )

    @staticmethod
    def _accepts_audit(method: Callable[..., Any]) -> bool:
        try:
            parameters = inspect.signature(method).parameters
        except (TypeError, ValueError):
            return False
        if "refresh_id" in parameters or "origin" in parameters:
            return True
        return any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )

    def _audit(
        self,
        *,
        refresh_id: str | None,
        origin: str,
        outcome: str,
        witness_verified: bool,
        triggered_at: datetime,
        waited_seconds: float = 0.0,
        error_type: str | None = None,
    ) -> None:
        """Record one refresh attempt, whether or not a navigation was sent."""
        if self._event_sink is None:
            return
        stats = getattr(self._refresh_driver, "last_refresh_stats", None) or {}
        fields: dict[str, object] = {
            "refresh_id": refresh_id,
            "origin": origin,
            "outcome": outcome,
            "witness_verified": bool(witness_verified),
            "clicks_sent": int(stats.get("clicks_completed", 0) or 0),
            "navigation_steps": list(stats.get("steps") or ()),
            "triggered_at": triggered_at.isoformat(),
            "waited_seconds": round(max(waited_seconds, 0.0), 3),
        }
        if error_type is not None:
            fields["error_type"] = error_type
        try:
            self._event_sink("ui_refresh_audit", fields)
        except Exception:
            # Audit is diagnostic: a broken sink must not change refresh policy.
            pass

    @staticmethod
    def _is_trusted(observation: Any, newer_than: datetime | None) -> bool:
        if not bool(getattr(observation, "source_freshness_verified", False)):
            return False
        if newer_than is None:
            return True
        observed_at = getattr(observation, "observed_at", None)
        return isinstance(observed_at, datetime) and observed_at > newer_than

    def await_passive_witness(
        self,
        *,
        timeout_seconds: float,
        newer_than: datetime | None = None,
    ) -> Any:
        """Wait for a natural port response instead of navigating for one.

        The 母港 click itself makes the client request a fresh port payload, so
        a caller that has just navigated home can often get a verified witness
        with no navigation at all. Waiting first is what keeps a post-action
        read from turning into another full three-click round trip: live
        2026-11-05 one run sent 27 navigations (75 clicks) to support two
        dispatches, because every read that found no witness navigated.

        Read-only by construction - it never clicks, even when it times out. A
        caller that still has no witness afterwards keeps whatever policy it
        had before (the refreshing reader may then navigate, or the caller
        fails closed).
        """
        if not math.isfinite(timeout_seconds) or timeout_seconds < 0:
            raise ValueError("timeout_seconds must be finite and non-negative")
        deadline = self._monotonic() + timeout_seconds
        with self._refresh_lock:
            observation = self._reader.read_fleets()
            while not self._is_trusted(observation, newer_than):
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    return observation
                self._sleep(min(self._poll_interval_seconds, remaining))
                observation = self._reader.read_fleets()
            return observation

    # -- return detection with observation rounds ---------------------------

    @property
    def generation(self) -> int:
        """The newest observation round; older rounds are stale by definition."""
        return self._generation

    def invalidate(self, reason: str) -> int:
        """Make every in-flight round stale before releasing listeners.

        Call this on timeout, cancellation and shutdown. A callback that fires
        afterwards sees ``is_current() is False`` and is refused before it can
        send a click.
        """
        self._generation += 1
        self._return_refresh.invalidate(reason)
        return self._generation

    def confirm_return(
        self,
        *,
        fleet_id: int,
        expected_return_at: datetime | None = None,
        witness_wait_seconds: float | None = None,
    ) -> ReturnRefreshOutcome:
        """Decide whether this fleet has returned, refreshing only if needed.

        The only lawful way to learn that a fleet is home is a visible
        navigation that makes the client ask for ``api_port/port``; the timer
        expiring proves nothing. This method therefore:

        * returns ``confirmed_return=True`` only for a *fresh* status-2
          observation, before or after exactly one refresh;
        * never retries the navigation and never treats elapsed time as
          success;
        * refuses outright when a newer round has taken over.

        ``witness_wait_seconds`` bounds the post-navigation wait for the
        natural port response. It is a wait, never a click.
        """
        wait = (
            self._max_wait_seconds
            if witness_wait_seconds is None
            else float(witness_wait_seconds)
        )
        if not math.isfinite(wait) or wait < 0:
            raise ValueError("witness_wait_seconds must be finite and non-negative")
        watch = self._return_refresh.begin_watch(
            fleet_id=fleet_id, expected_return_at=expected_return_at
        )
        self._generation = watch.generation

        def refresh() -> None:
            # Re-checked here as well, so a round invalidated between the
            # coordinator's decision and this call can never click.
            if not self._return_refresh.is_current(watch):
                raise ReturnRefreshRefused(
                    f"observation round {watch.generation} is stale; refusing to refresh"
                )
            refresh_id = self._refresh_id_factory()
            if self._driver_takes_audit:
                self._refresh_driver.refresh_port_state(
                    refresh_id=refresh_id,
                    origin=f"return_detection:g{watch.generation}",
                )
            else:
                self._refresh_driver.refresh_port_state()
            self._await_witness(deadline_seconds=wait)

        return self._return_refresh.refresh_and_confirm(
            watch=watch,
            observe=self._observe_fleet,
            refresh=refresh,
        )

    def _await_witness(self, *, deadline_seconds: float) -> Any:
        """Wait for a natural port response; never clicks, never raises."""
        deadline = self._monotonic() + deadline_seconds
        observation = self._reader.read_fleets()
        while not self._is_trusted(observation, None):
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return observation
            self._sleep(min(self._poll_interval_seconds, remaining))
            observation = self._reader.read_fleets()
        return observation

    def _observe_fleet(self, fleet_id: int) -> FleetReturnView:
        """Read one fleet's return-relevant fields; read-only, no navigation."""
        observation = self._reader.read_fleets()
        return self._fleet_view(observation, fleet_id)

    @staticmethod
    def _fleet_view(observation: Any, fleet_id: int) -> FleetReturnView:
        payload = getattr(observation, "payload", None) or ()
        matches = [
            item for item in payload if getattr(item, "fleet_id", None) == fleet_id
        ]
        if len(matches) != 1:
            return FleetReturnView(
                fleet_id=fleet_id,
                mission_status=None,
                mission_id=None,
                source_freshness_verified=False,
                observed_at=None,
            )
        item = matches[0]
        observed_at = getattr(observation, "observed_at", None)
        return FleetReturnView(
            fleet_id=fleet_id,
            mission_status=getattr(item, "mission_status", None),
            mission_id=getattr(item, "mission_id", None),
            source_freshness_verified=bool(
                getattr(observation, "source_freshness_verified", False)
            ),
            observed_at=observed_at if isinstance(observed_at, datetime) else None,
        )

    def read_fleets(self) -> Any:
        """Read the current state without touching the game.

        Genuinely read-only: it never navigates, never clicks and never waits
        for a witness. Callers that need a fresh observation must ask for one
        through :meth:`ensure_fresh` and name themselves as the reason.

        Before this change every read that found no witness fired a full
        three-click navigation, which turned routine polling into game input
        (live 2026-11-05: 27 navigations and 75 clicks behind two dispatch
        intents, 17 of those navigations never consumed by anything).
        """
        with self._refresh_lock:
            return self._reader.read_fleets()

    def ensure_fresh(self, *, origin: str) -> Any:
        """Read once, refreshing only when the caller asked for a fresh state.

        ``origin`` names the caller (``monitor_tick``, ``dispatch_preflight``,
        ``dispatch_post_action``, ``settlement_read`` ...). It never changes
        what is clicked or what counts as trustworthy; it only makes each
        refresh joinable to a reason. Fail-closed semantics are untouched: an
        observation that still has no witness is returned as-is and the caller's
        own ``require_fresh`` gate decides what to do.
        """
        if not isinstance(origin, str) or not origin.strip():
            raise ValueError("origin must name the caller that wants a fresh observation")
        origin = origin.strip()
        triggered_at = self._clock()
        with self._refresh_lock:
            started_generation = self._generation
            observation = self._reader.read_fleets()
            if self._is_trusted(observation, None):
                self._audit(
                    refresh_id=None,
                    origin=origin,
                    outcome="already_fresh",
                    witness_verified=True,
                    triggered_at=triggered_at,
                )
                return observation

            now = self._monotonic()
            if (
                self._last_refresh_at is not None
                and now - self._last_refresh_at < self._refresh_cooldown_seconds
            ):
                self._audit(
                    refresh_id=None,
                    origin=origin,
                    outcome="cooldown_suppressed",
                    witness_verified=False,
                    triggered_at=triggered_at,
                )
                return observation

            refresh_id = self._refresh_id_factory()
            # P0-4: a stale round must never click. If a newer round started
            # (or the process is shutting down) while this caller waited for
            # the lock, the navigation is suppressed and the untrusted
            # observation is returned unchanged, so the caller's own
            # require_fresh gate still fails closed.
            if started_generation != self._generation:
                self._audit(
                    refresh_id=None,
                    origin=origin,
                    outcome="stale_generation_suppressed",
                    witness_verified=False,
                    triggered_at=triggered_at,
                )
                return observation
            # Set the cooldown before touching the UI. Even an ambiguous click
            # must not be replayed automatically by the next monitor tick.
            self._last_refresh_at = now
            started_at = self._monotonic()
            try:
                if self._driver_takes_audit:
                    self._refresh_driver.refresh_port_state(
                        refresh_id=refresh_id, origin=origin
                    )
                else:
                    self._refresh_driver.refresh_port_state()

                deadline = self._monotonic() + self._max_wait_seconds
                while self._monotonic() < deadline:
                    observation = self._reader.read_fleets()
                    if self._is_trusted(observation, None):
                        self._audit(
                            refresh_id=refresh_id,
                            origin=origin,
                            outcome="refreshed",
                            witness_verified=True,
                            triggered_at=triggered_at,
                            waited_seconds=self._monotonic() - started_at,
                        )
                        return observation
                    remaining = deadline - self._monotonic()
                    if remaining <= 0:
                        break
                    self._sleep(min(self._poll_interval_seconds, remaining))
                self._audit(
                    refresh_id=refresh_id,
                    origin=origin,
                    outcome="witness_timeout",
                    witness_verified=False,
                    triggered_at=triggered_at,
                    waited_seconds=self._monotonic() - started_at,
                )
                return observation
            except Exception as exc:
                self._audit(
                    refresh_id=refresh_id,
                    origin=origin,
                    outcome="blocked",
                    witness_verified=False,
                    triggered_at=triggered_at,
                    waited_seconds=self._monotonic() - started_at,
                    error_type=type(exc).__name__,
                )
                raise
            finally:
                # Cooldown begins after the full navigation/witness wait, not
                # before it; otherwise a long timeout could expire the guard.
                self._last_refresh_at = self._monotonic()
