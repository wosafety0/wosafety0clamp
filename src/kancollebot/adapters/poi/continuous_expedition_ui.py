"""Visible-UI driver for dispatching an expedition the account actually has.

The driver owns CDP mouse input and visual checkpoint verification. It never
invokes game APIs, mutates page state, or retries an ambiguous click.

Three things had to become general before "dispatch any expedition" meant
anything, and each one is the reason for a piece of this module:

* **Which row.** ``locate_row`` resolves a display number to the row that is on
  screen right now, using the measured position index. The old constant
  ``(350, 350)`` is the third row only while the list sits at its top on tab 1.
* **Which mission.** After the row click the panel is compared against every
  known expedition's name band; the winner must be the intended one. The old
  ``MISSION_SELECTED`` checkpoint was a fingerprint of the whole panel and
  accepted mission 04 for mission 3 at 0.9871, so it proved layout, not identity.
* **Which fleet.** The fleet-selection screen is read through its tab strip, and
  the requested fleet must be the one the game paints as selected before the
  irreversible start click.

Every one of those is *evidence about what is on screen*, not a guess about what a
click should have done: a failure raises before the next click, and nothing is
ever retried.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Protocol

from kancollebot.adapters.poi.cdp_input import RecordedPoint
from kancollebot.adapters.poi.visual_target import (
    SUPPLY_TAB_FLEET_IDS,
    classify_supply_tab_selection,
    locate_fleet_supply_all_button,
    locate_supply_fleet_tab,
)
from kancollebot.observability.event_envelope import new_action_id
from kancollebot.observability.sink import correlation_scope

from kancollebot.calibration import (
    DECISION_POINT,
    DEFAULT_CATALOG,
    FLEET_TAB_POINTS,
    DetailMatch,
    MarkMatch,
    RowLocation,
)
from kancollebot.core.observation_contracts import UiStateEvidence


class ContinuousUiDriverError(RuntimeError):
    """A visible expedition UI transition cannot be proven safe."""


class VisibleGameInput(Protocol):
    def click(self, point: RecordedPoint) -> None:
        """Click one coordinate in the validated visible game viewport."""
        ...


HOME_PORT = RecordedPoint(50, 50, "home-port")
SORTIE = RecordedPoint(280, 425, "sortie")
EXPEDITION_MODE = RecordedPoint(1000, 360, "expedition-mode")
MISSION_CONFIRM = RecordedPoint(DECISION_POINT[0], DECISION_POINT[1], "mission-confirm")
EXPEDITION_START = RecordedPoint(920, 670, "expedition-start")
# The only entrance to the supply screen from the Home Port. Verified on this
# account by tools/full_supply_ui_action.py before its single supply click.
SUPPLY_MENU = RecordedPoint(103, 340, "supply-menu")

# Supply screens are verified with six samples, not the dispatch gate's three:
# the checkbox/button regions are small and the screen repaints after the click.
SUPPLY_VERIFY_SAMPLES = 6
# Live 2026-11-06 02:03Z: the return-home-port click fired about 1 s after the
# supply click and the port navigation never happened - the supply animation
# (bars filling, voice line) swallowed the click while the ledger shows it
# "sent". Five seconds covers the observed animation with margin; the port is
# still polled for afterwards rather than expected on one frame.
SUPPLY_SETTLE_SECONDS = 5.0

# The return notice and the result screen advance on a click in the empty area
# under the roster. It sits well away from the 次 control in the bottom-right
# corner, which is the only other thing on those screens a stray click could
# reach.
REWARD_ADVANCE = RecordedPoint(600, 400, "reward-advance")
# The bonus screen is the exception, verified on this account on 2026-11-05:
# the 次 control carries it to the Home Port in one click (1.5s, screenshots
# classified BONUS_ITEMS -> HOME_PORT either side). The empty-area point above
# has never been reached on this screen - every earlier run stopped at the
# fingerprint check one step before the click - so it is not assumed to work
# here. The two earlier steps keep the point that has always worked for them.
BONUS_NEXT = RecordedPoint(1144, 665, "bonus-next")
REWARD_SETTLE_SECONDS = 1.6
# The client fades between reward screens. A failed post-click check is retried
# this many times, one second apart, before the screen counts as never arrived.
REWARD_SCREEN_POLLS = 8
REWARD_SCREEN_POLL_SECONDS = 1.0
REWARD_VERIFY_SAMPLES = 6

# Fleets the game lets a player send on an expedition. Fleet 1 is drawn on the
# tab strip but cannot be dispatched, so it is not offered.
DEFAULT_SUPPORTED_FLEETS = (2, 3, 4)

# Fleets whose supply tab can be located by its bounded region but which have
# Every supply tab with a region now has a captured fingerprint template:
# ``SUPPLY_FLEET_4_SELECTED`` was recorded live 2026-11-06 (crop 341,155,30,40,
# the 46 px family spacing of tabs 1-3) and discriminates cleanly - it matches
# fleet-4-selected frames from two separate sessions at similarity 1.0 while a
# fleet-1-selected frame scores 0.91 and classifies as fleet 1. The
# relative-highlight fallback is gone: the relative brightness of these tabs
# is NOT a reliable selector on this account (the selected tab's margin over
# the runner-up measured as low as 1.7, and on a fleet-1 page tab 4 outscored
# tab 1), so the stronger template path answers for every fleet now.
TEMPLATE_FREE_SUPPLY_FLEETS: tuple[int, ...] = ()


class CdpContinuousExpeditionUiDriver:
    """Drive one expedition dispatch through measured pixels and click receipts.

    ``capture`` must return a fresh base64 PNG with the pointer parked off the
    UI. It is only called after a state checkpoint has proven which screen is
    showing, so a frame is never read while a transition is in flight.
    """

    def __init__(
        self,
        *,
        game_input: VisibleGameInput,
        verify_state: Callable[[str], UiStateEvidence],
        event_sink: Callable[[str, Mapping[str, object]], None],
        details: "DetailSourceLike",
        marks: "DispatchMarksLike",
        capture: Callable[[], str],
        classify_frame: Callable[[str], tuple[str, Mapping[str, float]]] | None = None,
        capture_composited: Callable[[], str] | None = None,
        neutralize_pointer: Callable[[], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        settle_seconds: float = 1.0,
        locate_row: Callable[[str], RowLocation] | None = None,
        supported_fleets: tuple[int, ...] = DEFAULT_SUPPORTED_FLEETS,
    ) -> None:
        if not callable(getattr(game_input, "click", None)):
            raise ValueError("game_input must provide click(point)")
        for name, value in (
            ("verify_state", verify_state),
            ("event_sink", event_sink),
            ("capture", capture),
            ("sleep", sleep),
        ):
            if not callable(value):
                raise ValueError(f"{name} must be callable")
        for method in ("reference", "require"):
            if not callable(getattr(details, method, None)):
                raise ValueError(f"details must provide {method}()")
        for method in ("require_decision", "require_fleet", "fleets"):
            if not callable(getattr(marks, method, None)):
                raise ValueError(f"marks must provide {method}()")
        if settle_seconds < 0:
            raise ValueError("settle_seconds must be non-negative")
        if locate_row is not None and not callable(locate_row):
            raise ValueError("locate_row must be callable or None")
        for name, value in (
            ("classify_frame", classify_frame),
            ("capture_composited", capture_composited),
        ):
            if value is not None and not callable(value):
                raise ValueError(f"{name} must be callable or None")
        if not supported_fleets:
            raise ValueError("supported_fleets must not be empty")
        self._input = game_input
        self._verify_state = verify_state
        self._event_sink = event_sink
        self._details = details
        self._marks = marks
        self._capture = capture
        self._classify_frame = classify_frame
        self._capture_composited = capture_composited
        self._neutralize_pointer = neutralize_pointer or (lambda: None)
        self._sleep = sleep
        self._settle_seconds = settle_seconds
        self._locate_row = locate_row
        self._supported_fleets = tuple(supported_fleets)

    # -- evidence ---------------------------------------------------------
    def _frame(self) -> str:
        self._neutralize_pointer()
        return self._capture()

    def _confirm(
        self, expected: str, *, phase: str, samples: int | None = None
    ) -> UiStateEvidence:
        self._neutralize_pointer()
        evidence = (
            self._verify_state(expected)
            if samples is None
            else self._verify_state(expected, visual_attempts=samples)
        )
        if not isinstance(evidence, UiStateEvidence):
            raise ContinuousUiDriverError(
                f"{phase}: verifier returned no structured UI evidence"
            )
        if (
            evidence.state != expected
            or evidence.consecutive_states != (expected, expected)
        ):
            raise ContinuousUiDriverError(
                f"{phase}: expected two consecutive {expected} frames; "
                f"observed {evidence.consecutive_states!r}"
            )
        self._event_sink("continuous_ui_state_confirmed", {
            "phase": phase,
            "state": expected,
            "evidence": evidence.evidence,
            "consecutive_states": evidence.consecutive_states,
        })
        return evidence

    def _await_state(
        self, expected: str, *, phase: str, samples: int | None = REWARD_VERIFY_SAMPLES
    ) -> UiStateEvidence:
        """Wait for a reward screen the client reaches through a fade.

        A post-click check is not a precondition: the client fades through the
        port background on its way to the next screen, and that intermediate
        frame is a *known* state (HOME_PORT) rather than an unknown one. Treating
        it as a failure ends a chain that is in fact progressing - live
        2026-11-05, the notice click showed HOME_PORT for ~2 s before the result
        screen appeared, which aborted a settlement one click from completion.

        Each attempt is the same strict check the caller would have run: the
        successful attempt *is* the confirmation, so a screen that arrives costs
        no extra verification. Only a failed check is repeated, and only
        ``REWARD_SCREEN_POLLS`` times - the click has already been sent, so the
        polling is read-only; no further input ever comes from here.
        """
        for attempt in range(1, REWARD_SCREEN_POLLS + 1):
            try:
                return self._confirm(expected, phase=phase, samples=samples)
            except Exception as exc:
                if (
                    type(exc).__name__ not in self._NOT_THIS_SCREEN
                    or attempt >= REWARD_SCREEN_POLLS
                ):
                    raise
                self._event_sink("continuous_ui_reward_screen_pending", {
                    "phase": phase,
                    "expected": expected,
                    "attempt": attempt,
                    "of": REWARD_SCREEN_POLLS,
                    "detail": str(exc)[:200],
                })
                self._sleep(REWARD_SCREEN_POLL_SECONDS)
        raise ContinuousUiDriverError(f"{phase}: {expected} never appeared")  # pragma: no cover

    # The verifier is injected, so "this is not the screen you expected" arrives
    # as whatever the caller's module raises: this driver's own
    # ContinuousUiDriverError, the gate's UiStateGateError, or the production
    # entry's LoopError. A probe must treat all three as a plain no, and must
    # still refuse to guess when the *evidence source* itself failed - clicking
    # blind is the one thing a reward chain must never do.
    _NOT_THIS_SCREEN = frozenset(
        {"ContinuousUiDriverError", "UiStateGateError", "LoopError"}
    )

    def _probe_state(
        self, expected: str, *, phase: str, samples: int | None = None
    ) -> UiStateEvidence | None:
        try:
            return self._confirm(expected, phase=phase, samples=samples)
        except Exception as exc:
            if type(exc).__name__ in self._NOT_THIS_SCREEN:
                return None
            raise

    def _click(self, point: RecordedPoint, *, action: str, step: int) -> None:
        """Send exactly one input, under exactly one action identity.

        The id is minted *here* because this method is the only place in the
        driver that actually dispatches an input; every row emitted while the
        click is in flight - ``attempted``, ``ambiguous``, ``sent`` - inherits
        the same ``action_id`` from the correlation scope. ``step`` is a legacy
        retry index and is deliberately not used as identity: one dispatch
        contains several clicks and they must be distinguishable.

        The order is the safety property, not a detail: ``attempted`` is
        emitted *before* the input call, so a sink that raises provably prevents
        the click instead of merely failing to record one.
        """
        with correlation_scope(self._event_sink, action_id=new_action_id()):
            self._event_sink("continuous_ui_click_attempted", {
                "step": step,
                "action": action,
                "target": point.label,
            })
            try:
                self._input.click(point)
            except Exception as exc:
                self._event_sink("continuous_ui_click_ambiguous", {
                    "step": step,
                    "action": action,
                    "target": point.label,
                    "error_type": type(exc).__name__,
                })
                raise
            self._event_sink("continuous_ui_click_sent", {
                "step": step,
                "action": action,
                "target": point.label,
            })
            if self._settle_seconds:
                self._sleep(self._settle_seconds)

    def _mission_row_point(self, expedition_id: str) -> RecordedPoint:
        """Where that expedition's row is *now*, proven by the list session."""
        if self._locate_row is None:
            raise ContinuousUiDriverError(
                f"cannot place {expedition_id}'s row without a list session; "
                "refusing to click a coordinate that was never measured"
            )
        try:
            # One argument only: the locator resolves the area tab from the
            # position index, because the game reopens the list on whichever tab
            # was last used and the index is per-tab.
            location = self._locate_row(expedition_id)
        except Exception as exc:
            raise ContinuousUiDriverError(
                f"cannot place {expedition_id}'s row: {type(exc).__name__}: {exc}"
            ) from exc
        if not isinstance(location, RowLocation):
            raise ContinuousUiDriverError("the list session returned no structured row location")
        if location.expedition_id != expedition_id:
            raise ContinuousUiDriverError(
                f"the list session placed {location.expedition_id}, not {expedition_id}"
            )
        self._event_sink("continuous_ui_row_located", {
            "expedition_id": location.expedition_id,
            "tab": location.tab,
            "position": location.position,
            "offset": location.offset,
            "row_index": location.row_index,
            "scroll_clicks": location.scroll_clicks,
            "tab_clicks": location.tab_clicks,
            "detail": location.detail,
        })
        return RecordedPoint(
            location.point[0], location.point[1],
            f"expedition-row-{location.expedition_id}-pos{location.position}",
        )

    def _require_detail(self, expedition_id: str, *, phase: str) -> DetailMatch:
        frame = self._frame()
        try:
            match = self._details.require(expedition_id, frame)
        except Exception as exc:
            raise ContinuousUiDriverError(
                f"{phase}: the selected panel is not {expedition_id}: {exc}"
            ) from exc
        self._event_sink("continuous_ui_detail_confirmed", {
            "phase": phase,
            "expected": expedition_id,
            "observed": match.expedition_id,
            **match.to_dict(),
        })
        return match

    def _require_decision(self, *, phase: str) -> MarkMatch:
        frame = self._frame()
        try:
            match = self._marks.require_decision(frame)
        except Exception as exc:
            raise ContinuousUiDriverError(f"{phase}: {exc}") from exc
        self._event_sink("continuous_ui_decision_confirmed", {
            "phase": phase,
            **match.to_dict(),
        })
        return match

    def _selected_fleet(self) -> int | None:
        """Which fleet tab the fleet-selection screen paints as selected."""
        frame = self._frame()
        best: tuple[float, int] | None = None
        for fleet_id in getattr(self._marks, "fleets", lambda: ())():
            try:
                match = self._marks.require_fleet(fleet_id, frame)
            except Exception:
                continue
            if best is None or match.score > best[0]:
                best = (match.score, fleet_id)
        return None if best is None else best[1]

    def _require_fleet(self, fleet_id: int, *, phase: str) -> MarkMatch:
        frame = self._frame()
        try:
            match = self._marks.require_fleet(fleet_id, frame)
        except Exception as exc:
            raise ContinuousUiDriverError(f"{phase}: {exc}") from exc
        self._event_sink("continuous_ui_fleet_confirmed", {
            "phase": phase,
            "fleet_id": fleet_id,
            **match.to_dict(),
        })
        return match

    # -- the transaction --------------------------------------------------
    def prepare_dispatch(self, *, fleet_id: int, mission_id: int) -> UiStateEvidence:
        """Navigate to the verified start-precondition state without starting."""
        expedition_id = DEFAULT_CATALOG.number_of_mission_id(mission_id)
        if expedition_id is None:
            raise ContinuousUiDriverError(
                f"mission id {mission_id} is not in the master-data table, so its row "
                "cannot be named; refusing to guess"
            )
        if fleet_id not in self._supported_fleets:
            raise ContinuousUiDriverError(
                f"fleet {fleet_id} cannot be dispatched; supported fleets are "
                f"{list(self._supported_fleets)}"
            )
        if self._details.reference(expedition_id) is None:
            raise ContinuousUiDriverError(
                f"no detail reference for {expedition_id}; walk that list before dispatching it"
            )
        available = tuple(getattr(self._marks, "fleets", lambda: ())())
        if fleet_id not in available:
            raise ContinuousUiDriverError(
                f"no fleet tab reference for fleet {fleet_id}; capture the dispatch marks first"
            )

        self._event_sink("continuous_ui_dispatch_preparation_started", {
            "fleet_id": fleet_id,
            "mission_id": mission_id,
            "expedition_id": expedition_id,
            "game_api_requests": False,
            "expedition_start_clicked": False,
        })
        try:
            self._confirm("HOME_PORT", phase="dispatch precondition")
            self._click(SORTIE, action="open_sortie", step=1)
            self._confirm("SORTIE_MENU", phase="after open_sortie")

            self._click(EXPEDITION_MODE, action="open_expedition_list", step=2)
            self._confirm("EXPEDITION_LIST", phase="after open_expedition_list")

            row_point = self._mission_row_point(expedition_id)
            # Locating the row scrolls the list and may switch the area tab, so
            # the checkpoint is re-established on the frame the click lands on.
            self._confirm("EXPEDITION_LIST", phase="dispatch row precondition")
            self._click(row_point, action=f"select_mission_{expedition_id}", step=3)

            self._require_detail(expedition_id, phase="after selecting the row")
            self._require_decision(phase="before confirming the mission")

            self._click(MISSION_CONFIRM, action="confirm_mission", step=4)

            selected = self._selected_fleet()
            if selected is None:
                raise ContinuousUiDriverError(
                    "after confirming the mission the fleet-selection screen did not "
                    "show any recognised fleet tab; refusing to continue"
                )
            if selected != fleet_id:
                self._event_sink("continuous_ui_fleet_switch_required", {
                    "selected": selected,
                    "requested": fleet_id,
                })
                tab = FLEET_TAB_POINTS[fleet_id]
                point = RecordedPoint(tab[0], tab[1], f"fleet-{fleet_id}-tab")
                self._click(point, action=f"select_fleet_{fleet_id}", step=5)
            else:
                self._event_sink("continuous_ui_fleet_already_selected", {
                    "fleet_id": fleet_id,
                })

            match = self._require_fleet(fleet_id, phase="expedition start precondition")
            state = f"FLEET_{fleet_id}_SELECTED"
            evidence = UiStateEvidence(
                state=state,
                consecutive_states=(state, state),
                evidence=f"fleet_tab_fingerprint:{match.score:.4f}",
            )
            self._event_sink("continuous_ui_dispatch_preparation_completed", {
                "fleet_id": fleet_id,
                "mission_id": mission_id,
                "expedition_id": expedition_id,
                "final_state": state,
                "expedition_start_clicked": False,
            })
            return evidence
        except Exception as exc:
            try:
                self._event_sink("continuous_ui_dispatch_preparation_blocked", {
                    "fleet_id": fleet_id,
                    "mission_id": mission_id,
                    "expedition_id": expedition_id,
                    "error_type": type(exc).__name__,
                    "reason": "checkpoint failed or UI click outcome is ambiguous; no automatic retry",
                })
            except Exception:
                pass
            raise

    def dispatch_expedition(self, *, fleet_id: int, mission_id: int) -> None:
        """Start the expedition only after the complete visual preparation gate."""
        try:
            self.prepare_dispatch(fleet_id=fleet_id, mission_id=mission_id)
            # Re-read the fleet tab on a fresh frame immediately before the one
            # click that cannot be undone; the preparation's own frames are older.
            self._require_fleet(fleet_id, phase="immediate expedition start precondition")
            self._click(EXPEDITION_START, action="start_expedition", step=6)
            self._event_sink("continuous_ui_dispatch_input_completed", {
                "fleet_id": fleet_id,
                "mission_id": mission_id,
                "postcondition": "must_be_confirmed_by_newer_poi_observation",
            })
        except Exception as exc:
            try:
                self._event_sink("continuous_ui_dispatch_blocked", {
                    "fleet_id": fleet_id,
                    "mission_id": mission_id,
                    "error_type": type(exc).__name__,
                    "reason": "checkpoint failed or UI click outcome is ambiguous; no automatic retry",
                })
            except Exception:
                pass
            raise

    def _confirm_fleet_page(
        self, fleet_id: int, *, phase: str
    ) -> tuple[str, UiStateEvidence]:
        """Prove the fleet-selection page with this fleet's tab selected.

        The ``FLEET_*_SELECTED`` fingerprints crop a 50x60 window of the fleet
        tab row that does not cover the tab label, so they cannot separate the
        fleets: live 2026-11-05 the same frame scored Fleet 3 at 0.8441 and
        Fleet 2 at 0.8149, a difference of 0.03 that the classifier's 0.04
        margin rejects, and the dispatch was registered as BLOCKED because the
        post-start page could not be named at all.

        The calibrated marks carry the tab label and are page specific on this
        account: measured from real frames, the same crop scores 0.86 on the
        port, 0.79 on the mission-selection page and 0.57 on the expedition
        list - all below the 0.95 threshold - and only the fleet-selection page
        reaches it. Two consecutive matches are required, like every other
        state proof here.
        """
        state = f"FLEET_{fleet_id}_SELECTED"
        last_error: Exception | None = None
        for attempt in range(2):
            frame = self._frame()
            try:
                match = self._marks.require_fleet(fleet_id, frame)
            except Exception as exc:
                last_error = exc
                continue
            self._event_sink("continuous_ui_fleet_page_confirmed", {
                "phase": phase,
                "fleet_id": fleet_id,
                "attempt": attempt + 1,
                **match.to_dict(),
            })
            return state, UiStateEvidence(
                state=state,
                consecutive_states=(state, state),
                evidence="two_consecutive_calibrated_fleet_tab_matches",
            )
        raise ContinuousUiDriverError(
            f"{phase}: the fleet-selection page with Fleet {fleet_id} selected "
            f"cannot be proven: {last_error}"
        )

    def _reward_screen_is_displayed(self) -> bool:
        """True when one of the reward screens is on display.

        Those three screens advance on a click anywhere, so a click-until-observed
        loop must never run while one of them is up: the chain belongs to the
        settlement controller, which proves every screen it advances.
        """
        for state in ("HOME_PORT_RETURN_NOTICE", "RETURN_RESULT", "BONUS_ITEMS"):
            if self._probe_state(state, phase=f"guard before a return-to-port click ({state})") is not None:
                self._event_sink("continuous_ui_reward_screen_blocks_return", {
                    "state": state,
                    "clicks_sent": 0,
                })
                return True
        return False

    def return_to_home_port_by_observation(
        self,
        *,
        fleet_id: int,
        max_attempts: int = 6,
        interval_seconds: float = 3.0,
    ) -> UiStateEvidence:
        """Return to the port by clicking and observing, not by naming the page.

        Some pages cannot be named - the fleet-selection page has no usable
        fingerprint - and a proof that never succeeds is a proof that never
        sends the click that would fix it. This is the last resort: the named
        states and the calibrated fleet tab are tried first, because a click on
        a page nobody can identify has an unknown effect.

        The loop waits before its first click. The client often settles on its
        own - after a start click it shows the fleet-selection page and then
        drops back to the expedition list - and waiting costs nothing while a
        click changes a screen we do not understand.

        Two rules keep it safe. A reward screen stops the loop outright,
        because those advance on a click anywhere and belong to the settlement
        controller. And every attempt is bounded: an unrecognised screen never
        turns into an unbounded click sequence.
        """
        observed_port = False
        for attempt in range(1, max_attempts + 1):
            try:
                return self._confirm(
                    "HOME_PORT", phase="Home Port reached by observation", samples=None
                )
            except Exception:
                pass
            if self._reward_screen_is_displayed():
                raise ContinuousUiDriverError(
                    "a reward screen is on display; the settlement controller owns "
                    "that chain, so no return-to-port click is sent"
                )
            if attempt == 1:
                # Let the client settle before the first click.
                self._event_sink("continuous_ui_return_by_observation_wait", {
                    "fleet_id": fleet_id,
                    "wait_seconds": interval_seconds,
                    "clicks_sent": 0,
                })
                self._sleep(interval_seconds)
                continue
            self._event_sink("continuous_ui_return_by_observation_attempt", {
                "fleet_id": fleet_id,
                "attempt": attempt,
                "of": max_attempts,
                "interval_seconds": interval_seconds,
                "target": HOME_PORT.label,
            })
            self._click(HOME_PORT, action="return_home_port_by_observation", step=attempt)
            self._sleep(interval_seconds)
        raise ContinuousUiDriverError(
            f"the Home Port was not reached after {max_attempts} observed "
            "return attempts; refusing to keep clicking"
        )

    def return_to_home_port(
        self,
        *,
        fleet_id: int,
        expected_current_state: str | None = None,
    ) -> UiStateEvidence:
        """Return from one *proven* page with exactly one navigation click.

        The page is proven, not assumed. After the start click the client does
        not settle on one screen: it shows the fleet-selection page the start
        was confirmed on, then drops back to the expedition list with that
        mission still selected. A caller naming a single expected state gets an
        ordering race - live 2026-11-05 the dispatch was registered as BLOCKED
        because "EXPEDITION_LIST" was demanded while the client was still
        showing FLEET_3_SELECTED, three seconds after an irreversible click that
        had worked. The default therefore proves whichever of the calibrated
        pages is really on screen; naming one state stays available and stays
        strict.
        """
        if fleet_id not in self._supported_fleets:
            raise ContinuousUiDriverError(
                f"Home Port recovery is not calibrated for Fleet {fleet_id}"
            )
        allowed = ["EXPEDITION_LIST", "MISSION_SELECTED"]
        allowed.extend(f"FLEET_{candidate}_SELECTED" for candidate in self._supported_fleets)
        if expected_current_state is None:
            try:
                current, _evidence = self._confirm_any(
                    allowed, phase="precondition for return_home_port"
                )
            except ContinuousUiDriverError as visual_error:
                # The fleet-selection page has no usable fingerprint, so the
                # calibrated fleet tab is the fallback proof of that page. It
                # is not a weaker gate: the tab crop only reaches the threshold
                # on that page, and both frames must match.
                try:
                    current, _evidence = self._confirm_fleet_page(
                        fleet_id, phase="precondition for return_home_port"
                    )
                except ContinuousUiDriverError:
                    # Still unnamed. Rather than refuse forever, click the
                    # 母港 control and look again - bounded, and never while a
                    # reward screen is up.
                    return self.return_to_home_port_by_observation(fleet_id=fleet_id)
            self._event_sink("continuous_ui_home_port_precondition", {
                "fleet_id": fleet_id,
                "state": current,
                "candidates": allowed,
            })
        else:
            if expected_current_state not in allowed:
                raise ContinuousUiDriverError(
                    f"Unsupported Home Port recovery precondition: {expected_current_state}"
                )
            self._confirm(expected_current_state, phase="precondition for return_home_port")
        self._click(HOME_PORT, action="return_home_port", step=7)
        try:
            return self._await_state(
                "HOME_PORT", phase="after return_home_port", samples=None
            )
        except Exception as first_error:
            self._event_sink("continuous_ui_home_port_verification_pending", {
                "fleet_id": fleet_id,
                "error_type": type(first_error).__name__,
                "retry": "read-only visual verification only; no second click",
            })
            self._sleep(self._settle_seconds)
            return self._await_state(
                "HOME_PORT",
                phase="after return_home_port observation retry",
                samples=None,
            )

    def _confirm_any(
        self, candidates: list[str], *, phase: str
    ) -> tuple[str, UiStateEvidence]:
        """Prove which of these pages is on screen, without clicking anything.

        Every candidate is a read-only classification of the same frame, so this
        cannot move the client; it only refuses to guess. Raises when none of
        them is on screen, which is the case where a click must not be sent.
        """
        for candidate in candidates:
            evidence = self._probe_state(
                candidate, phase=f"{phase} ({candidate})"
            )
            if evidence is not None:
                return candidate, evidence
        raise ContinuousUiDriverError(
            f"{phase}: the screen is none of {candidates}; refusing to click"
        )

    # -- resupply ---------------------------------------------------------
    def _supply_page_state(self, requested_fleet_id: int) -> str:
        """Which supply page variant is on screen, proven one state at a time.

        Fingerprints are tried first and answer for Fleets 1-3. Only when the
        *requested* fleet has no template is the relative-highlight check
        consulted, and it may only name that same fleet - so a Fleet 2/3
        resupply behaves exactly as before.
        """
        for candidate in (
            "SUPPLY_FLEET_1_SELECTED",
            "SUPPLY_FLEET_2_SELECTED",
            "SUPPLY_FLEET_3_SELECTED",
            "SUPPLY_FLEET_4_SELECTED",
            "SUPPLY_MENU",
        ):
            try:
                self._confirm(
                    candidate,
                    phase="identify the supply page",
                    samples=SUPPLY_VERIFY_SAMPLES,
                )
                return candidate
            except Exception:
                continue
        if requested_fleet_id in TEMPLATE_FREE_SUPPLY_FLEETS:
            template_free = self._template_free_supply_state(requested_fleet_id)
            if template_free is not None:
                return template_free
        raise ContinuousUiDriverError(
            "the current screen is not a verified supply page; refusing to click"
        )

    def _template_free_supply_state(self, fleet_id: int) -> str | None:
        """Name the selected tab from relative brightness, for one fleet only.

        Returns the expected state string only when the requested fleet is the
        one the frame paints as selected; any other answer is ``None`` so the
        caller keeps its fail-closed path.
        """
        if fleet_id not in SUPPLY_TAB_FLEET_IDS:
            return None
        try:
            state, scores = classify_supply_tab_selection(
                self._frame(), fleet_ids=SUPPLY_TAB_FLEET_IDS
            )
        except Exception as exc:
            self._event_sink("continuous_ui_supply_tab_highlight_failed", {
                "fleet_id": fleet_id,
                "error_type": type(exc).__name__,
                "verification": "template_free_relative_brightness",
            })
            return None
        self._event_sink("continuous_ui_supply_tab_highlight_scores", {
            "fleet_id": fleet_id,
            "detected_state": state,
            "scores": {str(key): round(value, 2) for key, value in scores.items()},
            "verification": "template_free_relative_brightness",
            "live_calibrated": False,
        })
        expected = f"SUPPLY_FLEET_{fleet_id}_SELECTED"
        return expected if state == expected else None

    def _confirm_supply_fleet_selected(self, fleet_id: int, *, phase: str) -> None:
        """Prove the requested fleet's supply tab is the selected one.

        A template match is the strong proof and is used whenever the fleet has
        one. A fleet without a template falls back to the relative-brightness
        check, which is recorded as such so no reader mistakes it for a
        calibrated fingerprint.
        """
        expected = f"SUPPLY_FLEET_{fleet_id}_SELECTED"
        if fleet_id not in TEMPLATE_FREE_SUPPLY_FLEETS:
            self._confirm(expected, phase=phase, samples=SUPPLY_VERIFY_SAMPLES)
            return
        try:
            self._confirm(expected, phase=phase, samples=SUPPLY_VERIFY_SAMPLES)
            return
        except Exception as fingerprint_error:
            if self._template_free_supply_state(fleet_id) is not None:
                return
            raise ContinuousUiDriverError(
                f"{phase}: fleet {fleet_id} is not provably the selected supply tab "
                "(no fingerprint template and the relative-highlight check did not "
                "confirm it); refusing to click"
            ) from fingerprint_error

    def resupply_fleet(self, *, fleet_id: int) -> UiStateEvidence:
        """Supply one idle fleet and return two-frame Home Port evidence.

        The chain is the one already calibrated for this account: Home Port ->
        supply menu -> that fleet's tab -> its single fleet-wide supply button
        -> back to Home Port. Completion is *not* claimed here; the caller
        re-reads Poi Redux and requires every ship to be at capacity. This
        driver only proves which screen each click landed on.
        """
        if isinstance(fleet_id, bool) or not isinstance(fleet_id, int) or fleet_id < 1:
            raise ContinuousUiDriverError("fleet_id must be a positive integer")
        if fleet_id not in self._supported_fleets:
            raise ContinuousUiDriverError(
                f"fleet {fleet_id} cannot be resupplied; supported fleets are "
                f"{list(self._supported_fleets)}"
            )
        if fleet_id not in (2, 3, 4):
            raise ContinuousUiDriverError(
                f"no calibrated supply tab region for fleet {fleet_id}; only fleets "
                "2, 3 and 4 have a locatable supply tab on this account"
            )
        expected_fleet = f"SUPPLY_FLEET_{fleet_id}_SELECTED"
        self._event_sink("continuous_ui_resupply_started", {
            "fleet_id": fleet_id,
            "game_api_requests": False,
            "fleet_tab_verification": (
                "fingerprint_template"
                if fleet_id not in TEMPLATE_FREE_SUPPLY_FLEETS
                else "template_free_relative_brightness"
            ),
        })
        self._confirm("HOME_PORT", phase="resupply precondition")
        self._click(SUPPLY_MENU, action="open_supply_menu", step=1)
        self._sleep(SUPPLY_SETTLE_SECONDS)
        state = self._supply_page_state(fleet_id)
        if state != expected_fleet:
            frame = self._frame()
            try:
                tab = locate_supply_fleet_tab(frame, fleet_index=fleet_id)
            except Exception as exc:
                raise ContinuousUiDriverError(
                    f"supply tab for fleet {fleet_id} could not be located: {exc}"
                ) from exc
            self._event_sink("continuous_ui_supply_tab_located", {
                "fleet_id": fleet_id,
                "bounds": list(tab.bounds),
                "region": list(tab.region),
                "locator": tab.locator,
            })
            # Routed through ``_click`` so this input has the same fail-closed
            # ordering as every other one: ``attempted`` is written *before*
            # the click, and ``ambiguous`` replaces ``sent`` when it throws.
            # The hand-rolled version used to click first and log afterwards,
            # which is exactly the ordering a broken log could not stop.
            self._click(
                RecordedPoint(tab.x, tab.y, tab.label),
                action="select_supply_fleet_tab",
                step=2,
            )
            self._event_sink("continuous_ui_supply_tab_click_sent", {
                "fleet_id": fleet_id,
                "target": tab.label,
                "retry_allowed": False,
            })
            self._confirm_supply_fleet_selected(
                fleet_id, phase="after selecting the supply fleet tab"
            )
        else:
            self._event_sink("continuous_ui_supply_tab_already_selected", {
                "fleet_id": fleet_id,
            })

        # The supply button is located three times and must not move: the
        # region is small, and a drifting bound means a repaint is in flight.
        targets = []
        frame = ""
        for _ in range(3):
            self._confirm_supply_fleet_selected(fleet_id, phase="supply button precondition")
            frame = self._frame()
            try:
                targets.append(locate_fleet_supply_all_button(frame))
            except Exception as exc:
                raise ContinuousUiDriverError(
                    f"fleet {fleet_id} supply button could not be located: {exc}"
                ) from exc
            self._sleep(0.25)
        if len({target.bounds for target in targets}) != 1:
            raise ContinuousUiDriverError(
                "the fleet supply button moved across three frames; refusing to click"
            )
        target = targets[-1]
        self._event_sink("continuous_ui_supply_button_verified", {
            "fleet_id": fleet_id,
            "bounds": list(target.bounds),
            "region": list(target.region),
            "locator": target.locator,
        })
        self._click(
            RecordedPoint(target.x, target.y, target.label),
            action="click_fleet_supply_all",
            step=3,
        )
        self._event_sink("continuous_ui_supply_click_sent", {
            "fleet_id": fleet_id,
            "retry_allowed": False,
        })
        self._sleep(SUPPLY_SETTLE_SECONDS)

        # A supply keeps the client on the supply screen; return Home Port so
        # the caller gets the same two-frame Home Port evidence every other
        # controller action produces. The 母港 control starts a scene fade, so
        # the port is polled for rather than expected on the next frame: live
        # 2026-11-05 the screen was still the supply page 2.6 s after the click
        # and the single post-click check ended a resupply that had worked.
        self._click(HOME_PORT, action="return_home_port", step=4)
        evidence = self._await_state("HOME_PORT", phase="after resupply")
        self._event_sink("continuous_ui_resupply_completed", {
            "fleet_id": fleet_id,
            "completion_verified_by": "caller_poi_observation",
        })
        return evidence

    # -- settlement -------------------------------------------------------
    def _advance_reward_screen(
        self,
        *,
        fleet_id: int,
        before: str,
        after: str,
        phase: str,
        step: int,
    ) -> UiStateEvidence:
        """Prove one reward screen, click once, then prove the next one.

        Each of the three screens accepts a click anywhere, so the same fixed
        point advances all of them; what is *not* assumed is the resulting
        screen. The post-click state must be observed on two consecutive frames
        or the chain stops here - the caller's scheduler is what decides whether
        the fleet is idle afterwards.
        """
        if isinstance(fleet_id, bool) or not isinstance(fleet_id, int) or fleet_id < 1:
            raise ContinuousUiDriverError("fleet_id must be a positive integer")
        if fleet_id not in self._supported_fleets:
            raise ContinuousUiDriverError(
                f"fleet {fleet_id} is not a supported dispatch/settlement fleet; "
                f"supported fleets are {list(self._supported_fleets)}"
            )
        self._event_sink("continuous_ui_settlement_step_started", {
            "fleet_id": fleet_id,
            "phase": phase,
            "expected_before": before,
            "expected_after": after,
            "game_api_requests": False,
        })
        # Resume before clicking. A settlement can be interrupted between two
        # screens (process restart, a blocked gate, or the game itself having
        # accepted the result while the chain was closed), and every screen
        # advances on a click anywhere - so a click sent to a screen that is
        # already the target would skip a screen instead of redoing one. Prove
        # the target first; only click when the chain really is one step behind.
        already = self._probe_state(after, phase=f"{phase} already advanced")
        if already is not None:
            self._event_sink("continuous_ui_settlement_step_already_advanced", {
                "fleet_id": fleet_id,
                "phase": phase,
                "state": after,
                "evidence": already.evidence,
                "clicks_sent": 0,
            })
            return already
        self._confirm(before, phase=f"{phase} precondition", samples=REWARD_VERIFY_SAMPLES)
        advance = BONUS_NEXT if before == "BONUS_ITEMS" else REWARD_ADVANCE
        self._click(advance, action=f"advance_{phase}", step=step)
        self._sleep(REWARD_SETTLE_SECONDS)
        evidence = self._await_state(after, phase=f"after {phase}")
        self._event_sink("continuous_ui_settlement_step_completed", {
            "fleet_id": fleet_id,
            "phase": phase,
            "final_state": after,
            "evidence": evidence.evidence,
        })
        return evidence

    SETTLEMENT_SCREENS = ("HOME_PORT_RETURN_NOTICE", "RETURN_RESULT", "BONUS_ITEMS")

    def settlement_screen(self, *, fleet_id: int) -> str | None:
        """Return the reward screen on display, or None when none is.

        Read-only: three bounded probes, no clicks. It answers the one question
        a restart cannot answer from Poi alone - "is there anything left to
        click?" - so the caller can finish a settlement that was interrupted
        after its reward request had already been accepted.
        """
        if isinstance(fleet_id, bool) or not isinstance(fleet_id, int) or fleet_id < 1:
            raise ContinuousUiDriverError("fleet_id must be a positive integer")
        if fleet_id not in self._supported_fleets:
            raise ContinuousUiDriverError(
                f"fleet {fleet_id} is not a supported dispatch/settlement fleet; "
                f"supported fleets are {list(self._supported_fleets)}"
            )
        state = self.probe_drain_state()
        if state is not None:
            if state in self.SETTLEMENT_SCREENS:
                self._event_sink("continuous_ui_settlement_screen_detected", {
                    "fleet_id": fleet_id,
                    "state": state,
                    "clicks_sent": 0,
                })
                return state
            self._event_sink("continuous_ui_settlement_screen_absent", {
                "fleet_id": fleet_id,
                "probed": list(self.SETTLEMENT_SCREENS),
                "clicks_sent": 0,
            })
            return None
        for screen in self.SETTLEMENT_SCREENS:
            if self._probe_state(screen, phase=f"probe {screen}") is not None:
                self._event_sink("continuous_ui_settlement_screen_detected", {
                    "fleet_id": fleet_id,
                    "state": screen,
                    "clicks_sent": 0,
                })
                return screen
        self._event_sink("continuous_ui_settlement_screen_absent", {
            "fleet_id": fleet_id,
            "probed": list(self.SETTLEMENT_SCREENS),
            "clicks_sent": 0,
        })
        return None

    def collect_return_result(self, *, fleet_id: int) -> UiStateEvidence:
        """Advance the return notice to the expedition result screen."""
        return self._advance_reward_screen(
            fleet_id=fleet_id,
            before="HOME_PORT_RETURN_NOTICE",
            after="RETURN_RESULT",
            phase="return_result",
            step=1,
        )

    def claim_bonus_items(self, *, fleet_id: int) -> UiStateEvidence:
        """Advance the result screen to the bonus/reward screen."""
        return self._advance_reward_screen(
            fleet_id=fleet_id,
            before="RETURN_RESULT",
            after="BONUS_ITEMS",
            phase="bonus_items",
            step=2,
        )

    def claim_reward(self, *, fleet_id: int) -> UiStateEvidence:
        """Dismiss the bonus screen and prove the idle Home Port behind it."""
        return self._advance_reward_screen(
            fleet_id=fleet_id,
            before="BONUS_ITEMS",
            after="HOME_PORT",
            phase="reward",
            step=3,
        )

    # -- fleet-agnostic settlement pages (P0.2 global drain) ----------------
    #
    # The three methods above take a fleet id; the two below deliberately do
    # not. The reward pages name the expedition, not the fleet, so a drain that
    # walks them cannot know - and must not guess - whose result it is advancing.
    # Attribution happens afterwards, from the dispatch ledger.

    # -- one-frame, all-state probe (P1 noise reduction) --------------------
    #
    # The historical probe walked SETTLEMENT_SCREENS one state at a time, and
    # each state asked the two-frame verifier for up to six samples: nine or
    # more screenshots in a round where a single screenshot already yields the
    # score of EVERY state. The injected classifier (production binds
    # rank_screenshot_fingerprints, same 0.97 score / 0.04 margin thresholds)
    # answers all candidates from that one frame.
    #
    # Layer note, proven live 2026-11-05/06: HOME_PORT_RETURN_NOTICE lives on a
    # composited overlay that the default fromSurface=True capture drops, while
    # RETURN_RESULT / BONUS_ITEMS / HOME_PORT descriptors were captured on the
    # default surface and classify UNKNOWN on the composited one. A strict
    # single screenshot cannot prove both families without breaking calibration,
    # so the default frame decides everything except the notice, and ONE extra
    # composited frame is taken only when the default reads HOME_PORT or UNKNOWN
    # (exactly the cases where an unnoticed return bubble can hide). That is
    # 1-2 captures instead of >= 9, with no threshold relaxed.
    def probe_drain_state(self) -> str | None:
        """Return the state proven by an all-state probe, or ``None``.

        ``None`` means the single-frame capability is not installed and the
        caller must use the legacy per-state probes. ``"UNKNOWN"`` is a real
        fail-closed answer. This method NEVER authorises a fixed-coordinate
        click: every input still goes through ``_confirm`` -> the injected
        two-consecutive-frame verifier.
        """
        if self._classify_frame is None:
            return None

        def classify(payload: str, layer: str) -> str:
            observed, scores = self._classify_frame(payload)
            self._event_sink("continuous_ui_single_frame_probe", {
                "layer": layer,
                "observed": observed,
                "scores": {
                    key: round(float(value), 4) for key, value in scores.items()
                },
                "screenshots": 1,
                "clicks_sent": 0,
                "authorizes_click": False,
            })
            return observed

        try:
            observed = classify(self._frame(), "default")
            if (
                observed in ("HOME_PORT", "UNKNOWN")
                and self._capture_composited is not None
            ):
                self._neutralize_pointer()
                notice_state = classify(self._capture_composited(), "composited")
                if notice_state == "HOME_PORT_RETURN_NOTICE":
                    return notice_state
            return observed
        except Exception as exc:  # noqa: BLE001 - a broken probe must refuse, never guess
            self._event_sink("continuous_ui_single_frame_probe_failed", {
                "reason": f"{type(exc).__name__}: {exc}",
                "clicks_sent": 0,
                "authorizes_click": False,
            })
            return "UNKNOWN"

    def probe_settlement_page(self) -> str | None:
        """Name the settlement screen on display, or ``None`` when none is.

        Read-only: one all-state probe when the capability is wired, otherwise
        the historical one bounded probe per screen. No clicks, no navigation.
        """
        state = self.probe_drain_state()
        if state is not None:
            if state in self.SETTLEMENT_SCREENS:
                self._event_sink("continuous_ui_drain_page_detected", {
                    "state": state,
                    "clicks_sent": 0,
                })
                return state
            self._event_sink("continuous_ui_drain_page_absent", {
                "probed": list(self.SETTLEMENT_SCREENS),
                "clicks_sent": 0,
            })
            return None
        for screen in self.SETTLEMENT_SCREENS:
            if self._probe_state(screen, phase=f"drain probe {screen}") is not None:
                self._event_sink("continuous_ui_drain_page_detected", {
                    "state": screen,
                    "clicks_sent": 0,
                })
                return screen
        self._event_sink("continuous_ui_drain_page_absent", {
            "probed": list(self.SETTLEMENT_SCREENS),
            "clicks_sent": 0,
        })
        return None

    def probe_home_port(self) -> bool:
        """True when a stable Home Port is on display. Read-only."""
        state = self.probe_drain_state()
        if state is not None:
            return state == "HOME_PORT"
        return self._probe_state("HOME_PORT", phase="drain probe HOME_PORT") is not None

    def advance_settlement_page(self, screen: str) -> None:
        """Send the single input that ``screen`` accepts, then return.

        Refuses anything that is not one of the three settlement screens: a
        drain that could be asked to "advance" a home port or an unknown page
        would be a blind click, which is the one thing this chain must never do.
        """
        if screen not in self.SETTLEMENT_SCREENS:
            raise ContinuousUiDriverError(
                f"{screen!r} is not a settlement screen; refusing to click it"
            )
        self._confirm(screen, phase=f"drain advance {screen}", samples=REWARD_VERIFY_SAMPLES)
        advance = BONUS_NEXT if screen == "BONUS_ITEMS" else REWARD_ADVANCE
        # step 0 marks a drain-initiated input: the chain's own 1/2/3 belong to
        # the per-fleet settlement path and must not be conflated with it.
        self._click(advance, action=f"drain_advance_{screen.lower()}", step=0)
        self._sleep(REWARD_SETTLE_SECONDS)


class DetailSourceLike(Protocol):
    """The identity half of the evidence, as far as this driver needs it."""

    def reference(self, expedition_id: str) -> object | None: ...

    def require(self, expedition_id: str, screenshot_base64: str) -> DetailMatch: ...


class DispatchMarksLike(Protocol):
    """The two dispatch controls, as far as this driver needs them."""

    def require_decision(self, screenshot_base64: str) -> MarkMatch: ...

    def require_fleet(self, fleet_id: int, screenshot_base64: str) -> MarkMatch: ...

    def fleets(self) -> tuple[int, ...]: ...
