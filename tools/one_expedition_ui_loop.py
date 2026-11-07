"""Run one supervised Fleet 3 -> mission 3 UI dispatch transaction.

The production entry composes the fresh Poi monitor, durable scheduler and
dispatch-intent coordinator, ContinuousDispatchController, the settlement and
resupply controllers, single-tick ContinuousExpeditionSupervisor, and a visible
CDP UI driver. It exits only after Poi confirms the new mission tuple and
two-frame Home Port evidence. A fleet that is already waiting for its return
result, its reward, or its resupply is closed out first, in the same tick, by
the injected controllers; it does not wait for a return that has not happened
yet and it does not run unattended. All game input is visible UI input; no
game-server requests or page-state mutation are performed.
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import sys
import tempfile
import signal
import threading
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from kancollebot.adapters.poi.cdp_fleet_state import (  # noqa: E402
    CdpFleetStateError,
    PoiFleetMissionState,
    read_poi_fleet_states,
)
from kancollebot.adapters.poi.cdp_port_supply import (  # noqa: E402
    PoiPortSupplySnapshot,
    read_poi_port_supply_snapshot,
)
from kancollebot.adapters.poi.cdp_input import (  # noqa: E402
    CdpInputError,
    RecordedPoint,
    home_port_candidates,
    home_port_point,
)
from kancollebot.adapters.poi.cdp_websocket import (  # noqa: E402
    connect_game_webview,
    connect_poi_session,
)
from kancollebot.adapters.poi.home_port_button_vision import (  # noqa: E402
    HomePortButtonLocator,
    HomePortButtonRefused,
    HomePortVisionError,
)
from kancollebot.adapters.poi.ui_operation_lock import poi_ui_operation  # noqa: E402
from kancollebot.adapters.poi.ui_state_gate import (  # noqa: E402
    UiState,
    UiStateGateError,
    require_state,
)
from kancollebot.adapters.poi.visual_state_gate import (
    VisualStateError,
    classify_screenshot_png,
    classify_template_scores,
    home_reference_similarity,
)  # noqa: E402
from kancollebot.adapters.poi.visual_fingerprint import (  # noqa: E402
    FingerprintRanking,
    VisualFingerprintError,
    classify_screenshot_fingerprints,
    classify_supply_checkbox_state,
    rank_screenshot_fingerprints,
    screen_fingerprint,
)
from kancollebot.adapters.poi.expedition_countdown_vision import (  # noqa: E402
    DEFAULT_COUNTDOWN_TEMPLATE_PATH,
    CountdownReader,
    CountdownTemplateError,
    CountdownTemplates,
)
from kancollebot.observability.coarse_page import classify_coarse_page  # noqa: E402
from kancollebot.expeditions.page_navigation import (  # noqa: E402
    ROUTE_HOME_NAVIGATION,
)
from kancollebot.observability.page_observation import (  # noqa: E402
    DEFAULT_CHANGED_UNKNOWN_LIMIT,
    DEFAULT_TOTAL_BUDGET_SECONDS,
    PageObservationBudget,
    PageSample,
)
from kancollebot.calibration import (  # noqa: E402
    DEFAULT_CATALOG,
    DEFAULT_DETAIL_PATH,
    DEFAULT_MARKS_PATH,
    DEFAULT_POSITION_INDEX_PATH,
    ExpeditionListSession,
    decode_png,
    load_detail_index,
    load_marks,
    load_position_index,
    scan_page,
)
from kancollebot.expeditions.expedition_timer_wait import (  # noqa: E402
    DEFAULT_POST_EXPIRY_GRACE_SECONDS,
    ExpeditionTimerSleeper,
    event_sink_from_stdout,
    plan_timer_sleep,
)
from kancollebot.expeditions.expedition_early_return import (  # noqa: E402
    DEFAULT_EARLY_RETURN_THRESHOLD_SECONDS,
    EarlyReturnObservation,
    EarlyReturnOutcome,
    ExpeditionEarlyReturn,
    read_remaining_from_text,
)
from kancollebot.core.task_attribution import merge_trusted_event_fields  # noqa: E402
from kancollebot.observability.audit_policy import audit_gate  # noqa: E402
from kancollebot.observability.sink import (  # noqa: E402
    build_run_log_sink,
    correlation_capability,
)
from kancollebot.runtime_config import RUNTIME_CONFIG  # noqa: E402

# Long enough for the Home Port scene to finish settling before a frame is
# classified; the same settle the state gate uses between its two samples.
HOME_REFERENCE_SETTLE_SECONDS = 0.3

# How many page-fallback recoveries one process may attempt. Each attempt is
# either one call to the existing Home Port routine or one fresh observation -
# never a click of our own - and the count is a *run* budget, not a per-page
# one: a page that will not settle must stop the loop, not be retried forever.
PAGE_FALLBACK_MAX_ATTEMPTS = 3

# How long an unnamed page is given to finish rendering before it is read
# again. This is a screen-settle wait, not a poll: it happens at most
# PAGE_FALLBACK_MAX_ATTEMPTS times per process, only after a page withheld a
# tick, and nothing is read or sent during it. It mirrors the settle wait the
# observed Home Port recovery already uses before its first click - sampling a
# page mid-transition is what produced the 2026-11-07 0.88 readings.
PAGE_FALLBACK_REOBSERVE_SECONDS = 3.0

# How long before a fleet's own deadline the loop starts looking at the
# *displayed* countdown. This number decides nothing about the game: the wiki is
# explicit that only the expedition screen's own display may be trusted
# (「PCの時計や外部ツールで時間を計っているとズレが生じる可能性がある」). It only
# decides how early an attempt becomes worth making, and the attempt itself
# refuses to act on anything above one minute.
DEFAULT_EARLY_RETURN_LOOKAHEAD_SECONDS = 90.0

# The passive api_req_mission/result observer that the real single-fleet
# closed loop used as an EXTERNAL process (logs/single_fleet_witness_run1.log,
# 10:36:50Z). Production now owns it in-process; the script, the allowlist and
# the durable ``mission_result_observed`` row format are unchanged.
DEFAULT_MISSION_RESULT_OBSERVER = (
    ROOT / "tools" / "hand_sandbox" / "poi_mission_result_observer_20261105.js"
)
DEFAULT_MISSION_RESULT_TIMEOUT_SECONDS = 180.0
DEFAULT_WITNESS_WAIT_SECONDS = 2.0


# Legacy helper/test path only; production target is RUNTIME_CONFIG.
LEGACY_HELPER_FLEET_ID = 2
FLEET_ID = LEGACY_HELPER_FLEET_ID
MISSION_ID = 3
HOME_PORT = home_port_point()
SORTIE = RecordedPoint(280, 425, "sortie")
EXPEDITION_MODE = RecordedPoint(1000, 360, "expedition-mode")
MISSION_ROW = RecordedPoint(350, 350, "mission-3-security-task")
# State-dependent: this is the mission-confirm control on MISSION_SELECTED, but overlaps the expedition-start control on fleet selection.
# The pre-click MISSION_SELECTED checkpoint is mandatory.
MISSION_CONFIRM = RecordedPoint(1030, 670, "mission-confirm")
FLEET_2_TAB = RecordedPoint(590, 170, "fleet-2-tab")
FLEET_3_TAB = RecordedPoint(630, 170, "fleet-3-tab")
EXPEDITION_START = RecordedPoint(920, 670, "expedition-start")
RETURN_CONFIRM = RecordedPoint(1140, 670, "returned-expedition-confirm")

# The reward chain, in order. Each screen accepts a click anywhere, so any click
# sent while one is up advances the chain - which is why only the settlement
# controller, which proves every screen, is allowed to send one.
REWARD_SCREEN_STATES = (
    UiState.HOME_PORT_RETURN_NOTICE.value,
    UiState.RETURN_RESULT.value,
    UiState.BONUS_ITEMS.value,
)

# Every click needs fresh evidence for its pre- and post-state. Pre-dispatch
# checkpoints use two consecutive in-memory Ooi fingerprints. Return/bonus
# checkpoints remain a separate production-readiness gate.
DISPATCH_STEPS = (
    (SORTIE, 1.0, "HOME_PORT", "SORTIE_MENU"),
    (EXPEDITION_MODE, 1.0, "SORTIE_MENU", "EXPEDITION_LIST"),
    (MISSION_ROW, 0.5, "EXPEDITION_LIST", "MISSION_SELECTED"),
    (MISSION_CONFIRM, 0.7, "MISSION_SELECTED", "FLEET_2_SELECTED"),
    (EXPEDITION_START, 1.5, "FLEET_2_SELECTED", "EXPEDITION_ACTIVE"),
)

# R3-I: mission composition numbers live in ONE versioned static table
# (src/kancollebot/expeditions/data/expedition_requirements.json), not in the
# composition roots. `_mission_requirement()` is the only adapter from that
# table to the legacy composition gate; the full check is performed by
# ExpeditionRequirementChecker before any click.


class LoopError(RuntimeError):
    """The observed state or UI sequence no longer matches the fixed loop."""


class SettleOnlyDispatcher:
    """A dispatch controller that can never dispatch.

    ``--settle-only`` exists to close out a fleet that is already on an
    expedition: settle the reward chain, resupply, stop. It still runs inside the
    supervisor, which only knows one dispatch contract, so it is handed this
    instead - one that satisfies the shared-scheduler/shared-reader wiring checks
    and refuses the one thing a settle-only run must never do.
    """

    def __init__(
        self, *, scheduler, state_reader, event_sink, mission_requirements, ui_arbiter=None
    ):
        self.scheduler = scheduler
        self.state_reader = state_reader
        self.event_sink = event_sink
        self.mission_requirements = mission_requirements
        # Carried only so the entry's single-arbiter assertion can compare it:
        # this controller sends no clicks, so it never asks the arbiter.
        self.ui_arbiter = ui_arbiter

    def dispatch_one_ready(self, fleet_id: int) -> None:
        raise LoopError(
            "settle-only run: the fleet is ready, and this entry closes out a "
            "returned fleet without ever sending a start click"
        )


def _mission_requirement(args, fleet_id: int, mission_id: int):
    """Adapt ONE static, versioned rule to the legacy composition gate.

    R3-I removed the per-entry hardcoded table: a mission without a rule is
    refused rather than guessed, and the full condition evaluation is done by
    ``ExpeditionRequirementChecker`` on the fresh Poi baseline. The two CLI
    overrides are kept only as an operator assertion read from the mission
    panel; they may make the gate STRICTER than the static rule, never weaker.
    """
    from kancollebot.expeditions.expedition_rule_data import (
        DEFAULT_RULE_SET,
        mission_composition_requirement_for,
    )
    from kancollebot.expeditions.mission_eligibility import MissionCompositionRequirement

    minimum_ships = getattr(args, "minimum_ships", None)
    flagship_level = getattr(args, "flagship_level", None)
    if (minimum_ships is None) != (flagship_level is None):
        raise LoopError(
            "--minimum-ships and --flagship-level must be given together; the "
            "composition gate needs both numbers"
        )
    if DEFAULT_RULE_SET.requirement_for(mission_id) is None:
        raise LoopError(
            f"no static requirement rule for fleet {fleet_id} / mission "
            f"{mission_id} in {DEFAULT_RULE_SET.rule_version}; refusing to "
            "invent one from the mission panel"
        )
    baseline = mission_composition_requirement_for(mission_id)
    if minimum_ships is None:
        return baseline
    for name, value in (
        ("--minimum-ships", minimum_ships),
        ("--flagship-level", flagship_level),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise LoopError(f"{name} must be a positive integer")
    if (
        minimum_ships < baseline.minimum_ships
        or flagship_level < baseline.minimum_flagship_level
    ):
        raise LoopError(
            f"explicit composition override ({minimum_ships}, {flagship_level}) is "
            f"weaker than the static rule for mission {mission_id} "
            f"({baseline.minimum_ships}, {baseline.minimum_flagship_level}); "
            "refusing to weaken the gate"
        )
    return MissionCompositionRequirement(
        mission_id=mission_id,
        minimum_ships=minimum_ships,
        minimum_flagship_level=flagship_level,
    )


def _fleet(states: tuple[PoiFleetMissionState, ...], fleet_id: int = LEGACY_HELPER_FLEET_ID) -> PoiFleetMissionState:
    matches = [state for state in states if state.fleet_id == fleet_id]
    if len(matches) != 1:
        raise LoopError(f"Expected exactly one fleet {fleet_id} in the Poi snapshot")
    return matches[0]


def _idle(state: PoiFleetMissionState) -> bool:
    return state.mission == (0, 0, 0, 0)


LOG_PATH: Path | None = None
RUN_ID = "uninitialized"
HOME_REFERENCE_BASE64: str | None = None
_VISUAL_DOCUMENT_CACHE: dict[tuple[str, int, int], object] = {}


def _read_visual_document(path: Path) -> object:
    # Read each unchanged fingerprint document only once per process.
    try:
        metadata = path.stat()
    except OSError:
        return json.loads(path.read_text(encoding="utf-8"))

    resolved_path = str(path.resolve())
    cache_key = (resolved_path, metadata.st_mtime_ns, metadata.st_size)
    if cache_key in _VISUAL_DOCUMENT_CACHE:
        return _VISUAL_DOCUMENT_CACHE[cache_key]

    document = json.loads(path.read_text(encoding="utf-8"))
    for stale_key in tuple(_VISUAL_DOCUMENT_CACHE):
        if stale_key[0] == resolved_path:
            del _VISUAL_DOCUMENT_CACHE[stale_key]
    if len(_VISUAL_DOCUMENT_CACHE) >= 4:
        _VISUAL_DOCUMENT_CACHE.pop(next(iter(_VISUAL_DOCUMENT_CACHE)))
    _VISUAL_DOCUMENT_CACHE[cache_key] = document
    return document


def _run_log_start_payload(
    *,
    dispatch_fleet_ids,
    database: object,
    ui_navigation_enabled: bool,
    game_actions_enabled: bool,
    tool_run_id: str,
    emitting_sink: object,
) -> dict[str, object]:
    """The durable ``run_started`` payload for the closed-loop entry.

    Live 2026-11-06: this entry never wrote a durable run boundary - the
    stdout ``run_started`` row never reached the run log, so the ledger had
    neither a run delimiter nor the ``correlation_capable`` field the monitor
    entry writes. The stdout emitter and the RunLogSink also mint separate
    run ids (a kept production fact, not an accident to paper over):
    ``tool_run_id`` writes the stdout domain into the ledger so a reader can
    join the two streams without guessing from timestamps. The capability
    fields come from the sink the components actually emit through. When the
    sink is prebuilt (it must be, so the startup recovery's gate rows are
    durable before the clicks they name) the tee that forms later inherits
    its capability from this same member, so reporting the sink's own
    capability here matches what the eventual tee would say.
    """
    return {
        "database": str(database),
        "dispatch_fleets": list(dispatch_fleet_ids),
        "ui_navigation_enabled": bool(ui_navigation_enabled),
        "game_actions_enabled": bool(game_actions_enabled),
        "tool_run_id": tool_run_id,
        **correlation_capability(emitting_sink).as_fields(),
    }


_ACTIVE_RUN_LOG_SINK: list[object] = []


def _remember_run_log_sink(sink: object | None) -> None:
    """Keep the one live run log of this process reachable from the stop path.

    ``run()`` builds the sink before the game session opens; the fail-closed
    handler in ``_main_impl`` cannot see local names. At most one sink is
    ever live per process, so a one-slot list is enough, and ``None`` clears
    it so a later run cannot stop a sink that already ended.
    """
    _ACTIVE_RUN_LOG_SINK.clear()
    if sink is not None:
        _ACTIVE_RUN_LOG_SINK.append(sink)


def _active_run_log_sink() -> object | None:
    """The live run log, or None when ``--run-log-dir`` is off.

    The startup recovery calls this because it runs before ``build_runtime``
    composes the tee - the sink is the only durable emitter that exists yet.
    """
    return _ACTIVE_RUN_LOG_SINK[0] if _ACTIVE_RUN_LOG_SINK else None


def _build_and_start_run_log(args: argparse.Namespace) -> object | None:
    """Build and start the run log before the game session opens.

    Returns the sink for ``build_runtime`` to tee, or None (log off). The
    ``isinstance`` guard keeps a stub's "unset" ``run_log_dir`` meaning off,
    exactly like the CLI default.
    """
    run_log_dir = args.run_log_dir
    if not isinstance(run_log_dir, (str, Path)):
        return None
    from kancollebot.task_scope import TaskKind  # local: mirrors the other use

    run_log_sink = build_run_log_sink(
        directory=run_log_dir,
        task_kind=TaskKind.EXPEDITION.value,
    )
    _remember_run_log_sink(run_log_sink)
    plan_fleet_ids, _plan_missions = _resolve_dispatch_plan(args)
    run_log_sink.start(
        _run_log_start_payload(
            dispatch_fleet_ids=tuple(plan_fleet_ids),
            database=args.database,
            ui_navigation_enabled=bool(args.enable_ui_refresh),
            game_actions_enabled=bool(args.authorize_ui_actions),
            tool_run_id=RUN_ID,
            emitting_sink=run_log_sink,
        )
    )
    return run_log_sink


def _stop_run_log(reason: str, **fields: object) -> None:
    """Record why this run ended - never raise.

    Live 2026-11-06: the closed-loop entry wrote a durable ``run_started``
    but no ``run_stopped`` on any path, so a reader could not tell a run
    that finished from one that died. The monitor entry already stops its
    sink in a ``finally``; this is the same lifecycle row for this entry.
    Lifecycle rows are diagnostics, not a gate: a stop record that fails to
    write must never replace the exception that actually ended the run.
    """
    for sink in _ACTIVE_RUN_LOG_SINK:
        try:
            sink.stop(reason, fields)
        except Exception:
            pass


def _print_event(event: str, state: PoiFleetMissionState | None = None, **extra: object) -> None:
    # P0 R2: the envelope is TRUSTED. Previously ``**extra`` came last, so any
    # caller could overwrite ``event`` / ``run_id`` / ``timestamp_utc`` /
    # ``task_kind`` and forge a row's identity in a shared automation log. The
    # merge contract drops every reserved key from ``extra`` before writing the
    # envelope, so attribution can only come from the composition root.
    trusted: dict[str, object] = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "run_id": RUN_ID,
        # Structured attribution: this jsonl is one of several automation logs,
        # and future tasks must be distinguishable without parsing event names.
        "task_kind": RUNTIME_CONFIG.dispatch_policy_task_kind,
        "event": event,
    }
    merged = merge_trusted_event_fields(extra, trusted=trusted)
    # Keep the historical key order (envelope first, diagnostics after) so the
    # jsonl stays byte-compatible for readers that compare raw lines.
    payload: dict[str, object] = dict(trusted)
    payload.update({key: value for key, value in merged.items() if key not in trusted})
    if state is not None:
        payload["fleet_id"] = state.fleet_id
        payload["mission"] = list(state.mission)
        payload["ship_count"] = len([ship_id for ship_id in state.ship_ids if ship_id > 0])
    line = json.dumps(payload, ensure_ascii=False)
    print(line, flush=True)
    _append_log_line(line)


def _single_frame_ranking_fields(ranking: FingerprintRanking) -> dict[str, object]:
    """Diagnostic fields for one all-state ranking; probe only, never a click gate.

    ``_print_event`` reserves ``state`` for a ``PoiFleetMissionState`` and reads
    ``state.fleet_id``. The probe's UI state is a plain string, so sending it as
    ``state=`` raises ``AttributeError``. The drain probe then fails closed to
    ``UNKNOWN`` and the global drain performs zero actions (live 2026-11-06
    stage-0 run). Keep the string under ``observed`` instead.
    """
    return {
        "observed": ranking.state,
        "best_name": ranking.best_name,
        "best_score": round(ranking.best_score, 4),
        "second_name": ranking.second_name,
        "second_score": round(ranking.second_score, 4),
        "accepted": ranking.accepted,
        # Read back from the ranking so the event records the values that
        # actually produced the verdict (no second source of truth).
        "minimum_score": ranking.minimum_score,
        "minimum_margin": ranking.minimum_margin,
        "authorizes_click": False,
    }


def _append_log_line(line: str) -> None:
    """Append one already-serialised row, rotating the file when it outgrows its cap.

    This jsonl predates the unified run log and had no bound: a loop left running
    appends forever and can only stop by filling the disk. Rotation keeps exactly
    one previous file (``<name>.1``) so the current stream is always the one at
    the configured path - readers that open ``LOG_PATH`` keep working.

    Deliberately *not* a retention policy: nothing here deletes evidence, and the
    three-day window belongs to the unified run log. A rotation failure
    propagates, exactly like a failed append, because a log that silently stops
    recording is worse than a loop that stops.
    """
    if LOG_PATH is None:
        return
    cap = RUNTIME_CONFIG.ui_loop_log_max_bytes
    try:
        size = LOG_PATH.stat().st_size
    except FileNotFoundError:
        size = 0
    if size >= cap:
        rotated = LOG_PATH.with_name(LOG_PATH.name + ".1")
        if rotated.exists():
            rotated.unlink()
        os.replace(LOG_PATH, rotated)
    with LOG_PATH.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(line + "\n")


def _write_diagnostic_capture(directory, state: str, payload_base64: str) -> None:
    """Persist one already-confirmed screenshot when a caller asks for it.

    Screenshots normally stay in memory; this is only reached when a caller
    passes an explicit directory, which exists so a calibration pass can be run
    offline afterwards. A failure here is reported and swallowed: the capture is
    evidence for later analysis, never a precondition for the state gate.
    """
    target = None
    try:
        folder = Path(directory)
        folder.mkdir(parents=True, exist_ok=True)
        data = base64.b64decode(payload_base64, validate=True)
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("payload is not a PNG")
        target = folder / f"{state}.png"
        target.write_bytes(data)
    except Exception as exc:  # noqa: BLE001 - diagnostics must not gate safety
        _print_event(
            "diagnostic_capture_failed",
            ui_state=state,
            reason=f"{type(exc).__name__}: {exc}",
        )
        return
    _print_event(
        "diagnostic_capture_written", ui_state=state, path=str(target), bytes=len(data)
    )


def _require_ui_state(
    game_session,
    expected: UiState,
    *,
    visual_attempts: int = 3,
    capture_dir: str | Path | None = None,
    from_surface: bool = True,
) -> str:
    """Require accessible labels or two consecutive in-memory visual matches.

    ``from_surface`` is forwarded to the capture call. Only a state that lives
    on a composited overlay layer - today just the Home Port return notice -
    may ask for the slower composited surface; every other state keeps the
    surface its calibrated fingerprint was recorded with.
    """
    if not isinstance(from_surface, bool):
        raise ValueError("from_surface must be a bool")
    if (
        isinstance(visual_attempts, bool)
        or not isinstance(visual_attempts, int)
        or not 2 <= visual_attempts <= 12
    ):
        raise ValueError("visual_attempts must be an integer from 2 through 12")
    result = game_session.transport.send("Accessibility.getFullAXTree", {})
    nodes = result.get("nodes") if isinstance(result, dict) else None
    try:
        require_state(nodes, expected)
        evidence = "accessibility"
        _print_event("ui_state_confirmed", ui_state=expected.value, evidence=evidence)
        return evidence
    except (UiStateGateError, ValueError) as accessible_error:
        fingerprint_path = Path(
            os.environ.get(
                "KANCOLLEBOT_FINGERPRINTS_PATH",
                ROOT / "src" / "kancollebot" / "adapters" / "poi" / "ooi_1200x720_fingerprints.json",
            )
        )
        template_dir = os.environ.get("KANCOLLEBOT_TEMPLATE_DIR")
        try:
            document = None
            if fingerprint_path.is_file():
                document = _read_visual_document(fingerprint_path)
            fingerprint_states = document.get("states", {}) if isinstance(document, dict) else {}
            checkbox_states = {
                state for state in UiState
                if state.value.startswith(("SUPPLY_FLEET_2_SHIP_", "SUPPLY_FLEET_3_SHIP_"))
                and state.value.endswith(("_CHECKED", "_UNCHECKED"))
            }
            if expected in checkbox_states:
                if not isinstance(document, dict):
                    raise LoopError("Parent Ooi fingerprint document is unavailable") from accessible_error
                checkbox_path = Path(
                    os.environ.get(
                        "KANCOLLEBOT_SUPPLY_CHECKBOX_FINGERPRINTS_PATH",
                        ROOT / "src" / "kancollebot" / "adapters" / "poi" / "supply_checkbox_fingerprints.json",
                    )
                )
                if not checkbox_path.is_file():
                    raise LoopError(
                        f"Supply checkbox fingerprint file is missing: {checkbox_path}"
                    ) from accessible_error
                checkbox_document = _read_visual_document(checkbox_path)
                checkbox_descriptors = checkbox_document.get("states", {})
                if expected.value not in checkbox_descriptors:
                    raise LoopError(
                        f"Supply checkbox fingerprint is missing for {expected.value}"
                    ) from accessible_error
                state_parts = expected.value.split("_")
                fleet_index = int(state_parts[2])
                ship_index = int(state_parts[4])

                consecutive_matches = 0
                for attempt in range(visual_attempts):
                    game_session.transport.send(
                        "Input.dispatchMouseEvent",
                        {"type": "mouseMoved", "x": 1195, "y": 715},
                    )
                    time.sleep(0.15)
                    payload = game_session.input.capture_screenshot(from_surface=from_surface)
                    observed, scores = classify_supply_checkbox_state(
                        payload, document, checkbox_document,
                        fleet_index=fleet_index, ship_index=ship_index
                    )
                    _print_event(
                        "ui_supply_checkbox_state_check",
                        attempt=attempt + 1,
                        expected=expected.value,
                        observed=observed,
                        scores={key: round(value, 4) for key, value in scores.items()},
                        screenshot_written_to_disk=False,
                    )
                    if observed == expected.value:
                        consecutive_matches += 1
                        if consecutive_matches >= 2:
                            evidence = "two_consecutive_in_memory_parent_and_checkbox_fingerprints"
                            _print_event(
                                "ui_state_confirmed",
                                ui_state=expected.value,
                                evidence=evidence,
                            )
                            return evidence
                    elif observed != "UNKNOWN":
                        raise LoopError(
                            f"Expected {expected.value}; visual classifier observed {observed}"
                        ) from accessible_error
                    else:
                        consecutive_matches = 0
                    if attempt < visual_attempts - 1:
                        time.sleep(0.25)
                raise LoopError(
                    f"Expected {expected.value}; no stable parent+checkbox fingerprint match after {visual_attempts} attempts"
                ) from accessible_error

            if expected.value in fingerprint_states:
                consecutive_matches = 0
                for attempt in range(visual_attempts):
                    game_session.transport.send(
                        "Input.dispatchMouseEvent",
                        {"type": "mouseMoved", "x": 1195, "y": 715},
                    )
                    time.sleep(0.15)
                    payload = game_session.input.capture_screenshot(from_surface=from_surface)
                    observed, scores = classify_screenshot_fingerprints(payload, document)
                    _print_event(
                        "ui_fingerprint_state_check",
                        attempt=attempt + 1,
                        expected=expected.value,
                        observed=observed,
                        scores={key: round(value, 4) for key, value in scores.items()},
                        screenshot_written_to_disk=False,
                    )
                    if observed == expected.value:
                        consecutive_matches += 1
                        if consecutive_matches >= 2:
                            evidence = "two_consecutive_in_memory_ooi_fingerprints"
                            _print_event(
                                "ui_state_confirmed",
                                ui_state=expected.value,
                                evidence=evidence,
                            )
                            if capture_dir is not None:
                                _write_diagnostic_capture(
                                    capture_dir, expected.value, payload
                                )
                            return evidence
                    elif observed != "UNKNOWN":
                        raise LoopError(
                            f"Expected {expected.value}; Ooi fingerprint classified {observed}"
                        ) from accessible_error
                    else:
                        consecutive_matches = 0
                    if attempt < visual_attempts - 1:
                        time.sleep(0.25)
                if not template_dir:
                    raise LoopError(
                        f"Expected {expected.value}; no stable fingerprint match was obtained after {visual_attempts} attempts"
                    ) from accessible_error

            if not template_dir:
                raise LoopError(
                    f"Expected {expected.value}; no fingerprint for this state and no KANCOLLEBOT_TEMPLATE_DIR configured"
                ) from accessible_error
            template_path = Path(template_dir)
            if not template_path.is_dir():
                raise LoopError(f"Visual template directory does not exist: {template_path}") from accessible_error

            consecutive_matches = 0
            for attempt in range(visual_attempts):
                game_session.transport.send(
                    "Input.dispatchMouseEvent",
                    {"type": "mouseMoved", "x": 1195, "y": 715},
                )
                time.sleep(0.15)
                payload = game_session.input.capture_screenshot(from_surface=from_surface)
                visual_state, scores = classify_screenshot_png(payload, template_path)
                if HOME_REFERENCE_BASE64:
                    scores["home_reference_similarity"] = home_reference_similarity(
                        payload, HOME_REFERENCE_BASE64
                    )
                    visual_state = classify_template_scores(scores)
                _print_event(
                    "ui_template_state_check",
                    attempt=attempt + 1,
                    expected=expected.value,
                    observed=visual_state,
                    scores={key: round(value, 4) for key, value in scores.items()},
                    screenshot_written_to_disk=False,
                )
                if visual_state == expected.value:
                    consecutive_matches += 1
                    if consecutive_matches >= 2:
                        evidence = "two_consecutive_in_memory_template_matches"
                        _print_event(
                            "ui_state_confirmed",
                            ui_state=expected.value,
                            evidence=evidence,
                        )
                        return evidence
                elif visual_state != "UNKNOWN":
                    raise LoopError(
                        f"Expected {expected.value}; visual template classifier returned {visual_state}"
                    ) from accessible_error
                else:
                    consecutive_matches = 0
                if attempt < visual_attempts - 1:
                    time.sleep(0.25)
            raise LoopError(
                f"Expected {expected.value}; no stable template match was obtained after {visual_attempts} attempts"
            ) from accessible_error
        except (VisualFingerprintError, VisualStateError, OSError, ValueError) as visual_error:
            raise LoopError(
                f"Accessible state unavailable ({accessible_error}); visual fallback failed ({visual_error})"
            ) from visual_error


def _capture_home_reference(game_session) -> None:
    """Capture a transient Home Port baseline only after fingerprint confirmation."""
    global HOME_REFERENCE_BASE64
    _require_ui_state(game_session, UiState.HOME_PORT)
    game_session.transport.send(
        "Input.dispatchMouseEvent",
        {"type": "mouseMoved", "x": 1195, "y": 715},
    )
    time.sleep(0.15)
    HOME_REFERENCE_BASE64 = game_session.input.capture_screenshot()
    _print_event(
        "home_reference_captured",
        evidence="in_memory_ooi_fingerprint",
        screenshot_written_to_disk=False,
    )


def _home_port_ui_layer_is_hidden(game_session) -> bool:
    """True when the port background is on screen but its UI layer has faded.

    The Home Port fades its whole UI layer after a click on the 母港 control, and
    the faded frame still classifies as *something* - every Home Port crop keeps
    matching the unchanged background, so the scores flatten around 0.88-0.91 and
    the gate correctly refuses to call it HOME_PORT. That refusal is right, but
    it leaves the loop stranded: the 母港 button is part of the hidden layer, so
    clicking it again cannot bring the UI back.

    The test is deliberately narrow - the best-scoring state must be HOME_PORT
    itself, so a sortie menu or expedition list can never be mistaken for a faded
    port, and the caller may then use a single area click to wake the layer.
    """
    fingerprint_path = Path(
        os.environ.get(
            "KANCOLLEBOT_FINGERPRINTS_PATH",
            ROOT / "src" / "kancollebot" / "adapters" / "poi" / "ooi_1200x720_fingerprints.json",
        )
    )
    if not fingerprint_path.is_file():
        return False
    try:
        document = _read_visual_document(fingerprint_path)
        payload = game_session.input.capture_screenshot()
        observed, scores = classify_screenshot_fingerprints(payload, document)
    except Exception:
        return False
    if observed != "UNKNOWN" or not scores:
        return False
    best_name = max(scores, key=scores.get)
    return best_name == UiState.HOME_PORT.value and scores[best_name] >= 0.85


def _visible_settlement_screen(game_session) -> str | None:
    """The proven reward screen on display right now, or None.

    The three reward screens advance on a click *anywhere*, so the Home Port
    recovery must never click while one of them is up: the click would move the
    reward chain forward without proving which screen it landed on. Live
    2026-11-05 the loop started on a result screen left by an interrupted
    settlement, sent two recovery clicks at the 母港 point, and walked the chain
    result -> bonus items -> port blind. The settlement controller is the only
    thing allowed to advance those screens, because it proves each one.

    Uses the same classifier and thresholds as the UI state gate, so a screen
    the gate would refuse to accept is also a screen the recovery leaves alone.
    """
    fingerprint_path = Path(
        os.environ.get(
            "KANCOLLEBOT_FINGERPRINTS_PATH",
            ROOT / "src" / "kancollebot" / "adapters" / "poi" / "ooi_1200x720_fingerprints.json",
        )
    )
    if not fingerprint_path.is_file():
        return None
    try:
        document = _read_visual_document(fingerprint_path)
        payload = game_session.input.capture_screenshot(
            from_surface=False
        )
        observed, _scores = classify_screenshot_fingerprints(payload, document)
    except Exception:
        return None
    return observed if observed in REWARD_SCREEN_STATES else None


def _reward_screen_on_any_layer(game_session) -> str | None:
    """The reward screen proven on either capture layer, or None.

    The composited layer is the authoritative one for the return notice, but the
    RETURN_RESULT and BONUS_ITEMS fingerprints were captured from the default
    surface (``fromSurface=True``), so a composited UNKNOWN is not proof that no
    reward screen is up. Recovery refuses on a screen proven by *either* layer:
    checking both is read-only and can only widen the refusal, never the click.
    """
    screen = _visible_settlement_screen(game_session)
    if screen is not None:
        return screen
    fingerprint_path = Path(
        os.environ.get(
            "KANCOLLEBOT_FINGERPRINTS_PATH",
            ROOT / "src" / "kancollebot" / "adapters" / "poi" / "ooi_1200x720_fingerprints.json",
        )
    )
    try:
        document = _read_visual_document(fingerprint_path)
        payload = game_session.input.capture_screenshot(from_surface=True)
        observed, _scores = classify_screenshot_fingerprints(payload, document)
    except Exception:
        return None
    return observed if observed in REWARD_SCREEN_STATES else None


def _probe_startup_screen(game_session):
    """One start-up CDP snapshot of both layers; the composited layer decides.

    The reward screens are drawn on a composited overlay that the default
    ``fromSurface=True`` capture drops. Live 2026-11-05 the same frame
    classified HOME_PORT_RETURN_NOTICE 0.9987 on the composited layer and
    HOME_PORT 0.9986 on the default one. A startup decision taken from the
    default layer would therefore walk straight into Home Port recovery and
    click the reward chain blind, so the default reading is recorded as audit
    only. Any CDP or classifier failure propagates: no screen proof, no clicks.
    """
    from kancollebot.expeditions.startup_settlement import select_startup_screen

    fingerprint_path = Path(
        os.environ.get(
            "KANCOLLEBOT_FINGERPRINTS_PATH",
            ROOT / "src" / "kancollebot" / "adapters" / "poi" / "ooi_1200x720_fingerprints.json",
        )
    )
    document = _read_visual_document(fingerprint_path)
    composited_payload = game_session.input.capture_screenshot(from_surface=False)
    composited_state, composited_scores = classify_screenshot_fingerprints(
        composited_payload, document
    )
    try:
        default_payload = game_session.input.capture_screenshot(from_surface=True)
        default_state, _default_scores = classify_screenshot_fingerprints(
            default_payload, document
        )
    except (CdpInputError, VisualFingerprintError, OSError, ValueError):
        # The default layer is audit data; a broken second capture must not
        # turn a proven composited screen into an unproven one.
        default_state = None
    # The two layers carry different reward screens. The notice is drawn
    # on the composited overlay and is invisible in the default capture;
    # the result and bonus screens are ordinary page content and were
    # calibrated on the default surface. A reward screen proven on
    # *either* layer is the startup screen, and the composited layer keeps
    # priority when both prove one. Live 2026-11-05 a result screen
    # classified 1.0000 on the default layer while the composited one said
    # UNKNOWN - a composited-only probe walked straight past it.
    composited_reward = composited_state in REWARD_SCREEN_STATES
    default_reward = default_state in REWARD_SCREEN_STATES
    if composited_reward:
        proven_state, proven_layer = composited_state, "composited"
    elif default_reward:
        proven_state, proven_layer = default_state, "default_surface"
    else:
        proven_state, proven_layer = composited_state, "composited"
    screen = select_startup_screen(
        composited_state=proven_state, default_surface_state=default_state
    )
    score = composited_scores.get(composited_state) if composited_scores else None
    _print_event(
        "startup_screen_snapshot",
        composited_state=composited_state,
        composited_state_score=None if score is None else round(float(score), 4),
        default_surface_state=default_state,
        startup_screen=screen.state,
        proven_layer=proven_layer,
        layers_disagree=screen.layers_disagree,
        evidence=screen.evidence,
        clicks_sent=0,
        game_api_requests=False,
    )
    return screen


def _probe_startup_attribution(args, runtime, target_fleet_id):
    """Resolve the recorded ship-ID set that ties a reward chain to a fleet.

    Three sources, in order of directness: the scheduler's own mission, the
    dispatch Poi confirmed (durable event log), and the dispatch intent. The
    event log is read once and also yields the evidence a blocked fleet needs
    to be recovered, plus the proof that a reward chain was ever closed, plus
    the expedition-result witness the game itself stated.
    """
    from kancollebot.expeditions.blocked_recovery import (
        read_dispatch_evidence,
        read_settlement_completion,
    )
    from kancollebot.expeditions.continuous_dispatch_controller import (
        SqliteDispatchEventLog,
    )
    from kancollebot.expeditions.dispatch_recovery import SqliteDispatchIntentStore
    from kancollebot.expeditions.mission_result_witness import (
        latest_witness_for_fleet,
    )
    from kancollebot.expeditions.startup_settlement import resolve_recorded_composition

    events = SqliteDispatchEventLog(str(args.database)).load_all()
    lookup = read_dispatch_evidence(events, target_fleet_id)
    settlement_completed_at = read_settlement_completion(events, target_fleet_id)
    witness = latest_witness_for_fleet(events, target_fleet_id)
    recorded = resolve_recorded_composition(
        mission=runtime.scheduler.fleet(target_fleet_id).mission,
        intent=SqliteDispatchIntentStore(args.database).get(target_fleet_id),
    )
    if recorded is None and lookup.evidence is not None:
        recorded = (lookup.evidence.ship_ids, "dispatch_confirmed_by_poi")
    attribution = recorded if recorded is not None else (None, None)
    return attribution, lookup, settlement_completed_at, witness


def _recovery_emit(
    event_sink: object | None, event: str, *, gating: bool = False, **fields: object
) -> None:
    """Emit one home-port-recovery row; durable when a sink is supplied.

    ``gating`` rows run BEFORE an irreversible click and fail closed: a broken
    audit log stops the click, exactly like the driver's ``_click``. Every
    other row is diagnostic - losing one must not break a recovery that is
    already in motion.
    """
    if event_sink is None:
        return
    if gating:
        event_sink(event, fields)
        return
    try:
        event_sink(event, fields)
    except Exception:
        pass


def _visual_home_port_point(game_session, event_sink: object | None = None) -> RecordedPoint:
    """Locate the rotating 母港 badge on a fresh frame; never guess a coordinate.

    The startup recovery used to cycle four fixed interior offsets of the
    top-left button.  That control rotates continuously and changes colour by
    screen, so the recovery now uses the same rotation-invariant visual locator
    as the return refresh and the dispatch driver.  A refusal is fail-closed:
    no click is sent and the run log carries the ROI, features, confidence,
    thresholds, candidate count and reason.
    """
    locator = HomePortButtonLocator(
        capture=lambda: game_session.input.capture_screenshot(from_surface=True)
    )
    try:
        detection = locator.locate()
    except HomePortButtonRefused as exc:
        fields = {**exc.evidence.as_log_fields(), "failure_reason": exc.reason}
        _print_event("home_port_button_detection_failed", **fields)
        _recovery_emit(
            event_sink, "home_port_button_detection_failed",
            reason=exc.reason, roi=list(exc.evidence.roi),
            confidence=exc.evidence.confidence,
            candidate_count=exc.evidence.candidate_count,
            click_authorized=False,
        )
        raise LoopError(
            f"home_port_button_not_detected:{exc.reason}; zero clicks sent"
        ) from exc
    except HomePortVisionError as exc:
        _print_event(
            "home_port_button_detection_failed",
            failure_reason=type(exc).__name__, reason=str(exc),
            roi=locator.thresholds.as_dict()["roi"], candidate_count=0,
            click_authorized=False,
        )
        _recovery_emit(
            event_sink, "home_port_button_detection_failed",
            reason=str(exc), click_authorized=False,
        )
        raise LoopError(
            f"home_port_button_frame_unusable:{exc}; zero clicks sent"
        ) from exc
    fields = detection.as_log_fields()
    _print_event("home_port_button_detected", **fields)
    _recovery_emit(
        event_sink, "home_port_button_detected",
        center=fields.get("center"), confidence=fields.get("confidence"),
        roi=fields.get("roi"), candidate_count=fields.get("candidate_count"),
        click_authorized=True,
    )
    return detection.point


def _recover_home_port(
    game_session, attempts: int = 3, force_click: bool = False, event_sink: object | None = None
) -> None:
    """Use bounded top-left navigation retries, requiring state evidence after each click.

    The recovery cycles a bounded set of interior points within the large
    top-left 母港 button, then verifies the result with the UI state gate. An
    incorrect layout fails closed after the bounded set is exhausted.

    Live 2026-11-06 02:08Z the tool recovered nothing because the operator had
    already clicked home - and the durable log could not even tell, because
    every recovery row went to stdout only. With an ``event_sink`` the whole
    recovery (including "nothing needed to happen") lands in the run log.
    """
    if not force_click:
        try:
            _require_ui_state(game_session, UiState.HOME_PORT)
            _print_event("home_port_recovery_confirmed", attempt=0)
            _recovery_emit(event_sink, "home_port_recovery_confirmed", attempt=0)
            return
        except (UiStateGateError, LoopError) as exc:
            _print_event("home_port_state_not_confirmed", attempt=0, error=str(exc))
            _recovery_emit(
                event_sink, "home_port_state_not_confirmed", attempt=0,
                error=str(exc)[:200],
            )
    settlement_screen = _reward_screen_on_any_layer(game_session)
    if settlement_screen is not None:
        raise LoopError(
            f"The account is showing {settlement_screen}, a reward screen that advances on a "
            "click anywhere. Home Port recovery will not send that click: the settlement "
            "controller owns the reward chain because it proves every screen it advances."
        )
    for attempt in range(1, attempts + 1):
        # Re-locate on every attempt: the badge is rotating between frames.
        point = _visual_home_port_point(game_session, event_sink=event_sink)
        _print_event("ui_action_precondition", action="click_home_port", attempt=attempt,
                     force_click=force_click, point=point.label)
        _recovery_emit(event_sink, "home_port_recovery_click_attempted",
                       gating=True, action="click_home_port", attempt=attempt,
                       point=point.label)
        game_session.input.click(point)
        _print_event("ui_click_sent", point=point.label, attempt=attempt)
        _recovery_emit(event_sink, "home_port_recovery_click_sent",
                       attempt=attempt, point=point.label)
        time.sleep(2.0)
        _print_event("ui_action_wait_completed", point=point.label, attempt=attempt,
                     wait_seconds=2.0)
        try:
            _require_ui_state(game_session, UiState.HOME_PORT)
            _print_event("home_port_recovery_confirmed", attempt=attempt)
            _recovery_emit(event_sink, "home_port_recovery_confirmed", attempt=attempt)
            return
        except (UiStateGateError, LoopError) as exc:
            _print_event("home_port_recovery_retry", attempt=attempt, error=str(exc))
            _recovery_emit(
                event_sink, "home_port_recovery_retry", attempt=attempt,
                error=str(exc)[:200],
            )
    # The 母港 control is part of the UI layer it hides, so no number of retries
    # on it can restore the layer. One click on the port background does - but
    # only after the frame has been proven to be a faded port and nothing else.
    if _home_port_ui_layer_is_hidden(game_session):
        wake = RecordedPoint(600, 300, "home-port-ui-wake")
        _print_event("ui_action_precondition", action="wake_home_port_ui_layer",
                     attempt=1, force_click=False, point=wake.label)
        _recovery_emit(event_sink, "home_port_recovery_click_attempted",
                       gating=True, action="wake_home_port_ui_layer", attempt=1,
                       point=wake.label)
        game_session.input.click(wake)
        _print_event("ui_click_sent", point=wake.label, attempt=1)
        _recovery_emit(event_sink, "home_port_recovery_click_sent",
                       attempt=1, point=wake.label)
        time.sleep(1.8)
        _print_event("ui_action_wait_completed", point=wake.label, attempt=1,
                     wait_seconds=1.8)
        try:
            _require_ui_state(game_session, UiState.HOME_PORT)
        except (UiStateGateError, LoopError) as exc:
            _print_event("home_port_ui_wake_failed", error=str(exc))
            _recovery_emit(
                event_sink, "home_port_ui_wake_failed", error=str(exc)[:200],
            )
        else:
            _print_event("home_port_recovery_confirmed", attempt=attempts + 1,
                         evidence="ui_layer_woken_by_area_click")
            _recovery_emit(event_sink, "home_port_recovery_confirmed",
                           attempt=attempts + 1, evidence="ui_layer_woken_by_area_click")
            return
    # The named-page proof failed on a page it cannot name. Click and observe
    # instead - bounded, and never while a reward screen is up.
    _recover_home_port_by_observation(game_session, event_sink=event_sink)


def _recover_home_port_by_observation(
    game_session, attempts: int = 6, interval_seconds: float = 3.0,
    event_sink: object | None = None,
) -> None:
    """Click the 母港 control and look again, instead of naming the page.

    Some screens cannot be named (the fleet-selection page has no usable
    fingerprint), and a proof that never succeeds is a proof that never sends
    the click that would fix it. Each round proves the port, refuses to click
    while a reward screen is up - those advance on a click anywhere and belong
    to the settlement controller - clicks once, and looks again.

    Live 2026-11-05 the named-page recovery failed twice in a row on the sortie
    menu, and both times the client had to be walked back by hand.
    """
    for attempt in range(1, attempts + 1):
        try:
            _require_ui_state(game_session, UiState.HOME_PORT)
            _print_event("home_port_recovery_confirmed", attempt=attempt,
                         evidence="observed_after_return_click")
            _recovery_emit(event_sink, "home_port_recovery_confirmed",
                           attempt=attempt, evidence="observed_after_return_click")
            return
        except (UiStateGateError, LoopError):
            pass
        reward_screen = _reward_screen_on_any_layer(game_session)
        if reward_screen is not None:
            raise LoopError(
                f"The account is showing {reward_screen}, a reward screen that advances "
                "on a click anywhere. The observed recovery will not send that click: "
                "the settlement controller owns the reward chain."
            )
        if attempt == 1:
            # Let the client settle before the first click: waiting changes
            # nothing, while a click on an unnamed page changes a screen we do
            # not understand.
            _print_event("home_port_recovery_settle_wait", attempt=attempt,
                         wait_seconds=interval_seconds, clicks_sent=0)
            _recovery_emit(event_sink, "home_port_recovery_settle_wait",
                           attempt=attempt, wait_seconds=interval_seconds, clicks_sent=0)
            time.sleep(interval_seconds)
            continue
        point = _visual_home_port_point(game_session, event_sink=event_sink)
        _print_event("ui_action_precondition", action="click_home_port_observed",
                     attempt=attempt, force_click=False, point=point.label)
        _recovery_emit(event_sink, "home_port_recovery_click_attempted",
                       gating=True, action="click_home_port_observed", attempt=attempt,
                       point=point.label)
        game_session.input.click(point)
        _print_event("ui_click_sent", point=point.label, attempt=attempt)
        _recovery_emit(event_sink, "home_port_recovery_click_sent",
                       attempt=attempt, point=point.label)
        time.sleep(interval_seconds)
        _print_event("ui_action_wait_completed", point=point.label,
                     attempt=attempt, wait_seconds=interval_seconds)
    _recovery_emit(event_sink, "home_port_recovery_exhausted", attempts=attempts)
    raise LoopError(
        f"Home Port was not reached after {attempts} observed return attempts; "
        "refusing to keep clicking"
    )


def _wait_for_return(
    reader_transport,
    expected_start_ms: int,
    poll_seconds: float,
    timeout_seconds: float,
    grace_delay_seconds: float = 10.0,
) -> PoiFleetMissionState:
    deadline = time.monotonic() + timeout_seconds
    last_log = 0.0
    grace_applied = False
    while time.monotonic() < deadline:
        state = _fleet(read_poi_fleet_states(reader_transport))
        if state.mission_status == 2 and state.mission_id == MISSION_ID and state.final_time_ms == expected_start_ms:
            _print_event("same_mission_returned", state)
            return state
        if state.mission_status != 1 or state.mission_id != MISSION_ID or state.final_time_ms != expected_start_ms:
            raise LoopError(f"Fleet 2 left the expected active mission unexpectedly: {state.mission}")
        if int(time.time() * 1000) >= expected_start_ms:
            if not grace_applied:
                _print_event("completion_grace_delay_started", state, grace_delay_seconds=grace_delay_seconds)
                time.sleep(grace_delay_seconds)
                grace_applied = True
                continue
            _print_event("completion_grace_elapsed_refresh_required", state)
            return state
        now = time.monotonic()
        if now - last_log >= 30:
            _print_event("waiting_for_return", state)
            last_log = now
        time.sleep(poll_seconds)
    raise LoopError("Timed out waiting for the same mission to return; no input was sent after timeout")

def _wait_for_idle(reader_transport, poll_seconds: float = 1.0, timeout_seconds: float = 10.0) -> PoiFleetMissionState:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        state = _fleet(read_poi_fleet_states(reader_transport))
        if _idle(state):
            _print_event("fleet_idle_confirmed_after_bonus_advance", state)
            return state
        if state.mission_status != 2 or state.mission_id != MISSION_ID:
            raise LoopError(f"Unexpected fleet 2 state after reward confirmation: {state.mission}")
        time.sleep(poll_seconds)
    raise LoopError("Fleet 2 did not become idle after bonus advance; refusing further UI actions")


def _advance_returned_expedition_screens(game_session) -> None:
    """Advance the verified result and bonus screens using bounded UI actions."""
    _print_event("ui_action_precondition", action="collect_expedition_reward",
                 fleet_id=FLEET_ID, mission_id=MISSION_ID, expected_state="RETURN_RESULT")
    game_session.input.click(RETURN_CONFIRM)
    _print_event("ui_click_sent", point=RETURN_CONFIRM.label)
    time.sleep(1.5)
    _print_event("ui_action_wait_completed", point=RETURN_CONFIRM.label, wait_seconds=1.5)

    _require_ui_state(game_session, UiState.BONUS_ITEMS)
    _print_event("ui_action_precondition", action="advance_bonus_items", expected_state="BONUS_ITEMS")
    game_session.input.click(RETURN_CONFIRM)
    _print_event("ui_click_sent", point="bonus-next")
    time.sleep(1.0)
    _print_event("ui_action_wait_completed", point="bonus-next", wait_seconds=1.0)


def _verify_returned_expedition_ready(
    reader_transport, expected_final_time_ms: int
) -> PoiFleetMissionState:
    final_state = _fleet(read_poi_fleet_states(reader_transport))
    if not _idle(final_state):
        raise LoopError(f"Fleet 2 did not return to idle after collection: {final_state.mission}")
    _print_event("ONE_EXPEDITION_LOOP_VERIFIED_READY", final_state,
                 dispatch_final_time_ms=expected_final_time_ms)
    return final_state


def _collect_returned_expedition_reward(
    game_session, reader_transport, expected_final_time_ms: int
) -> PoiFleetMissionState:
    """Compatibility wrapper for the complete reward flow."""
    _advance_returned_expedition_screens(game_session)
    _wait_for_idle(reader_transport)
    _recover_home_port(game_session)
    return _verify_returned_expedition_ready(reader_transport, expected_final_time_ms)


def build_checkpoint_verifier(reader_transport, game_session, expected_start_ms: int):
    """Build the per-step verifier used by the one-shot dispatch sequence."""

    def verify(step: int, phase: str, expected_state: str) -> None:
        if expected_state == "EXPEDITION_ACTIVE":
            state = _fleet(read_poi_fleet_states(reader_transport))
            if (
                state.mission_status != 1
                or state.mission_id != MISSION_ID
                or state.final_time_ms <= expected_start_ms
            ):
                raise LoopError(
                    f"Expected the same newly started expedition; observed {state.mission}"
                )
            _print_event(
                "dispatch_checkpoint_confirmed",
                step=step,
                phase=phase,
                expected_state=expected_state,
                evidence="poi_redux_same_mission_new_final_time",
            )
            return

        try:
            expected = UiState(expected_state)
        except ValueError as exc:
            raise LoopError(f"Unknown UI checkpoint state: {expected_state}") from exc
        if expected is UiState.UNKNOWN:
            raise LoopError("UNKNOWN is not an acceptable dispatch checkpoint")
        evidence = _require_ui_state(game_session, expected)
        _print_event(
            "dispatch_checkpoint_confirmed",
            step=step,
            phase=phase,
            expected_state=expected_state,
            evidence=evidence,
        )

    return verify


def _dispatch(
    reader_transport,
    game_input,
    expected_start_ms: int,
    *,
    checkpoint_verifier: Callable[[int, str, str], None] | None = None,
) -> PoiFleetMissionState:
    """Dispatch only when a verifier can confirm every UI transition.

    The callback receives (step_number, phase, expected_state). A failed check
    raises and prevents every subsequent click. Pre-dispatch Ooi states require
    two consecutive high-confidence in-memory fingerprints. Production remains
    disabled until RETURN_RESULT and BONUS_ITEMS are independently verified.
    """
    if checkpoint_verifier is None:
        _print_event(
            "dispatch_blocked",
            reason="stepwise_ui_checkpoint_verifier_unavailable",
            clicks_sent=0,
        )
        raise LoopError(
            "Dispatch blocked before any click: a verified per-step UI checkpoint "
            "provider is not available for all five dispatch transitions"
        )

    before = _fleet(read_poi_fleet_states(reader_transport))
    if not _idle(before):
        raise LoopError(f"Fleet 2 is not idle before dispatch: {before.mission}")

    for step, (point, settle, expected_before, expected_after) in enumerate(
        DISPATCH_STEPS, start=1
    ):
        checkpoint_verifier(step, "before", expected_before)
        if point == EXPEDITION_START:
            # Recheck the target immediately before the irreversible start click.
            # This Redux read is not freshness proof; the independent visual
            # checkpoint remains mandatory.
            pre_start = _fleet(read_poi_fleet_states(reader_transport))
            if not _idle(pre_start) or pre_start.ship_ids != before.ship_ids:
                raise LoopError("Fleet 2 changed before expedition start; refusing to click")
            expected_supply_ids = {ship_id for ship_id in pre_start.ship_ids if ship_id > 0}
            actual_supply_ids = {item.ship_id for item in pre_start.supply}
            if not expected_supply_ids or actual_supply_ids != expected_supply_ids:
                raise LoopError("Fleet 2 supply snapshot is missing or incomplete; refusing to click")
            unsupplied = [item.ship_id for item in pre_start.supply if not item.is_fully_supplied]
            if unsupplied:
                raise LoopError(
                    f"Fleet 2 has {len(unsupplied)} ship(s) not fully supplied; refusing to click"
                )
            _print_event("fleet_supply_verified", pre_start, fully_supplied_ship_count=len(pre_start.supply))
        _print_event(
            "ui_action_precondition",
            action="dispatch_menu_click",
            step=step,
            point=point.label,
            expected_ui_state=expected_before,
            fleet_idle=True,
        )
        game_input.click(point)
        _print_event("ui_click_sent", point=point.label, step=step)
        time.sleep(settle)
        _print_event(
            "ui_action_wait_completed", point=point.label, step=step,
            wait_seconds=settle,
        )
        if expected_after != "EXPEDITION_ACTIVE":
            checkpoint_verifier(step, "after", expected_after)

    deadline = time.monotonic() + 12.0
    while time.monotonic() < deadline:
        state = _fleet(read_poi_fleet_states(reader_transport))
        if state.mission_status == 1 and state.mission_id == MISSION_ID and state.final_time_ms > expected_start_ms:
            _print_event("dispatch_verified", state)
            return state
        if not _idle(state) and not (state.mission_status == 1 and state.mission_id == MISSION_ID):
            raise LoopError(f"Unexpected state after re-dispatch clicks: {state.mission}")
        time.sleep(1.0)
    raise LoopError("Dispatch was not verified from Poi Redux state; do not repeat clicks")


def _return_home_port_after_dispatch(
    game_session, reader_transport, dispatched: PoiFleetMissionState
) -> PoiFleetMissionState:
    """Return from the expedition list to Home Port and revalidate the active mission."""
    _recover_home_port(game_session)
    observed = _fleet(read_poi_fleet_states(reader_transport))
    if (
        observed.mission_status != 1
        or observed.mission_id != dispatched.mission_id
        or observed.final_time_ms != dispatched.final_time_ms
        or observed.ship_ids != dispatched.ship_ids
    ):
        raise LoopError(
            "Fleet mission changed while returning to Home Port; "
            f"expected {dispatched.mission}, observed {observed.mission}"
        )
    _print_event(
        "dispatch_home_port_verified",
        observed,
        evidence="two_consecutive_in_memory_ooi_fingerprints_and_poi_active_tuple",
    )
    return observed


def _validate_dispatch_fleet_id(target_fleet_id: int) -> None:
    """Refuse fleet ids POLICY does not allow, before any game connection.

    This is the static permission gate only. Fleet 4 is now a dispatchable
    fleet in principle; whether it exists on THIS account is a separate fact
    checked by ``_probe_unlocked_fleet_ids`` before any click.
    """
    if target_fleet_id in RUNTIME_CONFIG.non_dispatchable_fleet_ids:
        raise LoopError(
            f"Fleet {target_fleet_id} is reserved and must not be dispatched on an "
            "expedition. It is marked non_dispatchable in runtime_config; promoting "
            "it to a dispatch target is a game-policy change, not a dispatch decision."
        )
    if target_fleet_id not in RUNTIME_CONFIG.dispatchable_fleet_ids:
        raise LoopError(
            f"Fleet {target_fleet_id} is outside the dispatchable fleet policy "
            f"{list(RUNTIME_CONFIG.dispatchable_fleet_ids)}. Nothing was clicked; "
            "changing this is a runtime-config change, not a dispatch decision."
        )


def _probe_unlocked_fleet_ids() -> tuple[int, ...]:
    """Read-only CDP probe: which fleets actually exist on this account.

    KanColle unlocks fleets 2/3/4 progressively. ``all_fleet_ids`` is only a
    policy ceiling, so the live Poi fleet snapshot is the sole authority on
    whether Fleet 3 or Fleet 4 can be sent at all. This opens a read session
    only: it sends no clicks and performs no navigation.
    """
    # P0 R4: the SAME task-neutral normalizer the monitor CLI parses with.
    # An ambiguous observation fails closed rather than being widened.
    from kancollebot.core.fleet_availability import normalize_unlocked_fleet_ids

    with connect_poi_session(timeout=5.0) as poi_session:
        states = tuple(read_poi_fleet_states(poi_session.transport))
    return normalize_unlocked_fleet_ids(
        tuple(state.fleet_id for state in states)
    )


def _read_poi_port_supply_snapshot() -> PoiPortSupplySnapshot:
    """Read-only: one fixed Redux port-supply snapshot over a short CDP session.

    Poi's Redux state lives on the shell ``page`` target, not on the game
    ``webview`` this entry clicks in, so the resupply data source needs its own
    ``connect_poi_session``. Opening it per read keeps no long-lived second
    connection alive across the expedition wait, and guarantees the post-action
    verification is a genuinely new observation rather than a cached one.
    """
    with connect_poi_session(timeout=5.0) as poi_session:
        return read_poi_port_supply_snapshot(poi_session.transport)


# Default expedition each dispatchable fleet runs when none is supplied.
# The map is conflict-free by construction: one expedition id per fleet, so
# the same expedition never runs on two fleets at once (a game rule the entry
# enforces before any click). Override per account with --fleet-missions.
DEFAULT_FLEET_MISSION_MAP: dict[int, int] = {2: 1, 3: 3, 4: 2}


def _parse_fleet_missions(raw: str) -> dict[int, int]:
    """Parse "2:3,3:1,4:2" into {fleet_id: mission_id}.

    Refuses any malformed token, a non-integer, or a fleet that is not in the
    policy's dispatchable set - the caller has already narrowed the fleet set.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise LoopError("--fleet-missions must be a non-empty \"fleet:mission\" map")
    parsed: dict[int, int] = {}
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if ":" not in token:
            raise LoopError(f"--fleet-missions token {token!r} is not fleet:mission")
        fleet_part, _, mission_part = token.partition(":")
        try:
            fleet_id = int(fleet_part)
            mission_id = int(mission_part)
        except ValueError as exc:
            raise LoopError(
                f"--fleet-missions token {token!r} has a non-integer fleet or mission"
            ) from exc
        if fleet_id not in RUNTIME_CONFIG.dispatchable_fleet_ids:
            raise LoopError(
                f"--fleet-missions fleet {fleet_id} is not in the dispatchable "
                f"policy {list(RUNTIME_CONFIG.dispatchable_fleet_ids)}"
            )
        if mission_id <= 0:
            raise LoopError(f"--fleet-missions mission {mission_id} must be positive")
        parsed[fleet_id] = mission_id
    if not parsed:
        raise LoopError("--fleet-missions produced no fleet:mission pairs")
    return parsed


def _refuse_duplicate_missions(mission_map: Mapping[int, int], *, context: str) -> None:
    """One expedition id may belong to at most one fleet (game rule).

    The caller passes the resolved map; a repeated mission id is refused
    BEFORE any game connection, never after a click has been sent.
    """
    seen: dict[int, int] = {}
    for fleet_id, mission_id in mission_map.items():
        if mission_id in seen:
            raise LoopError(
                f"{context}: expedition {mission_id} is assigned to both fleet "
                f"{seen[mission_id]} and fleet {fleet_id}; the same expedition "
                "cannot run on two fleets at once. Reconfigure so each fleet "
                "has a distinct expedition."
            )
        seen[mission_id] = fleet_id


def _resolve_dispatch_plan(args: argparse.Namespace) -> tuple[tuple[int, ...], dict[int, int]]:
    """Resolve which fleets to dispatch and the expedition each runs.

    ``--fleet-id`` is the legacy single-fleet override and takes precedence;
    otherwise the entry runs every dispatchable fleet concurrently. The plan
    is the single source of truth every later stage (build_runtime, the
    validation, settlement and the supervisor report) consumes, so the single-
    and multi-fleet paths cannot silently diverge.
    """
    fleet_id = getattr(args, "fleet_id", None)
    excluded_raw = getattr(args, "exclude_fleets", None)
    # The test harness builds args as a MagicMock, where an unset attribute is a
    # truthy MagicMock rather than None; only a real string means "set".
    if excluded_raw is not None and not isinstance(excluded_raw, str):
        excluded_raw = None
    if fleet_id is not None:
        if excluded_raw is not None:
            raise LoopError(
                "--exclude-fleets cannot be combined with --fleet-id "
                "(single-fleet mode)"
            )
        mission_id = getattr(args, "mission_id", None)
        if mission_id is None:
            raise LoopError("--fleet-id requires --mission-id")
        # The legacy override also honours the per-entry composition knobs.
        return (int(fleet_id),), {int(fleet_id): int(mission_id)}
    mission_id = getattr(args, "mission_id", None)
    if mission_id is not None:
        raise LoopError(
            "--mission-id without --fleet-id is ambiguous in all-fleets mode; "
            "pass --fleet-id with --mission-id, or --fleet-missions to map each "
            "fleet to its expedition"
        )
    target_fleet_ids = tuple(int(f) for f in RUNTIME_CONFIG.dispatchable_fleet_ids)
    if excluded_raw:
        try:
            excluded_ids = {
                int(tok) for tok in excluded_raw.split(",") if tok.strip()
            }
        except ValueError as exc:
            raise LoopError(
                f"--exclude-fleets must be comma-separated integers: {exc}"
            )
        unknown = excluded_ids - set(RUNTIME_CONFIG.dispatchable_fleet_ids)
        if unknown:
            raise LoopError(
                f"--exclude-fleets names non-dispatchable fleet ids "
                f"{sorted(unknown)}; only {RUNTIME_CONFIG.dispatchable_fleet_ids} "
                "may be excluded"
            )
        target_fleet_ids = tuple(
            f for f in target_fleet_ids if f not in excluded_ids
        )
        if not target_fleet_ids:
            raise LoopError(
                "--exclude-fleets removed every dispatchable fleet; refusing an "
                "empty plan"
            )
    fleet_missions = getattr(args, "fleet_missions", None)
    if fleet_missions:
        mission_map = _parse_fleet_missions(fleet_missions)
    else:
        mission_map = dict(DEFAULT_FLEET_MISSION_MAP)
    # Keep only the fleets this account is allowed to dispatch, in policy
    # order, filling any gap with the default so every target has a mission
    # before the duplicate check.
    mission_map = {
        fleet_id: mission_map.get(fleet_id, DEFAULT_FLEET_MISSION_MAP.get(fleet_id))
        for fleet_id in target_fleet_ids
    }
    missing = [fleet_id for fleet_id, mission in mission_map.items() if mission is None]
    if missing:
        raise LoopError(
            f"no expedition configured for dispatchable fleet {missing} in "
            "all-fleets mode; pass --fleet-missions to set it explicitly"
        )
    _refuse_duplicate_missions(mission_map, context="all-fleets mode")
    return target_fleet_ids, mission_map


def _witness_observer_script(args: argparse.Namespace) -> Path:
    """Resolve the passive result-observer script, ignoring MagicMock stubs."""
    raw = getattr(args, "mission_result_observer_script", None)
    if isinstance(raw, (str, Path)) and str(raw).strip():
        return Path(raw)
    return DEFAULT_MISSION_RESULT_OBSERVER


def _witness_configuration(
    args: argparse.Namespace, fleet_count: int
) -> tuple[float, int]:
    """Observer timeout and the number of result exchanges to expect.

    One exchange per returning fleet is the default, so a three-fleet run does
    not silently stop listening after the first fleet's receipt. A MagicMock
    attribute (offline doubles) is never coerced into a real setting.
    """
    raw_timeout = getattr(args, "mission_result_timeout_seconds", None)
    if (
        isinstance(raw_timeout, (int, float))
        and not isinstance(raw_timeout, bool)
        and raw_timeout > 0
    ):
        timeout = float(raw_timeout)
    else:
        timeout = DEFAULT_MISSION_RESULT_TIMEOUT_SECONDS
    raw_captures = getattr(args, "witness_captures", None)
    if (
        isinstance(raw_captures, int)
        and not isinstance(raw_captures, bool)
        and raw_captures > 0
    ):
        captures = min(raw_captures, 16)
    else:
        captures = max(1, fleet_count)
    return timeout, captures


def _stateless_control_enabled(args: argparse.Namespace) -> bool:
    """Resolve the main-loop control mode from ``--stateless`` / ``--strict``.

    Stateless control is the default; ``--strict`` is the only opt-out back to
    the historical ledger-veto path and wins even if both flags are given.
    Strict identity, not truthiness: offline composition tests hand this a
    MagicMock namespace whose unknown attributes are truthy, so a mock value
    must never be mistaken for an explicit boolean decision.

    This resolves ONLY the main loop's ledger veto power (other_returns,
    UNATTRIBUTED and the audit_gate preconditions). The startup settlement
    path has its own attribution/witness gates and is not consulted here.
    """
    if getattr(args, "strict", False) is True:
        return False
    return getattr(args, "stateless", True) is not False


def _collect_fleet_timers(runtime, supervisor, target_fleet_ids) -> dict[int, int]:
    """Poi-authored expedition deadlines for fleets that are still out.

    ``scheduler.fleet(fleet_id).mission.final_time_ms`` is the same
    ``api_mission`` completion time that Poi confirmed at dispatch; reading it
    costs no probe.  Fleets whose phase is no longer ACTIVE are excluded: their
    outcome is pending work, not a timer to wait for.
    """
    from kancollebot.expeditions.continuous_scheduler import SchedulerInvariantError

    timers: dict[int, int] = {}
    for fleet_id in target_fleet_ids:
        try:
            state = runtime.scheduler.fleet(fleet_id)
        except SchedulerInvariantError:
            # Not configured for dispatch in this run: it has no timer to wait for.
            continue
        phase = getattr(getattr(state, "phase", None), "value", "")
        mission = getattr(state, "mission", None)
        final_ms = getattr(mission, "final_time_ms", None)
        if (
            str(phase).upper() in supervisor.ACTIVE_PHASES
            and isinstance(final_ms, int)
            and not isinstance(final_ms, bool)
            and final_ms > 0
        ):
            timers[int(fleet_id)] = final_ms
    return timers


def _pending_timer_work_fleet_ids(supervisor) -> tuple[int, ...]:
    """Fleets with executable work right now; a timer must never sleep past them."""
    return tuple(
        int(action.fleet_id)
        for action in supervisor.pending_actions()
        if isinstance(action.fleet_id, int) and not isinstance(action.fleet_id, bool)
    )


def _fleet_decision_summary(result) -> list[dict[str, object]]:
    """Per-fleet monitor verdicts, so a pause label is never the only reason.

    ``fleets_2_not_ready`` says a fleet could not be dispatched; it does not say
    *why*. The monitor already answered that (``plan.reason`` from the planner),
    so the pause row carries it instead of discarding it. A test double without
    a ``fleets`` attribute yields an empty list rather than an exception.
    """
    fleets = getattr(getattr(result, "monitor_result", None), "fleets", None)
    if not isinstance(fleets, (list, tuple)):
        return []
    rows: list[dict[str, object]] = []
    for item in fleets:
        previous = getattr(item, "previous_phase", None)
        current = getattr(item, "current_phase", None)
        rows.append(
            {
                "fleet_id": getattr(item, "fleet_id", None),
                "previous_phase": getattr(previous, "value", previous),
                "current_phase": getattr(current, "value", current),
                "action": getattr(item, "action", None),
                "reason": getattr(item, "reason", None),
            }
        )
    return rows


def _pending_settlement_fleet_ids() -> tuple[int, ...]:
    """Read-only Poi probe: which fleets still owe a settlement.

    ``api_mission[0] == 2`` is the returned-and-uncollected state. The drain uses
    this before every settlement input: once it is empty, the account owes
    nothing more and only the client is catching up, so clicking again would
    claim a result the server no longer reports as pending.

    A read failure propagates: guessing "no pending settlement" from a failed
    read would be exactly the assumption this guard exists to prevent.
    """
    with connect_poi_session(timeout=5.0) as poi_session:
        states = tuple(read_poi_fleet_states(poi_session.transport))
    return tuple(
        state.fleet_id for state in states if state.mission_status == 2
    )


def _refresh_observation_budget(args: argparse.Namespace) -> PageObservationBudget:
    """Read the refresh observation budget, tolerating a non-numeric fixture.

    Test namespaces carry ``MagicMock`` attributes; ``float()`` on one raises.
    A non-numeric value therefore falls back to the documented default instead
    of turning into an unrelated crash before any UI work starts.
    """

    def _number(name: str, default, cast):
        value = getattr(args, name, default)
        if isinstance(value, bool):
            return default
        try:
            return cast(value)
        except (TypeError, ValueError):
            return default

    total = _number("refresh_observation_seconds", DEFAULT_TOTAL_BUDGET_SECONDS, float)
    limit = _number(
        "refresh_changed_unknown_limit", DEFAULT_CHANGED_UNKNOWN_LIMIT, int
    )
    if total <= 0:
        total = DEFAULT_TOTAL_BUDGET_SECONDS
    if limit <= 0:
        limit = DEFAULT_CHANGED_UNKNOWN_LIMIT
    return PageObservationBudget(total_seconds=total, changed_unknown_limit=limit)


def _early_return_settings(args: argparse.Namespace) -> tuple[bool, float, int]:
    """Resolve the early-return knobs once, tolerating a mocked namespace.

    Disabled unless ``--early-return`` was passed explicitly. The mechanic is
    documented by two wikis but has never been exercised in this client, and it
    is the only feature here that navigates *away* from the home port while the
    loop is merely waiting - so the default run must not acquire that ability
    by accident.
    """
    enabled = getattr(args, "early_return", False) is True

    def _number(name: str, default, cast, *, positive: bool):
        value = getattr(args, name, default)
        # A mocked namespace answers with a MagicMock, which ``float()`` happily
        # converts. Only a real number or a real string may become a knob.
        if not isinstance(value, (int, float, str)) or isinstance(value, bool):
            return default
        try:
            number = cast(value)
        except (TypeError, ValueError):
            return default
        if positive and not (number > 0):
            return default
        if not positive and number < 0:
            return default
        return number

    lookahead = _number(
        "early_return_lookahead_seconds", DEFAULT_EARLY_RETURN_LOOKAHEAD_SECONDS,
        float, positive=True,
    )
    attempts = _number("early_return_max_attempts", 2, int, positive=False)
    return bool(enabled), float(lookahead), int(attempts)


def _read_early_return_observation(fleet_id: int) -> EarlyReturnObservation:
    """One read-only Poi probe for the early-return precondition.

    ``fresh`` means "this read succeeded at decision time" - it is not a claim
    that Poi's cached Redux state already reflects the server. A failed read is
    reported as ``fresh=False`` instead of raising: the caller must stop
    judging, and stopping is not an error the whole loop should die of. The next
    tick's supervisor read remains the authority on the fleet's phase.
    """
    try:
        with connect_poi_session(timeout=5.0) as poi_session:
            states = tuple(read_poi_fleet_states(poi_session.transport))
    except Exception as exc:  # noqa: BLE001 - any read failure means "not fresh"
        return EarlyReturnObservation(
            mission_status=None,
            fresh=False,
            detail=f"{type(exc).__name__}: {exc}",
        )
    for state in states:
        if state.fleet_id == fleet_id:
            return EarlyReturnObservation(
                mission_status=state.mission_status, fresh=True
            )
    return EarlyReturnObservation(
        mission_status=None,
        fresh=False,
        detail=f"fleet {fleet_id} is absent from the Poi fleet snapshot",
    )


def _named_page(ui_driver) -> str | None:
    """The page the client shows right now, without any side effect.

    ``probe_drain_state`` is one frame ranked against every calibrated state.
    ``None`` means the capability is not installed and ``UNKNOWN`` means the
    frame could not be named; neither is a page, so neither may be compared to
    a page name by the caller.
    """
    try:
        named = ui_driver.probe_drain_state()
    except Exception:  # noqa: BLE001 - a failed probe is "no answer", not a page
        return None
    if not isinstance(named, str) or named == "UNKNOWN":
        return None
    return named


def _build_early_return(
    args: argparse.Namespace,
    *,
    ui_driver,
    event_sink,
    fleet_id: int,
    capture_countdown_frame,
) -> tuple[ExpeditionEarlyReturn | None, dict[str, object]]:
    """Compose the coordinator, or explain why the loop will not try it.

    Two gates, both hard. ``--early-return`` opts in. A calibrated countdown
    template is the second: without it every attempt would drive the client to
    the expedition list and back for a reading that can never be produced, so
    the feature is reported unavailable instead of switching screens for
    nothing.
    """
    enabled, lookahead, attempts = _early_return_settings(args)
    if not enabled:
        return None, {
            "reason": "not_requested",
            "lookahead_seconds": lookahead,
            "ui_switches_at_risk": 0,
        }
    template_path = getattr(args, "countdown_template", None)
    if isinstance(template_path, Path):
        path = template_path
    elif isinstance(template_path, str) and template_path:
        path = Path(template_path)
    else:
        # A MagicMock namespace carries no meaningful path; the documented
        # default is the only honest reading of "nothing was configured".
        path = Path(DEFAULT_COUNTDOWN_TEMPLATE_PATH)
    try:
        templates = CountdownTemplates.load(path)
    except CountdownTemplateError as exc:
        return None, {
            "reason": "countdown_templates_unavailable",
            "detail": str(exc)[:200],
            "lookahead_seconds": lookahead,
            "ui_switches_at_risk": 0,
            "next_step": "record_the_displayed_countdown_glyphs_on_a_live_client",
        }
    reader = CountdownReader(capture=capture_countdown_frame, templates=templates)

    def read_countdown():
        text = reader.read_text()
        if text is None:
            return None
        return read_remaining_from_text(text)

    coordinator = ExpeditionEarlyReturn(
        fleet_id=fleet_id,
        read_poi=lambda: _read_early_return_observation(fleet_id),
        open_expedition_list=ui_driver.open_expedition_list,
        read_countdown=read_countdown,
        current_page=lambda: _named_page(ui_driver),
        go_home_port=lambda: ui_driver.return_to_home_port_by_observation(
            fleet_id=fleet_id
        ),
        leave_home_port=ui_driver.leave_home_port,
        event_sink=event_sink,
        threshold_seconds=DEFAULT_EARLY_RETURN_THRESHOLD_SECONDS,
    )
    return coordinator, {
        "reason": "enabled",
        "lookahead_seconds": lookahead,
        "max_attempts": attempts,
        "threshold_seconds": DEFAULT_EARLY_RETURN_THRESHOLD_SECONDS,
        "displayed_time_is_the_authority": True,
        "abort_or_recall_clicked": False,
    }


def _nearest_deadline_fleet(
    timers: Mapping[int, int], fleet_ids, *, now_epoch_ms: int
) -> tuple[int, float] | None:
    """The fleet closest to its deadline, and how long that is, in seconds.

    A local deadline decides only *when it is worth looking* at the displayed
    countdown - never whether the mechanic applies. That is exactly the
    division the Japanese wiki demands, because a local clock drifts.
    """
    best: tuple[int, float] | None = None
    for fleet_id in fleet_ids:
        final_ms = timers.get(int(fleet_id))
        if not isinstance(final_ms, int) or isinstance(final_ms, bool):
            continue
        remaining = (final_ms - now_epoch_ms) / 1000.0
        if best is None or remaining < best[1]:
            best = (int(fleet_id), remaining)
    return best


def _maybe_attempt_early_return(
    *,
    coordinator: ExpeditionEarlyReturn | None,
    settings: Mapping[str, object],
    timers: Mapping[int, int],
    fleet_ids,
    ui_arbiter,
    event_sink,
    tick_index: int,
    tick_budget: int,
    attempts_used: dict[int, int],
) -> tuple[EarlyReturnOutcome | None, float | None]:
    """One bounded early-return attempt when a deadline is close.

    Returns the outcome and an optional cap for the following timer sleep. The
    cap exists because the honest answer "the display still says more than a
    minute" must not be followed by a sleep that runs past the moment the
    display drops below it - the display, not the local clock, decides.
    """
    if coordinator is None:
        return None, None
    lookahead = settings.get("lookahead_seconds")
    if not isinstance(lookahead, float) or not lookahead > 0:
        return None, None
    limit = settings.get("max_attempts")
    limit = limit if isinstance(limit, int) and not isinstance(limit, bool) else 2

    nearest = _nearest_deadline_fleet(
        timers, fleet_ids, now_epoch_ms=int(time.time() * 1000)
    )
    if nearest is None:
        return None, None
    fleet_id, remaining = nearest
    if remaining > lookahead:
        # Nothing to look at yet: no screenshot, no navigation, no Poi probe.
        return None, None
    used = attempts_used.get(fleet_id, 0)
    if used >= limit:
        event_sink("expedition_early_return_skipped", {
            "fleet_id": fleet_id,
            "reason": "attempt_budget_spent",
            "attempts_used": used,
            "max_attempts": limit,
            "local_remaining_seconds": round(remaining, 1),
            "ui_switches_sent": 0,
        })
        return None, None

    attempts_used[fleet_id] = used + 1
    event_sink("expedition_early_return_attempting", {
        "fleet_id": fleet_id,
        "attempt": used + 1,
        "max_attempts": limit,
        "local_remaining_seconds": round(remaining, 1),
        "lookahead_seconds": lookahead,
        "displayed_time_is_the_authority": True,
        "tick": tick_index,
        "of": tick_budget,
    })
    # The attempt navigates and clicks, so it runs under the same cross-process
    # UI mutex every controller uses; two processes must never drive the same
    # client into two different screens.
    try:
        with ui_arbiter.operation("expedition_early_return"):
            outcome = coordinator.attempt()
    except Exception as exc:  # noqa: BLE001 - a refused navigation is not success
        event_sink("expedition_early_return_failed", {
            "fleet_id": fleet_id,
            "error_type": type(exc).__name__,
            "detail": str(exc)[:300],
            "succeeded": False,
            "next_step": "resume_the_normal_timer_wait_without_retrying_the_switch",
        })
        # The client may be anywhere after a refused navigation. A short sleep
        # makes the next tick re-observe instead of sleeping to a deadline the
        # display just contradicted.
        return None, 0.0

    cap: float | None = None
    if outcome.succeeded:
        # Poi now reports the pending settlement, so there is executable work
        # waiting. Re-observe after the same fixed grace the loop already uses
        # post-expiry instead of sleeping to a deadline the server just passed.
        return outcome, float(DEFAULT_POST_EXPIRY_GRACE_SECONDS)
    if outcome.reason == "displayed_remaining_time_at_or_above_threshold":
        displayed = outcome.remaining_seconds
        if isinstance(displayed, int) and displayed >= DEFAULT_EARLY_RETURN_THRESHOLD_SECONDS:
            cap = float(displayed - DEFAULT_EARLY_RETURN_THRESHOLD_SECONDS) + 2.0
    elif outcome.reason in (
        "displayed_remaining_time_unreadable",
        "client_still_reports_the_fleet_on_expedition",
    ):
        cap = 15.0
    return outcome, cap


def _timer_wait_settings(args: argparse.Namespace) -> tuple[bool, float, float | None, float]:
    """Resolve the timer-wait knobs once, tolerating a mocked namespace.

    ``run()`` validates the real CLI values; this keeps a MagicMock namespace
    (the convention in the loop tests) from turning a mock into a sleep budget.
    """
    enabled = getattr(args, "timer_sleep", True) is not False
    grace = getattr(args, "post_expiry_grace_seconds", DEFAULT_POST_EXPIRY_GRACE_SECONDS)
    if (
        not isinstance(grace, (int, float))
        or isinstance(grace, bool)
        or not math.isfinite(grace)
        or grace < 0
    ):
        grace = DEFAULT_POST_EXPIRY_GRACE_SECONDS
    cap = getattr(args, "max_timer_sleep_seconds", None)
    if (
        not isinstance(cap, (int, float))
        or isinstance(cap, bool)
        or not math.isfinite(cap)
        or cap <= 0
    ):
        cap = None
    interval = getattr(args, "tick_interval_seconds", 10.0)
    if (
        not isinstance(interval, (int, float))
        or isinstance(interval, bool)
        or not math.isfinite(interval)
        or interval <= 0
    ):
        interval = 10.0
    return enabled, float(grace), (float(cap) if cap is not None else None), float(interval)


def _wait_before_next_observation(
    args: argparse.Namespace,
    sleeper: ExpeditionTimerSleeper,
    *,
    runtime,
    supervisor,
    target_fleet_ids,
    tick_index: int,
    tick_budget: int,
    wake_reason: str,
    sleep_cap_seconds: float | None = None,
) -> str:
    """Block until the next fleet deadline instead of polling the game.

    Requirement A: while a target fleet's timer has not expired and nothing else
    is executable, this is ONE blocking sleep straight to ``final_time_ms`` plus
    the fixed post-expiry grace.  No Poi read, no CDP command, no screenshot and
    no frame classification runs during it; the sleeper snapshots the probe
    counters and raises if any of them moved.

    Three bounded fall-backs keep the loop honest without reintroducing a poll:
    no active timer (nothing to wait for), an already-expired timer (only the
    3 s grace applies), and ``--no-timer-sleep`` (the historical interval).

    ``sleep_cap_seconds`` shortens - never lengthens - that sleep. The early
    return passes it after the expedition screen said "still more than a
    minute": sleeping to the local deadline would then walk straight past the
    moment the *displayed* time drops below the threshold the wiki describes.
    A zero cap skips the sleep entirely so the next tick re-observes.
    """
    enabled, grace_seconds, max_sleep_seconds, interval = _timer_wait_settings(args)
    if isinstance(sleep_cap_seconds, float) and math.isfinite(sleep_cap_seconds):
        if sleep_cap_seconds < 0:
            sleep_cap_seconds = 0.0
        if max_sleep_seconds is None:
            max_sleep_seconds = sleep_cap_seconds
        else:
            max_sleep_seconds = min(max_sleep_seconds, sleep_cap_seconds)
        if sleep_cap_seconds <= 0:
            _print_event(
                "expedition_wait_reobserve_immediately",
                tick=tick_index,
                of=tick_budget,
                wake_reason=wake_reason,
                sleep_cap_seconds=0.0,
                probes_during_wait=False,
            )
            return "reobserve_immediately"
    plan = plan_timer_sleep(
        timers=_collect_fleet_timers(runtime, supervisor, target_fleet_ids),
        now_epoch_ms=int(time.time() * 1000),
        grace_seconds=grace_seconds,
        pending_fleet_ids=_pending_timer_work_fleet_ids(supervisor),
        enabled=enabled,
        max_sleep_seconds=max_sleep_seconds,
    )
    if plan.should_sleep:
        sleeper.wait(plan)
        return "timer_sleep"
    if plan.timers and plan.reason == "timer_expired_refresh_may_start":
        # The deadline has already passed: the contract asks for the same fixed
        # grace before the visible 出击 -> 母港 refresh, nothing longer.
        _print_event(
            "expedition_timer_expired_grace",
            tick=tick_index,
            of=tick_budget,
            fleet_timer_count=len(plan.timers),
            grace_seconds=plan.grace_seconds,
            next_step="visible_sortie_home_refresh_then_fresh_poi_read",
            probes_during_wait=False,
        )
        time.sleep(plan.grace_seconds)
        return "post_expiry_grace"
    _print_event(
        "expedition_wait_without_timer",
        tick=tick_index,
        of=tick_budget,
        reason=plan.reason,
        wake_reason=wake_reason,
        pending_fleet_ids=list(plan.pending_fleet_ids),
        fleet_timer_count=len(plan.timers),
        next_wake_seconds=interval,
    )
    time.sleep(interval)
    return "interval_fallback"


def _run_production_supervisor_once(args: argparse.Namespace) -> int:
    """Compose the durable monitor/controller/supervisor with the visible CDP driver."""
    from datetime import timedelta

    from kancollebot.adapters.poi.continuous_expedition_ui import (
        CdpContinuousExpeditionUiDriver,
    )
    from kancollebot.expeditions.continuous_dispatch_controller import (
        ContinuousDispatchController,
        UiStateEvidence,
    )
    from kancollebot.expeditions.continuous_monitor import ContinuousMonitorError
    from kancollebot.expeditions.continuous_resupply_controller import (
        ContinuousResupplyController,
    )
    from kancollebot.expeditions.continuous_scheduler import (
        FleetPhase,
        UiOperationArbiter,
    )
    from kancollebot.expeditions.continuous_settlement_controller import (
        ContinuousSettlementController,
    )
    from kancollebot.expeditions.continuous_supervisor import (
        ContinuousExpeditionSupervisor,
    )
    from kancollebot.expeditions.page_intent import PageEvidence
    from kancollebot.expeditions.startup_settlement import StartupSettlementRefused
    from kancollebot.expeditions.port_supply_provider import (
        PoiReduxFleetSupplyProvider,
    )
    from kancollebot.expeditions.dispatch_recovery import (
        DurableDispatchCoordinator,
        SqliteDispatchIntentStore,
    )
    from kancollebot.expeditions.dispatch_registry import SqliteDispatchRegistry
    from kancollebot.expeditions.fleet_availability import (
        FleetAvailabilityError,
        resolve_fleet_availability,
    )
    from kancollebot.expeditions.expedition_requirement_checker import (
        ExpeditionRequirementChecker,
    )
    from kancollebot.task_scope import TaskKind
    from run_continuous_expedition_monitor import build_runtime

    runtime = None
    witness_provider = None
    # Restored in finally so a cancelled timer sleep never leaves a modified
    # signal disposition behind for the next in-process run.
    _previous_signal_handlers: dict[object, object] = {}
    # P0 Step 1: stateless control is now the default; --strict restores the
    # old ledger-veto semantics. The resolver keeps strict identity so a
    # MagicMock namespace can never silently flip the control mode.
    stateless = _stateless_control_enabled(args)
    try:
        target_fleet_ids, mission_map = _resolve_dispatch_plan(args)
        if stateless:
            _print_event(
                "stateless_mode_enabled",
                ledger_authority="audit_only",
                reward_receipt_gate="disabled",
                visual_click_gate="unchanged",
                game_api_requests=False,
                game_input_sent=False,
            )
        target_fleet_id = target_fleet_ids[0]
        target_mission_id = mission_map[target_fleet_id]
        fleet_label = (
            target_fleet_id
            if len(target_fleet_ids) == 1
            else "-".join(str(f) for f in target_fleet_ids)
        )
        multi_fleet = args.fleet_id is None
        drain_requested = getattr(args, "global_drain", False) is True
        # Multi-fleet mode dispatches several fleets at once, so two of them
        # can return together. The per-fleet settlement barrier cannot absorb
        # that (it raises and stops the run), so the global drain is mandatory
        # here; the guard fails closed before any game connection.
        if multi_fleet and not drain_requested:
            raise LoopError(
                "all-fleets mode dispatches multiple fleets concurrently; "
                "simultaneous returns require --global-drain, which is not set. "
                "Pass --global-drain, or use --fleet-id for a single fleet."
            )
        for fleet_id, mission_id in mission_map.items():
            expedition_id = DEFAULT_CATALOG.number_of_mission_id(mission_id)
            if expedition_id is None:
                raise LoopError(
                    f"Mission id {mission_id} (fleet {fleet_id}) is not in the "
                    "master-data table; refusing to guess which row it is"
                )
        for fleet_id in target_fleet_ids:
            _validate_dispatch_fleet_id(fleet_id)
        # Account fact check, before the game webview and before any click.
        # An account without Fleet 3/4 must fail closed here rather than have
        # the monitor demand an observation for a fleet that does not exist.
        unlocked_fleet_ids = _probe_unlocked_fleet_ids()
        try:
            resolve_fleet_availability(
                unlocked_fleet_ids, target_fleet_ids, strict=True
            )
        except FleetAvailabilityError as exc:
            raise LoopError(
                f"Fleet availability check failed: {exc}"
            ) from exc
        _print_event(
            "account_fleet_availability_verified",
            unlocked_fleet_ids=list(unlocked_fleet_ids),
            target_fleet_ids=list(target_fleet_ids),
            source="poi-cdp-fleet-snapshot",
        )
        with connect_game_webview(timeout=3.0) as game_session:
            if args.settle_only:
                # settle-only never dispatches, but the monitor still needs the
                # configured mission identity per fleet: --adopt-observed-mission
                # compares the observed in-flight mission against the configured
                # requirement for that fleet. Passing None here made
                # build_runtime fall back to the static default assignment
                # {2: 1, 3: 3, 4: 1}, which rejected a real in-flight mission 2
                # on fleet 2 (live 2026-11-07 "observed mission 2 is not the
                # configured mission 1") and fail-closed BLOCKED instead of
                # adopting. The requirements gate nothing here that the
                # SettleOnlyDispatcher does not already refuse, so sharing the
                # dispatch-mode construction cannot widen what a settle-only
                # run may click: it clicks nothing by construction.
                requirement_map = {
                    fleet_id: _mission_requirement(args, fleet_id, mission_id)
                    for fleet_id, mission_id in mission_map.items()
                }
            else:
                if (
                    getattr(args, "minimum_ships", None) is not None
                    or getattr(args, "flagship_level", None) is not None
                ):
                    raise LoopError(
                        "--minimum-ships/--flagship-level are single-fleet "
                        "composition overrides and must not be used in all-fleets "
                        "mode; rely on the static requirement rule per mission"
                    )
                requirement_map = {
                    fleet_id: _mission_requirement(args, fleet_id, mission_id)
                    for fleet_id, mission_id in mission_map.items()
                }
            # One start-up snapshot decides the path, from the composited layer.
            # A proven reward screen is settled by the settlement controller
            # BEFORE the monitor ever asks for a freshness witness: that
            # witness needs a HOME_PORT navigation, which cannot run while a
            # reward screen is up. This is the entry-level fix for the
            # start-up deadlock (live 2026-11-05); the supervisor's tick order
            # and the monitor's freshness gate are unchanged.
            startup_screen = _probe_startup_screen(game_session)
            if startup_screen.state is not None:
                _print_event(
                    "startup_reward_screen_detected",
                    startup_screen=startup_screen.state,
                    default_surface_state=startup_screen.default_surface_state,
                    owned_by="startup settlement path",
                    recovery_clicks_sent=0,
                    ui_refresh_deferred_reason=(
                        "the freshness navigation starts at HOME_PORT and cannot run "
                        "while a reward screen is up"
                    ),
                )
            else:
                # Live 2026-11-06 02:08Z a recovery ran invisible: its rows
                # went to stdout only, so the run log could not tell whether
                # the tool or the operator brought the account home. The sink
                # is prebuilt (it must be: these gate rows are durable before
                # the clicks they name), so recovery can emit through it.
                _recover_home_port(
                    game_session, event_sink=_active_run_log_sink()
                )
                _capture_home_reference(game_session)

            def verify_state(expected: str, *, visual_attempts: int = 3) -> UiStateEvidence:
                state = UiState(expected)
                # The return notice is drawn on a composited overlay layer that
                # Page.captureScreenshot(fromSurface=True) drops: on 2026-11-05
                # the same screen scored HOME_PORT 0.9985 / notice 0.8332 on the
                # default surface and notice 0.9994 on the composited one, which
                # made a real return look unsettleable. Ask for the composited
                # surface only for the state that actually lives there.
                evidence = _require_ui_state(
                    game_session,
                    state,
                    visual_attempts=visual_attempts,
                    from_surface=state is not UiState.HOME_PORT_RETURN_NOTICE,
                )
                return UiStateEvidence(
                    state=state.value,
                    consecutive_states=(state.value, state.value),
                    evidence=evidence,
                )

            def refresh_verify_state(expected: str) -> UiStateEvidence:
                # Six visual samples are the live-verified setting for the
                # freshness navigation checkpoints (2026-11-03 archive). The
                # dispatch checkpoints keep their stricter three-sample gate.
                return verify_state(expected, visual_attempts=6)

            # One frame -> (page, fingerprint) for the bounded return refresh.
            # The page comes from the SAME single-frame all-state ranking the
            # drain probe uses (unchanged 0.97 / 0.04 thresholds); the
            # fingerprint comes from the same frame, so "did the picture move"
            # and "which page is this" always describe one capture.
            refresh_fingerprint_document = None
            try:
                refresh_fingerprint_path = Path(
                    os.environ.get(
                        "KANCOLLEBOT_FINGERPRINTS_PATH",
                        ROOT / "src" / "kancollebot" / "adapters" / "poi"
                        / "ooi_1200x720_fingerprints.json",
                    )
                )
                if refresh_fingerprint_path.is_file():
                    refresh_fingerprint_document = _read_visual_document(
                        refresh_fingerprint_path
                    )
            except (OSError, ValueError) as exc:
                _print_event(
                    "refresh_page_observer_unavailable",
                    reason=f"{type(exc).__name__}: {exc}",
                )
            if refresh_fingerprint_document is None and args.enable_ui_refresh:
                _print_event(
                    "refresh_page_observer_unavailable",
                    reason=(
                        "the calibrated fingerprint document is missing; the "
                        "return refresh keeps its fixed-attempt checkpoints"
                    ),
                )

            # The last page verdict the refresh probe produced, stamped with the
            # monotonic moment it was read. The supervisor only accepts it while
            # it is fresh (see page_intent), so evidence from before a 30-minute
            # timer sleep can never veto a dispatch.
            page_evidence: list[PageEvidence] = []

            def refresh_observe_page() -> PageSample:
                game_session.transport.send(
                    "Input.dispatchMouseEvent",
                    {"type": "mouseMoved", "x": 1195, "y": 715},
                )
                time.sleep(HOME_REFERENCE_SETTLE_SECONDS)
                payload = game_session.input.capture_screenshot(from_surface=True)
                ranking = rank_screenshot_fingerprints(
                    payload, refresh_fingerprint_document
                )
                coarse = classify_coarse_page(ranking)
                page_evidence[:] = [
                    PageEvidence(
                        page=coarse.page,
                        detail_state=coarse.detail_state,
                        observed_at=time.monotonic(),
                    )
                ]
                sample = PageSample(
                    state=ranking.state,
                    scores=ranking.scores,
                    fingerprint=screen_fingerprint(payload),
                )
                _print_event(
                    "refresh_page_observed",
                    observed=ranking.state,
                    # Coarse summary: the detailed identity above stays
                    # authoritative, and the class can only withhold an action
                    # (phase 3 veto-only evidence) - it never authorises one.
                    coarse_page=coarse.page,
                    coarse_reason=coarse.reason,
                    coarse_detail_state=coarse.detail_state,
                    best_name=ranking.best_name,
                    best_score=round(ranking.best_score, 4),
                    second_name=ranking.second_name,
                    second_score=round(ranking.second_score, 4),
                    accepted=ranking.accepted,
                    # The thresholds that ACTUALLY produced this verdict, read
                    # back from the ranking itself so the event can never drift
                    # from the classifier defaults (live 2026-11-07: the
                    # hardcoded 0.97/0.04 here outlived the threshold change).
                    minimum_score=ranking.minimum_score,
                    minimum_margin=ranking.minimum_margin,
                    frame_fingerprint_cells=len(sample.fingerprint or ()),
                    screenshot_written_to_disk=False,
                    authorizes_click=False,
                )
                return sample

            runtime = build_runtime(
                database_path=args.database,
                observer_script=args.observer_script,
                observer_timeout_seconds=args.observer_timeout_seconds,
                max_state_age_seconds=args.max_state_age_seconds,
                dispatch_fleet_ids=tuple(target_fleet_ids),
                mission_requirements=(
                    None if requirement_map is None else dict(requirement_map)
                ),
                unlocked_fleet_ids=unlocked_fleet_ids,
                fleet_availability_strict=True,
                task_kind=TaskKind.EXPEDITION,
                enable_ui_refresh=args.enable_ui_refresh,
                ui_input=game_session.input if args.enable_ui_refresh else None,
                ui_state_verifier=refresh_verify_state if args.enable_ui_refresh else None,
                ui_page_observer=(
                    refresh_observe_page
                    if args.enable_ui_refresh and refresh_fingerprint_document is not None
                    else None
                ),
                page_observation_budget=_refresh_observation_budget(args),
                refresh_verifier_error_types=(LoopError,),
                adopt_unplanned_missions=args.adopt_observed_mission,
                run_log_dir=args.run_log_dir,
                prebuilt_run_log_sink=_active_run_log_sink(),
            )

            # #134: production now owns the passive result observer itself.
            # Arm here: the durable event sink exists, and every result-screen
            # click (the startup settlement below, the supervisor's global
            # drain) is still ahead of us. It reuses the already-verified
            # observer script, the existing ``mission_result_observed`` row
            # format, the single-frame drain probe and the calibrated
            # fingerprint asset. No new template, screenshot or game request.
            if not stateless and getattr(args, "in_process_witness", False) is True:
                from kancollebot.adapters.poi.mission_result_observer import (
                    PassiveMissionResultObserver,
                )
                from kancollebot.expeditions.mission_result_witness_provider import (
                    MissionResultWitnessProvider,
                )

                observer_script = _witness_observer_script(args)
                if not observer_script.is_file():
                    raise LoopError(
                        "in-process mission-result witness is enabled but its "
                        f"passive observer script is missing: {observer_script}"
                    )
                observer_timeout, expected_captures = _witness_configuration(
                    args, len(target_fleet_ids)
                )
                witness_provider = MissionResultWitnessProvider(
                    observer=PassiveMissionResultObserver(
                        observer_script, timeout_seconds=observer_timeout
                    ),
                    # The SAME durable sink the monitor and controllers use:
                    # a witness that cannot be recorded is not evidence.
                    event_log=runtime.monitor.event_sink,
                    event_sink=runtime.monitor.event_sink,
                    expected_captures=expected_captures,
                )
                witness_provider.arm()
                _print_event(
                    "in_process_witness_wired",
                    observer_script=str(observer_script),
                    expected_captures=expected_captures,
                    timeout_seconds=observer_timeout,
                    armed_before_result_click=True,
                    game_api_requests=False,
                    game_input_sent=False,
                )

            def capture_frame() -> str:
                game_session.transport.send(
                    "Input.dispatchMouseEvent",
                    {"type": "mouseMoved", "x": 1195, "y": 715},
                )
                time.sleep(HOME_REFERENCE_SETTLE_SECONDS)
                return game_session.input.capture_screenshot()

            # P1 single-frame, all-state drain probe. ONE screenshot is scored
            # against EVERY state with the unchanged 0.97 score / 0.04 margin;
            # the result is a probe only and never authorises a click. The
            # two-consecutive-frame verifier above remains the sole click gate.
            drain_probe_document = None
            try:
                fingerprint_path = Path(
                    os.environ.get(
                        "KANCOLLEBOT_FINGERPRINTS_PATH",
                        ROOT / "src" / "kancollebot" / "adapters" / "poi"
                        / "ooi_1200x720_fingerprints.json",
                    )
                )
                if fingerprint_path.is_file():
                    drain_probe_document = _read_visual_document(fingerprint_path)
                else:
                    _print_event(
                        "single_frame_probe_unavailable",
                        reason=f"fingerprint document missing: {fingerprint_path}",
                    )
            except (OSError, ValueError) as exc:
                _print_event(
                    "single_frame_probe_unavailable",
                    reason=f"{type(exc).__name__}: {exc}",
                )

            def classify_frame(payload: str) -> tuple[str, dict[str, float]]:
                ranking = rank_screenshot_fingerprints(payload, drain_probe_document)
                _print_event(
                    "single_frame_all_state_ranking",
                    **_single_frame_ranking_fields(ranking),
                )
                return ranking.state, ranking.scores

            def capture_composited() -> str:
                # HOME_PORT_RETURN_NOTICE lives on the composited overlay that
                # the default fromSurface=True capture drops (live 2026-11-05).
                game_session.transport.send(
                    "Input.dispatchMouseEvent",
                    {"type": "mouseMoved", "x": 1195, "y": 715},
                )
                time.sleep(HOME_REFERENCE_SETTLE_SECONDS)
                return game_session.input.capture_screenshot(from_surface=False)

            scratch = Path(tempfile.mkdtemp(prefix="dispatch_row_")) / "frame.png"

            def scan_list():
                payload = capture_frame()
                scratch.write_bytes(base64.b64decode(payload))
                return scan_page(decode_png(scratch))

            list_session = ExpeditionListSession(
                scan=scan_list,
                click=lambda point, label: game_session.input.click(
                    RecordedPoint(point[0], point[1], label)
                ),
                index=load_position_index(args.position_index),
                settle_seconds=0.55,
            )

            ui_driver = CdpContinuousExpeditionUiDriver(
                game_input=game_session.input,
                verify_state=verify_state,
                event_sink=runtime.monitor.event_sink,
                details=load_detail_index(args.detail_index),
                marks=load_marks(args.marks),
                capture=capture_frame,
                # The 母港 control rotates: every return/resupply click resolves
                # its point from a fresh frame instead of a recorded coordinate.
                home_port_locator=HomePortButtonLocator(capture=capture_frame),
                classify_frame=(
                    classify_frame if drain_probe_document is not None else None
                ),
                capture_composited=(
                    capture_composited if drain_probe_document is not None else None
                ),
                locate_row=list_session.locate_any_tab,
                supported_fleets=(2, 3, 4),
                neutralize_pointer=lambda: game_session.transport.send(
                    "Input.dispatchMouseEvent",
                    {"type": "mouseMoved", "x": 1195, "y": 715},
                ),
            )

            def capture_countdown_frame() -> str:
                """One fresh frame of the expedition-screen countdown.

                Same pointer neutralisation the refresh observer uses: a resting
                cursor over the digits is a colour change in the crop, and the
                reader compares glyphs, not intent.
                """
                game_session.transport.send(
                    "Input.dispatchMouseEvent",
                    {"type": "mouseMoved", "x": 1195, "y": 715},
                )
                time.sleep(HOME_REFERENCE_SETTLE_SECONDS)
                return game_session.input.capture_screenshot(from_surface=True)

            early_return, early_return_settings = _build_early_return(
                args,
                ui_driver=ui_driver,
                event_sink=runtime.monitor.event_sink,
                fleet_id=target_fleet_id,
                capture_countdown_frame=capture_countdown_frame,
            )
            early_return_attempts: dict[int, int] = {}
            _print_event(
                "expedition_early_return_configuration",
                fleet_id=target_fleet_id,
                coordinator_ready=early_return is not None,
                game_api_requests=False,
                game_input_sent=False,
                **early_return_settings,
            )
            dispatch_coordinator = DurableDispatchCoordinator(
                runtime.scheduler,
                SqliteDispatchIntentStore(args.database),
            )
            # The durable dispatch identity ledger. It lives in the SAME
            # dispatch database as the intents (a new table, not a second
            # ledger) and is what lets the settlement half attribute a returned
            # mission to the attempt that produced it. Without it, a fleet
            # dispatched twice on the same mission with the same ships could
            # never be told apart once the intent row is deleted on confirmation.
            dispatch_registry = SqliteDispatchRegistry(
                args.database,
                task_kind=RUNTIME_CONFIG.dispatch_policy_task_kind,
            )
            # One arbiter for every visible click in this process. The refresh
            # driver, the dispatch controller, the settlement controller and the
            # resupply controller must exclude one another, or two of them could
            # interleave clicks on the same screen.
            ui_arbiter = runtime.ui_arbiter or UiOperationArbiter()
            if args.settle_only:
                dispatch_controller = SettleOnlyDispatcher(
                    scheduler=runtime.scheduler,
                    state_reader=runtime.monitor.state_reader,
                    event_sink=runtime.monitor.event_sink,
                    mission_requirements=runtime.monitor.mission_requirements,
                    ui_arbiter=ui_arbiter,
                )
            else:
                # R3-I is a LIVE dispatch gate, not an optional diagnostic:
                # the pure evaluator runs on the same fresh baseline before the
                # first click. Only PASS proceeds; REJECT/UNKNOWN raise with
                # zero UI input. Production additionally demands a cross-checked
                # rule, so a single-source (unconfirmed) rule fails closed.
                # There is deliberately no CLI or runtime bypass switch.
                requirement_checker = ExpeditionRequirementChecker(
                    minimum_confidence="cross_checked",
                )
                dispatchable_fleet_ids = tuple(RUNTIME_CONFIG.dispatchable_fleet_ids)
                _print_event(
                    "dispatch_requirement_gate_enabled",
                    rule_source=requirement_checker.rule_source,
                    rule_version=requirement_checker.rule_version,
                    minimum_confidence="cross_checked",
                    unlocked_fleet_ids=list(unlocked_fleet_ids),
                    dispatchable_fleet_ids=list(dispatchable_fleet_ids),
                    dispatch_clicks_sent=0,
                )
                dispatch_controller = ContinuousDispatchController(
                    scheduler=runtime.scheduler,
                    dispatch_coordinator=dispatch_coordinator,
                    state_reader=runtime.monitor.state_reader,
                    ui_driver=ui_driver,
                    ui_arbiter=ui_arbiter,
                    mission_requirements=runtime.monitor.mission_requirements,
                    event_sink=runtime.monitor.event_sink,
                    allow_ui_actions=True,
                    max_state_age=timedelta(seconds=args.max_state_age_seconds),
                    requirement_checker=requirement_checker,
                    unlocked_fleet_ids=unlocked_fleet_ids,
                    dispatchable_fleet_ids=dispatchable_fleet_ids,
                    # Only present when the unified run log is enabled: the gate
                    # proves the log is writable before an intent is persisted or
                    # a control is clicked. A sink without a probe yields None,
                    # which restores the historical behaviour exactly.
                    # P0 stateless: the run-log gate is an audit concern, not a
                    # precondition for an expedition the game can confirm.
                    audit_gate=(
                        None if stateless else audit_gate(runtime.monitor.event_sink)
                    ),
                    dispatch_registry=dispatch_registry,
                    dispatch_source="supervisor",
                    ledger_audit_only=stateless,
                )
            # R3: the settlement and resupply controllers are the two branches
            # supervisor.tick() could never reach while they were absent. They
            # share the SAME ui_driver and the SAME arbiter as dispatch. The
            # resupply controller keeps its own narrower safety gate: only
            # fleets in RUNTIME_CONFIG.resupply_verified_fleet_ids may be
            # clicked, so Fleet 4 still fails closed until its supply tab is
            # calibrated.
            #
            # Capability is checked, not assumed. The production driver now
            # implements the visible supply chain *and* the three reward-screen
            # transitions (2026-11-05: the return notice, result and bonus
            # screens all advance on a click anywhere, so each step is one
            # proven click rather than a located button). The check stays
            # because a driver without those methods must keep the supervisor
            # paused instead of raising AttributeError at the first returned
            # fleet - swapped drivers and future refactors are exactly when
            # this guard earns its keep.
            settlement_actions = (
                "collect_return_result",
                "claim_bonus_items",
                "claim_reward",
            )
            settlement_controller = None
            if all(callable(getattr(ui_driver, name, None)) for name in settlement_actions):
                settlement_controller = ContinuousSettlementController(
                    scheduler=runtime.scheduler,
                    state_reader=runtime.monitor.state_reader,
                    ui_driver=ui_driver,
                    ui_arbiter=ui_arbiter,
                    event_sink=runtime.monitor.event_sink,
                    allow_ui_actions=True,
                    max_state_age=timedelta(seconds=args.max_state_age_seconds),
                    dispatch_registry=dispatch_registry,
                    # P0 stateless: a settlement the game already accepted is
                    # never vetoed by an audit write. The strict-ledger path
                    # keeps the historical gate verbatim.
                    audit_gate=(
                        None if stateless else audit_gate(runtime.monitor.event_sink)
                    ),
                    ledger_audit_only=stateless,
                    # #134: the armed in-process observer is consumed at
                    # close-out. Optional: without it the controller behaves
                    # exactly as the previously verified production path.
                    witness_provider=witness_provider,
                    witness_wait_seconds=DEFAULT_WITNESS_WAIT_SECONDS,
                )
            else:
                _print_event(
                    "settlement_controller_unavailable",
                    reason=(
                        "the production UI driver has no calibrated settlement click chain; "
                        "a returned fleet stays paused instead of being clicked blind"
                    ),
                    missing_actions=[
                        name
                        for name in settlement_actions
                        if not callable(getattr(ui_driver, name, None))
                    ],
                    game_api_requests=False,
                )
            fleet_data_provider = PoiReduxFleetSupplyProvider(
                snapshot_reader=_read_poi_port_supply_snapshot,
                # The monitor's own passive observer is the only source of a
                # real api_port/port witness in this process; reusing it keeps
                # one witness stream instead of inventing a second one.
                port_witness_provider=runtime.provider,
                max_age=timedelta(seconds=args.max_state_age_seconds),
            )
            resupply_controller = ContinuousResupplyController(
                scheduler=runtime.scheduler,
                ui_driver=ui_driver,
                fleet_data_provider=fleet_data_provider,
                ui_arbiter=ui_arbiter,
                event_sink=runtime.monitor.event_sink,
                allow_ui_actions=True,
                max_state_age=timedelta(seconds=args.max_state_age_seconds),
                # A supply spends real resources; the log must be writable
                # before the click, not after it.
                audit_gate=audit_gate(runtime.monitor.event_sink),
            )
            supervisor_drain_session = None
            supervisor_drain_request = None
            # ``drain_requested`` is resolved once, before any game connection
            # (see above): ``is True`` on purpose so a test double (or any
            # Namespace-like) that carries a non-boolean attribute cannot
            # silently enable a path that sends UI inputs.
            if drain_requested and settlement_controller is not None:
                drain_capabilities = (
                    "probe_settlement_page",
                    "probe_home_port",
                    "advance_settlement_page",
                )
                missing_drain = [
                    name
                    for name in drain_capabilities
                    if not callable(getattr(ui_driver, name, None))
                ]
                if missing_drain:
                    _print_event(
                        "global_drain_unavailable",
                        reason=(
                            "the UI driver cannot name or advance a settlement page "
                            "without being told which fleet owns it; the per-fleet "
                            "settlement path stays in use"
                        ),
                        missing_methods=missing_drain,
                        game_api_requests=False,
                    )
                else:
                    from kancollebot.expeditions.settlement_drain import (
                        SettlementDrainRequest,
                        SettlementDrainSession,
                    )
                    from kancollebot.expeditions.settlement_drain_ui import (
                        PoiSettlementDrainDriver,
                        PoiSettlementDrainObserver,
                    )

                    supervisor_drain_request = SettlementDrainRequest(
                        max_actions=args.drain_max_actions,
                        timeout_seconds=args.drain_timeout_seconds,
                        action_interval_seconds=args.drain_action_interval_seconds,
                        stable_frame_count=args.drain_stable_frame_count,
                        action_observation_seconds=(
                            args.drain_action_observation_seconds
                        ),
                    )
                    # Server-first guard: once Poi reports no pending settlement,
                    # the drain may only wait for the UI. Read-only, and consulted
                    # before every settlement input rather than after it.
                    def _no_fleet_pending_settlement() -> bool:
                        return not _pending_settlement_fleet_ids()

                    supervisor_drain_session = SettlementDrainSession(
                        PoiSettlementDrainObserver(ui_driver),
                        # The drain is the one visible click path that used to
                        # run outside the shared mutex: it is wired with the
                        # SAME arbiter every controller takes, so a second
                        # process cannot interleave a click with it.
                        PoiSettlementDrainDriver(ui_driver, ui_arbiter=ui_arbiter),
                        event_sink=runtime.monitor.event_sink,
                        settlement_settled=_no_fleet_pending_settlement,
                    )
                    _print_event(
                        "global_drain_wired",
                        max_actions=supervisor_drain_request.max_actions,
                        timeout_seconds=supervisor_drain_request.timeout_seconds,
                        action_interval_seconds=(
                            supervisor_drain_request.action_interval_seconds
                        ),
                        stable_frame_count=supervisor_drain_request.stable_frame_count,
                        passes_fleet_id=False,
                        ui_arbiter_shared=True,
                        game_api_requests=False,
                    )
            elif drain_requested:
                _print_event(
                    "global_drain_unavailable",
                    reason="no settlement controller is wired, so there is nothing to drain",
                    game_api_requests=False,
                )

            supervisor_kwargs = {}
            if supervisor_drain_session is not None:
                supervisor_kwargs = {
                    "drain_session": supervisor_drain_session,
                    "drain_request": supervisor_drain_request,
                }
            supervisor = ContinuousExpeditionSupervisor(
                monitor=runtime.monitor,
                dispatch_controller=dispatch_controller,
                settlement_controller=settlement_controller,
                resupply_controller=resupply_controller,
                **supervisor_kwargs,
            )
            # All-fleets mode only works if the global drain is actually wired:
            # without it two simultaneous returns hit the per-fleet settlement
            # barrier and crash the run. Refuse clearly rather than limp on.
            if multi_fleet and supervisor.drain_session is None:
                raise LoopError(
                    "all-fleets mode needs a working global drain (--global-drain "
                    "with a settlement controller wired); without it simultaneous "
                    "returns cannot be settled and the run would crash on the "
                    "first concurrent return"
                )

            _print_event(
                "production_supervisor_started",
                dispatch_fleets=list(target_fleet_ids),
                fixed_mission_id=target_mission_id,
                unlocked_fleet_ids=list(unlocked_fleet_ids),
                read_only_fleets=list(runtime.scheduler.read_only_fleet_ids),
                deferred_fleets=list(runtime.scheduler.deferred_fleet_ids),
                fleet_availability_verified=runtime.fleet_availability_verified,
                settlement_controller_wired=supervisor.settlement_controller is not None,
                resupply_controller_wired=supervisor.resupply_controller is not None,
                global_drain_wired=supervisor.drain_session is not None,
                shared_ui_arbiter=(
                    dispatch_controller.ui_arbiter is ui_arbiter
                    and supervisor.resupply_controller.ui_arbiter is ui_arbiter
                    and (
                        supervisor.settlement_controller is None
                        or supervisor.settlement_controller.ui_arbiter is ui_arbiter
                    )
                ),
                game_api_requests=False,
                ui_refresh_enabled=args.enable_ui_refresh,
            )
            if startup_screen.state is not None:
                # Entry-level startup settlement. The gate compares a fleet's
                # observed ship-ID set with the composition recorded when it was
                # dispatched; a missing or different set refuses with zero game
                # actions. Only after that proof does the controller own the
                # chain, and it re-proves the screen read-only before the first
                # click. The monitor is not involved in this decision.
                if settlement_controller is None:
                    raise LoopError(
                        "startup reward screen detected but no settlement controller "
                        "is wired; refusing to click the chain"
                    )
                if args.fleet_id is not None:
                    # Legacy single-fleet path: exactly one fleet can own this
                    # screen, so we attribute it directly and settle it.
                    (
                        (expected_ship_ids, expected_source),
                        evidence_lookup,
                        settled_at,
                        mission_witness,
                    ) = _probe_startup_attribution(args, runtime, target_fleet_id)
                    try:
                        startup_settlement = settlement_controller.settle_visible_reward_chain(
                            fleet_id=target_fleet_id,
                            screen=startup_screen,
                            expected_ship_ids=expected_ship_ids,
                            expected_ship_ids_source=expected_source,
                            blocked_evidence=evidence_lookup.evidence,
                            blocked_evidence_rejection=evidence_lookup.rejection,
                            settlement_completed_at=settled_at,
                            mission_result_witness=mission_witness,
                        )
                    except StartupSettlementRefused as exc:
                        raise LoopError(
                            f"startup settlement refused ({exc.reason}); zero game actions sent"
                        ) from exc
                    _print_event(
                        "STARTUP_SETTLEMENT_CLOSED",
                        fleet_id=target_fleet_id,
                        start_screen=startup_settlement.start_screen,
                        steps=[state for state, _ in startup_settlement.steps],
                        ship_ids=list(startup_settlement.ship_ids),
                        mission_credited=startup_settlement.mission is not None,
                        recovered_from_blocked=startup_settlement.recovered_from_blocked,
                        recovery_reason=startup_settlement.recovery_reason,
                        scheduler_phase_after=startup_settlement.scheduler_phase_after,
                        mission_result_witness_used=(startup_settlement.witness_verdict is not None),
                        mission_result_witness_reason=(
                            startup_settlement.witness_verdict.reason
                            if startup_settlement.witness_verdict is not None
                            else None
                        ),
                        game_api_requests=False,
                    )
                else:
                    # All-fleets mode: a reward screen at start-up could belong
                    # to any of the dispatched fleets, and there may be several
                    # of them. Picking one fleet would be a guess, so the
                    # settlement is deferred to the global drain, which is
                    # mandatory in this mode and walks every settlement page in
                    # one pass during the first tick.
                    if supervisor.drain_session is None:
                        raise LoopError(
                            "a startup reward screen is up, but all-fleets mode "
                            "has no global drain wired to settle it; refusing to "
                            "guess which fleet it belongs to"
                        )
                    _print_event(
                        "STARTUP_SETTLEMENT_DEFERRED_TO_GLOBAL_DRAIN",
                        startup_screen=startup_screen.state,
                        target_fleet_ids=list(target_fleet_ids),
                        reason=(
                            "multi-fleet startup settlement is handled by the global "
                            "drain in tick 1"
                        ),
                        game_api_requests=False,
                    )
            # One explicit bootstrap refresh before the first tick.
            #
            # The monitor no longer navigates as a side effect of reading, and
            # it refuses to navigate when the durable phase says nothing is
            # pending. At start-up both halves of that rule deadlock: the phase
            # says BLOCKED or "out for hours", so nothing counts as pending,
            # so the observation can never be refreshed, so the phase can never
            # change. Live 2026-11-05 an adopted run stopped before its first
            # tick for exactly this reason. The caller that needs the state is
            # this entry point, so it says so once, by name.
            bootstrap_refresh = getattr(
                runtime.monitor.state_reader, "ensure_fresh", None
            )
            if callable(bootstrap_refresh):
                bootstrap_refresh(origin="run_bootstrap")

            def report_actions(tick_index: int, result) -> None:
                if result.settlements or result.resupplies:
                    _print_event(
                        "PRODUCTION_CLOSED_LOOP_ACTIONS",
                        tick=tick_index,
                        settlement_fleet_ids=[
                            execution.fleet_id for execution in result.settlements
                        ],
                        resupply_fleet_ids=[
                            execution.fleet_id for execution in result.resupplies
                        ],
                        resupply_already_supplied=[
                            bool(execution.already_supplied)
                            for execution in result.resupplies
                        ],
                        # #134: the loop now states which settlements carry a
                        # game-authored, dispatch-bound, complete receipt.
                        reward_receipts_verified=[
                            bool(getattr(execution, "reward_receipt_verified", False))
                            for execution in result.settlements
                        ],
                        game_api_requests=False,
                    )

            def pause(result, reason: str, tick_index: int) -> int:
                _print_event(
                    "PRODUCTION_SUPERVISOR_PAUSED",
                    reason=result.paused_reason or reason,
                    dispatch_count=len(result.dispatches),
                    game_actions_sent=0,
                    tick=tick_index,
                    # Why each fleet could not be dispatched, not just that it
                    # could not: the label alone is not a reconstructable cause.
                    fleet_decisions=_fleet_decision_summary(result),
                )
                return 2

            # One explicit shutdown/cancel channel for the blocking timer sleep.
            # A sleeping process can only be woken by this signal; waking is by
            # Event.wait, never by a periodic screenshot or state poll.
            timer_cancel = threading.Event()

            def _cancel_timer_sleep(signum, _frame):
                timer_cancel.set()
                _print_event(
                    "expedition_timer_sleep_cancel_requested",
                    signal=signum,
                    game_actions_sent=0,
                )

            for _name in ("SIGINT", "SIGTERM", "SIGBREAK"):
                _sig = getattr(signal, _name, None)
                if _sig is None:
                    continue
                try:
                    _previous_signal_handlers[_sig] = signal.signal(_sig, _cancel_timer_sleep)
                except (OSError, ValueError):
                    # Not the main thread, or the platform refuses this signal.
                    pass
            timer_sleeper = ExpeditionTimerSleeper(
                cancel_event=timer_cancel,
                event_sink=event_sink_from_stdout(_print_event),
            )

            # One tick is the historical contract: dispatch (or explain why not)
            # and exit. --wait-for-return turns the same tick into a bounded
            # wait, which is what makes a full round trip observable in one
            # process instead of requiring a human to re-run the tool later.
            max_ticks = args.max_ticks if args.wait_for_return else 1
            # Phase 4 run budget: how many page-fallback recoveries this
            # process has already attempted. Reset per process, never per page.
            page_fallback_attempts = 0
            for tick_index in range(1, max_ticks + 1):
                try:
                    result = supervisor.tick(
                        page_evidence=page_evidence[-1] if page_evidence else None
                    )
                except ContinuousMonitorError as exc:
                    # Waiting for a return means most ticks legitimately have
                    # no trusted observation: the witness is 30s old and the
                    # monitor will not navigate until the return is inside the
                    # lead window. Refusing to act is the correct answer, and
                    # killing the wait because of it would guarantee the return
                    # is never observed. Only the wait mode tolerates this; a
                    # single-shot run still stops on the first refusal.
                    if not args.wait_for_return:
                        raise
                    _print_event(
                        "PRODUCTION_TICK_OBSERVATION_UNAVAILABLE",
                        tick=tick_index,
                        of=max_ticks,
                        error=str(exc),
                        game_actions_sent=0,
                    )
                    if tick_index == max_ticks:
                        _print_event(
                            "PRODUCTION_SUPERVISOR_PAUSED",
                            reason=(
                                f"fleets_{fleet_label}_no_trusted_observation_"
                                "within_max_ticks"
                            ),
                            dispatch_count=0,
                            game_actions_sent=0,
                            tick=tick_index,
                        )
                        return 2
                    _wait_before_next_observation(
                        args, timer_sleeper,
                        runtime=runtime, supervisor=supervisor,
                        target_fleet_ids=target_fleet_ids,
                        tick_index=tick_index, tick_budget=max_ticks,
                        wake_reason="no_trusted_observation",
                    )
                    continue
                report_actions(tick_index, result)
                if result.paused_reason is not None:
                    fallback = getattr(result, "page_fallback", None)
                    if fallback is None:
                        return pause(result, "", tick_index)
                    # Phase 4: a page class withheld this tick. The route comes
                    # from ``page_navigation`` (decided by the class alone);
                    # this loop only executes it, and every branch is bounded
                    # by PAGE_FALLBACK_MAX_ATTEMPTS and by the tick budget.
                    if page_fallback_attempts >= PAGE_FALLBACK_MAX_ATTEMPTS:
                        _print_event(
                            "page_fallback_exhausted",
                            route=fallback.route,
                            page=fallback.page,
                            attempts=page_fallback_attempts,
                            clicks_sent=0,
                            next_step="hold_for_human",
                        )
                        return pause(result, "", tick_index)
                    page_fallback_attempts += 1
                    fallback_fields = {
                        "attempt": page_fallback_attempts,
                        "of": PAGE_FALLBACK_MAX_ATTEMPTS,
                        "route": fallback.route,
                        "page": fallback.page,
                        "intent": fallback.intent,
                        "reason": fallback.reason,
                        "clicks_sent_this_tick": 0,
                    }
                    last_tick = tick_index >= max_ticks
                    if fallback.hold:
                        # MODAL: the return notice, the result page and the
                        # reward pages advance on a click anywhere and belong
                        # to the settlement / reward controllers. Sending a
                        # generic dismiss click here would bypass exactly the
                        # handlers that prove each screen they advance, so the
                        # correct fallback is to hold.
                        _print_event(
                            "page_fallback_held_for_business_handler",
                            **fallback_fields,
                            navigation_sent=0,
                            next_step="paused_page_untouched",
                        )
                        _recovery_emit(
                            _active_run_log_sink(),
                            "page_fallback_held_for_business_handler",
                            **fallback_fields,
                            navigation_sent=0,
                            next_step="paused_page_untouched",
                        )
                        return pause(result, "", tick_index)
                    if fallback.route == ROUTE_HOME_NAVIGATION:
                        # MENU: delegate to the routine that already exists. It
                        # keeps its own bounded attempts, its own post-click
                        # state proof and its own reward-screen refusal; none
                        # of that is reimplemented here.
                        _print_event(
                            "page_fallback_navigation_started",
                            **fallback_fields,
                            delegate=fallback.delegates_navigation,
                        )
                        _recovery_emit(
                            _active_run_log_sink(),
                            "page_fallback_navigation_started",
                            **fallback_fields,
                            delegate=fallback.delegates_navigation,
                        )
                        _recover_home_port(
                            game_session, event_sink=_active_run_log_sink()
                        )
                        if last_tick:
                            # Nothing left to tick: the recovery still ran (it
                            # is the point of this phase), and the process ends
                            # with the same honest pause label.
                            _print_event(
                                "page_fallback_navigation_completed",
                                **fallback_fields,
                                next_step="tick_budget_exhausted_rerun",
                            )
                            return pause(result, "", tick_index)
                        # The verdict that withheld the tick describes a page
                        # the recovery has just left. Replace it with a fresh
                        # observation instead of letting the stale row veto the
                        # next tick; with no observer wired, carrying nothing is
                        # the honest state - and dispatch still needs its own
                        # two-frame Home Port proof, which no page class gives.
                        if (
                            args.enable_ui_refresh
                            and refresh_fingerprint_document is not None
                        ):
                            refresh_observe_page()
                        else:
                            page_evidence.clear()
                        _recovery_emit(
                            _active_run_log_sink(),
                            "page_fallback_navigation_completed",
                            **fallback_fields,
                            next_step="fresh_page_observation_then_next_tick",
                        )
                        continue
                    # UNKNOWN (and the defensive HOME / no-evidence routes):
                    # look again without clicking. Repetition cannot change the
                    # class - ``page_navigation`` has no attempt counter - so
                    # this can never walk an UNKNOWN page into HOME.
                    if last_tick:
                        return pause(result, "", tick_index)
                    _print_event(
                        "page_fallback_reobserve",
                        **fallback_fields,
                        navigation_sent=0,
                        wait_seconds=PAGE_FALLBACK_REOBSERVE_SECONDS,
                        next_step="fresh_page_observation_then_next_tick",
                    )
                    time.sleep(PAGE_FALLBACK_REOBSERVE_SECONDS)
                    if (
                        args.enable_ui_refresh
                        and refresh_fingerprint_document is not None
                    ):
                        refresh_observe_page()
                    else:
                        page_evidence.clear()
                    continue
                if not result.dispatches:
                    if not args.wait_for_return:
                        return pause(
                            result, f"fleets_{fleet_label}_not_ready", tick_index
                        )
                    # Wait mode: the supervisor drives each fleet's lifecycle
                    # (out -> settle -> resupply -> re-dispatch) inside this
                    # tick. Between ticks the loop now blocks on the fleet's own
                    # timer instead of re-reading Poi every tick_interval: an
                    # unexpired deadline has nothing an observation can advance.
                    # A pause happens only when the observation budget is spent.
                    if tick_index == max_ticks:
                        return pause(
                            result,
                            f"fleets_{fleet_label}_return_not_observed_within_max_ticks",
                            tick_index,
                        )
                    _print_event(
                        "production_waiting_for_return",
                        tick=tick_index,
                        fleet_ids=list(target_fleet_ids),
                        poll_interval_seconds=args.tick_interval_seconds,
                        wait_strategy=(
                            "blocking_timer_sleep" if args.timer_sleep
                            else "legacy_interval_sleep"
                        ),
                        probes_during_wait=False,
                    )
                    # Documented early completion, attempted only when a deadline
                    # is close enough to be worth reading the expedition screen.
                    # The local deadline decides *when to look*; the displayed
                    # countdown decides whether anything happens.
                    early_outcome, sleep_cap = _maybe_attempt_early_return(
                        coordinator=early_return,
                        settings=early_return_settings,
                        timers=_collect_fleet_timers(
                            runtime, supervisor, target_fleet_ids
                        ),
                        fleet_ids=target_fleet_ids,
                        ui_arbiter=ui_arbiter,
                        event_sink=runtime.monitor.event_sink,
                        tick_index=tick_index,
                        tick_budget=max_ticks,
                        attempts_used=early_return_attempts,
                    )
                    _wait_before_next_observation(
                        args, timer_sleeper,
                        runtime=runtime, supervisor=supervisor,
                        target_fleet_ids=target_fleet_ids,
                        tick_index=tick_index, tick_budget=max_ticks,
                        wake_reason=(
                            "early_return_confirmed"
                            if early_outcome is not None and early_outcome.succeeded
                            else "return_not_observed"
                        ),
                        sleep_cap_seconds=sleep_cap,
                    )
                    continue

                # Every dispatch the supervisor returned must be one of our
                # target fleets on its configured mission, left in the verified
                # active tuple. A fleet outside the plan must never be clicked,
                # and over-dispatch is caught by the unexpected-set check below.
                dispatched_ids = {d.fleet_id for d in result.dispatches}
                unexpected = dispatched_ids - set(target_fleet_ids)
                if unexpected:
                    raise LoopError(
                        f"Supervisor dispatched fleets {sorted(unexpected)} which are "
                        f"not in the dispatch plan {list(target_fleet_ids)}; refusing "
                        "the whole result"
                    )
                for dispatch in result.dispatches:
                    expected_mission = mission_map[dispatch.fleet_id]
                    state = runtime.scheduler.fleet(dispatch.fleet_id)
                    if (
                        dispatch.mission_id != expected_mission
                        or state.phase is not FleetPhase.EXPEDITION_ACTIVE
                        or not state.home_port_verified
                    ):
                        raise LoopError(
                            f"Supervisor returned without the exact fleet "
                            f"{dispatch.fleet_id} / mission {expected_mission} active "
                            f"tuple and verified Home Port state"
                        )
                _print_event(
                    "PRODUCTION_UI_CONTROLLER_INTEGRATION_VERIFIED",
                    fleet_ids=[d.fleet_id for d in result.dispatches],
                    mission_ids=[d.mission_id for d in result.dispatches],
                    final_time_ms=[d.mission.final_time_ms for d in result.dispatches],
                    ship_ids=[list(d.mission.ship_ids) for d in result.dispatches],
                    home_port_verified=True,
                    tick=tick_index,
                    closed_loop=bool(result.settlements),
                    reward_receipts_verified=[
                        bool(getattr(execution, "reward_receipt_verified", False))
                        for execution in result.settlements
                    ],
                    evidence="fresh_poi_before_after_and_two_frame_home_port",
                )
                return 0
    except Exception as exc:
        try:
            _print_event(
                "STOPPED_FAIL_CLOSED",
                error_type=type(exc).__name__,
                error=str(exc),
                automatic_retry=False,
            )
        except OSError:
            pass
        if isinstance(exc, LoopError):
            raise
        raise LoopError(
            f"Production UI controller stopped fail-closed: {type(exc).__name__}: {exc}"
        ) from exc
    finally:
        for _sig, _handler in list(_previous_signal_handlers.items()):
            try:
                signal.signal(_sig, _handler)
            except (OSError, ValueError, TypeError):
                pass
        _previous_signal_handlers.clear()
        if witness_provider is not None:
            # A passive observer child must not outlive its run. Cancellation
            # sets a signal only; it never fabricates a capture.
            witness_provider.cancel()
            # Give the cancellable observer child a bounded chance to terminate
            # so it cannot outlive the run as an orphan process.
            try:
                witness_provider.wait(timeout_seconds=1.0)
            except Exception:
                pass
            try:
                captured = witness_provider.witnesses
                _print_event(
                    "in_process_witness_stopped",
                    captured=len(captured),
                    captured_fleet_ids=[item.fleet_id for item in captured],
                    status=witness_provider.status,
                    game_api_requests=False,
                    game_input_sent=False,
                )
            except Exception:
                # Diagnostics in a finally block must never mask the outcome.
                pass
        if runtime is not None:
            runtime.provider.close(join_timeout_seconds=1.0)


def run(args: argparse.Namespace) -> int:
    # The run log must exist before any game action can happen: the startup
    # recovery's gate rows have to be durable BEFORE the clicks they name,
    # and that recovery runs before build_runtime composes the tee. The
    # prebuilt sink reaches build_runtime through _active_run_log_sink().
    _build_and_start_run_log(args)
    if not args.authorize_ui_actions:
        raise LoopError("Missing explicit --authorize-ui-actions flag")
    if args.settle_only and not args.adopt_observed_mission:
        # Without adoption the durable state has to already say this fleet is
        # waiting for its result. Adopting is what lets a settle-only run pick up
        # an expedition that is still out and close it out when it lands.
        allowed = (
            "a durable state that already has this fleet returned or awaiting "
            "resupply; pass --adopt-observed-mission to close out one that is "
            "still on expedition"
        )
        _print_event("settle_only_without_adoption", note=allowed)
    if args.wait_for_return:
        if isinstance(args.max_ticks, bool) or not isinstance(args.max_ticks, int) or args.max_ticks < 1:
            raise LoopError("--max-ticks must be a positive integer")
        interval = args.tick_interval_seconds
        if not isinstance(interval, (int, float)) or isinstance(interval, bool) or not (interval > 0):
            raise LoopError("--tick-interval-seconds must be positive")
        grace = args.post_expiry_grace_seconds
        if (
            not isinstance(grace, (int, float))
            or isinstance(grace, bool)
            or not math.isfinite(grace)
            or grace < 0
        ):
            raise LoopError("--post-expiry-grace-seconds must be finite and non-negative")
        cap = args.max_timer_sleep_seconds
        if cap is not None and (
            not isinstance(cap, (int, float))
            or isinstance(cap, bool)
            or not math.isfinite(cap)
            or cap <= 0
        ):
            raise LoopError("--max-timer-sleep-seconds must be positive or unset")
    # The early-return knobs are validated even when the feature is off: a run
    # that names them has stated an intent, and silently ignoring a malformed
    # value would make the flag look applied when it was not. A non-numeric
    # fixture namespace is "not provided", the same tolerance the rest of this
    # file gives a mocked namespace.
    raw_lookahead = getattr(args, "early_return_lookahead_seconds", None)
    if isinstance(raw_lookahead, (int, float)) and (
        isinstance(raw_lookahead, bool)
        or not math.isfinite(raw_lookahead)
        or raw_lookahead <= 0
    ):
        raise LoopError("--early-return-lookahead-seconds must be positive")
    raw_attempts = getattr(args, "early_return_max_attempts", None)
    if isinstance(raw_attempts, int) and not isinstance(raw_attempts, bool) and raw_attempts < 0:
        raise LoopError("--early-return-max-attempts must be a non-negative integer")
    if getattr(args, "early_return", False) is True and not args.wait_for_return:
        raise LoopError(
            "--early-return needs --wait-for-return: with a single tick there is "
            "no wait whose deadline it could shorten"
        )
    from kancollebot.adapters.poi.visual_target import (
        VisualTargetError,
        require_visual_dependencies,
    )

    try:
        require_visual_dependencies()
    except VisualTargetError as exc:
        raise LoopError(f"Visual expedition UI unavailable: {exc}") from exc
    return _run_production_supervisor_once(args)


def _main_impl(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--authorize-ui-actions",
        action="store_true",
        required=True,
        help="Explicitly authorize the visible-UI dispatch named by --fleet-id/--mission-id",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=RUNTIME_CONFIG.dispatch_database,
        help="Dedicated durable scheduler/dispatch database for this one-fleet entry",
    )
    parser.add_argument(
        "--observer-script",
        type=Path,
        default=RUNTIME_CONFIG.observer_script,
    )
    parser.add_argument(
        "--run-log-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            "Also write the unified three-day rolling JSONL run log to DIR "
            "(default off, so an already-verified run behaves exactly as before). "
            "When enabled, a critical audit row that cannot be written aborts the "
            "action it describes instead of letting it happen unlogged."
        ),
    )
    parser.add_argument("--observer-timeout-seconds", type=float, default=RUNTIME_CONFIG.observer_timeout_seconds)
    parser.add_argument("--max-state-age-seconds", type=float, default=RUNTIME_CONFIG.max_state_age_seconds)
    parser.add_argument(
        "--stateless",
        action="store_true",
        default=True,
        help=(
            "P0 stateless control, ON BY DEFAULT: Poi fleet state and the "
            "visible screen are the only business facts. The dispatch ledger "
            "keeps writing audit rows but cannot veto settlement, resupply or "
            "re-dispatch; the in-process reward receipt observer is not armed "
            "and receipt completeness is not a precondition. The 0.97/0.04 "
            "fingerprint thresholds and the two-frame click gate are unchanged. "
            "Pass --strict to restore the historical ledger-veto path; the "
            "startup settlement gates are unchanged in BOTH modes."
        ),
    )
    parser.add_argument(
        "--strict",
        dest="strict",
        action="store_true",
        default=False,
        help=(
            "Restore the historical strict-ledger control mode of the main "
            "loop: other_returns and UNATTRIBUTED refuse settlement, a missing "
            "ledger or an unwritable audit gate stops the click chain, and the "
            "in-process result observer is armed. Startup settlement gates are "
            "not affected. Takes precedence over --stateless."
        ),
    )
    parser.add_argument(
        "--no-in-process-witness",
        dest="in_process_witness",
        action="store_false",
        default=True,
        help="Disable the in-process passive api_req_mission/result observer. "
             "It is on by default so a run can confirm its own reward receipt "
             "without an external helper process; it sends no game input and "
             "initiates no game API request.",
    )
    parser.add_argument(
        "--mission-result-observer-script",
        type=Path,
        default=DEFAULT_MISSION_RESULT_OBSERVER,
    )
    parser.add_argument(
        "--mission-result-timeout-seconds",
        type=float,
        default=DEFAULT_MISSION_RESULT_TIMEOUT_SECONDS,
        help="How long one passive result-observer child waits for a natural exchange",
    )
    parser.add_argument(
        "--witness-captures",
        type=int,
        default=0,
        metavar="N",
        help="Natural result exchanges to capture in one run (default: one per "
             "fleet in the dispatch plan). Each is durably recorded before the "
             "settlement close-out may adopt it.",
    )
    parser.add_argument(
        "--no-ui-refresh",
        dest="enable_ui_refresh",
        action="store_false",
        default=True,
        help="Disable the six-sample visible freshness navigation (diagnostic only; "
             "without a fresh natural /api_port/port witness the supervisor fails closed)",
    )
    parser.add_argument(
        "--global-drain",
        action="store_true",
        help="Settle returned fleets by walking the visible settlement pages once for "
             "the whole account instead of once per fleet. Required before two "
             "simultaneous returns can be resolved; off by default so the per-fleet "
             "path stays exactly as verified.",
    )
    parser.add_argument(
        "--drain-max-actions",
        type=int,
        default=8,
        help="Upper bound on the inputs one global drain may send",
    )
    parser.add_argument(
        "--drain-timeout-seconds",
        type=float,
        # A wall-clock budget, not a click authorization: inputs stay bounded
        # by --drain-max-actions and the per-click Poi settled guard, so a
        # wider window never lets the drain send an extra action. A three-page
        # walk costs about 47s on the live client (2026-11-07 03:20Z: 47.489s
        # to reach home) because each transition needs 9-19s of UNKNOWN frames
        # before the next page is identifiable; the old 45s default expired
        # 1.08s after the home port appeared at 0.9979 (03:39Z) and reported
        # reached_home=false for a settlement the server had already booked.
        # 150s is the value the verified closed loop ran with, leaving room
        # for one slow transition to repeat.
        default=150.0,
        help="Wall-clock budget for one global drain",
    )
    parser.add_argument(
        "--drain-action-interval-seconds",
        type=float,
        default=2.5,
        help="Minimum post-action window. After it, the same page is still "
             "acceptable while the frames are provably changing; it becomes "
             "ui_stuck only when they are also frozen",
    )
    parser.add_argument(
        "--drain-action-observation-seconds",
        type=float,
        default=30.0,
        help="Upper bound for one post-action observation (default 30 s). It does "
             "not lengthen the failure window: a frozen screen still stops as "
             "ui_stuck, this only allows a still-animating screen to finish",
    )
    parser.add_argument(
        "--drain-stable-frame-count",
        type=int,
        default=2,
        help="Consecutive identical frames that make a drain page trustworthy",
    )
    parser.add_argument(
        "--fleet-id",
        type=int,
        default=None,
        help="Single-fleet legacy override: send exactly this fleet (2, 3 or 4) "
             "on --mission-id. When omitted (the default), the entry runs every "
             "dispatchable fleet (2, 3, 4) concurrently; use --fleet-missions to "
             "set each fleet's expedition. --fleet-id takes precedence over "
             "--all-fleets and is the only mode that honours --minimum-ships/"
             "--flagship-level. Concurrent fleets require --global-drain.",
    )
    parser.add_argument(
        "--all-fleets",
        action="store_true",
        default=False,
        help="Run every dispatchable fleet (2, 3, 4) concurrently. This is the "
             "default behaviour when --fleet-id is not given, so the flag is "
             "explicitness only; simultaneous returns require --global-drain.",
    )
    parser.add_argument(
        "--fleet-missions",
        type=str,
        default=None,
        metavar="MAP",
        help="Multi-fleet expedition map, e.g. \"2:3,3:1,4:2\" (fleet:mission). "
             "Only used in all-fleets mode; each expedition id may appear at "
             "most once across fleets, or the run is refused before any click.",
    )
    parser.add_argument(
        "--exclude-fleets",
        type=str,
        default=None,
        metavar="IDS",
        help="Comma-separated dispatchable fleet ids to leave out of all-fleets "
             "mode, e.g. \"4\" runs only fleets 2 and 3. Must name a subset of "
             "the dispatchable fleets; the remaining set must be non-empty and "
             "still obey the no-duplicate-mission rule. Cannot be combined with "
             "--fleet-id.",
    )
    parser.add_argument(
        "--mission-id",
        type=int,
        default=None,
        help="Master-data mission id for --fleet-id; required whenever --fleet-id "
             "is given. Ignored in all-fleets mode, where --fleet-missions or the "
             "default map selects each mission.",
    )
    parser.add_argument(
        "--position-index",
        type=Path,
        default=DEFAULT_POSITION_INDEX_PATH,
        help="Position index walked on this account",
    )
    parser.add_argument(
        "--minimum-ships",
        type=int,
        default=None,
        help="Ships the chosen mission needs, read from its panel; required for a "
             "mission with no verified requirement",
    )
    parser.add_argument(
        "--flagship-level",
        type=int,
        default=None,
        help="Flagship level the chosen mission needs; required together with "
             "--minimum-ships",
    )
    parser.add_argument(
        "--detail-index",
        type=Path,
        default=DEFAULT_DETAIL_PATH,
        help="Per-expedition detail-panel references walked on this account",
    )
    parser.add_argument(
        "--marks",
        type=Path,
        default=DEFAULT_MARKS_PATH,
        help="決定 control and fleet tab references",
    )
    parser.add_argument(
        "--log-path",
        type=Path,
        default=RUNTIME_CONFIG.dispatch_log_path,
        help="The sole permitted local output file; defaults to logs/one_expedition_ui_loop.jsonl",
    )
    parser.add_argument(
        "--settle-only",
        action="store_true",
        help="Close out a fleet that is already out and never dispatch. The "
             "composition gate is skipped because no start click can be sent, and "
             "a fleet that becomes ready stops the run instead of being dispatched.",
    )
    parser.add_argument(
        "--adopt-observed-mission",
        action="store_true",
        help="Resume a target fleet that is already on the configured mission "
             "(bot restart, or an expedition started by hand) instead of pausing "
             "with fleet_N_blocked. Requires the same mission and composition; "
             "Home Port is confirmed before the first tick either way",
    )
    parser.add_argument(
        "--wait-for-return",
        action="store_true",
        help="Keep ticking while the target fleet is out, so its return, reward "
             "screens, resupply and re-dispatch happen in this process instead of "
             "requiring a second run. Bounded by --max-ticks",
    )
    parser.add_argument(
        "--max-ticks",
        type=int,
        default=60,
        help="Upper bound on supervisor ticks in --wait-for-return mode; each tick "
             "is one fresh observation, not one click",
    )
    parser.add_argument(
        "--tick-interval-seconds",
        type=float,
        default=10.0,
        help="Pause between ticks while waiting for a return",
    )
    parser.add_argument(
        "--no-timer-sleep",
        action="store_false",
        dest="timer_sleep",
        default=True,
        help="Disable the blocking sleep to the fleet's Poi timer and fall back to "
             "the historical --tick-interval-seconds pause. Off by default: an "
             "unexpired deadline has nothing an observation can advance, so the "
             "loop sleeps straight to final_time_ms + grace with zero probes",
    )
    parser.add_argument(
        "--post-expiry-grace-seconds",
        type=float,
        default=DEFAULT_POST_EXPIRY_GRACE_SECONDS,
        help="Extra wall-clock wait after a fleet timer expires, before the visible "
             "出击 -> 母港 refresh starts (contract default: 3 s)",
    )
    parser.add_argument(
        "--max-timer-sleep-seconds",
        type=float,
        default=None,
        help="Optional cap on one blocking timer sleep. Unset means sleep to the "
             "actual deadline; a cap only shortens the sleep, it never adds probes",
    )
    parser.add_argument(
        "--refresh-observation-seconds",
        type=float,
        default=DEFAULT_TOTAL_BUDGET_SECONDS,
        help="Total bounded observation budget for one 出击 -> 母港 refresh "
             "(default 120 s). Frames are sampled while the page is identified or "
             "the screen is provably still; time alone never proves a page",
    )
    parser.add_argument(
        "--refresh-changed-unknown-limit",
        type=int,
        default=DEFAULT_CHANGED_UNKNOWN_LIMIT,
        help="How many CHANGING unrecognised frames one refresh tolerates before "
             "it stops (default 20). A frozen unrecognised frame does not consume "
             "this budget; it stays bounded by the total seconds instead",
    )
    parser.add_argument(
        "--early-return",
        action="store_true",
        help="Opt in to the documented early completion: when the expedition "
             "screen itself displays less than one minute, return to the home "
             "port to finish the expedition without waiting. Off by default "
             "because it navigates away from the port while merely waiting, and "
             "the mechanic is sourced from two wikis but unverified in this "
             "client. It also needs a calibrated countdown template, and it "
             "never clicks 遠征中止 or 召還",
    )
    parser.add_argument(
        "--early-return-lookahead-seconds",
        type=float,
        default=DEFAULT_EARLY_RETURN_LOOKAHEAD_SECONDS,
        help="How long before a fleet's own deadline the displayed countdown "
             "becomes worth reading (default 90 s). This only decides when to "
             "look; the screen's own display decides whether anything happens",
    )
    parser.add_argument(
        "--early-return-max-attempts",
        type=int,
        default=2,
        help="Bounded screen-switch attempts per fleet per run (default 2). A "
             "refused attempt is never retried in a loop",
    )
    parser.add_argument(
        "--countdown-template",
        type=Path,
        default=DEFAULT_COUNTDOWN_TEMPLATE_PATH,
        help="Calibrated countdown glyph template JSON. Without it every "
             "reading is None and the early return stays unavailable",
    )
    args = parser.parse_args(argv)
    global LOG_PATH, RUN_ID, HOME_REFERENCE_BASE64
    LOG_PATH = args.log_path.expanduser().resolve()
    HOME_REFERENCE_BASE64 = None
    RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _print_event("run_started", log_path=str(LOG_PATH), output_policy="append-only-jsonl; no screenshots or other runtime files")
        result = run(args)
    except (LoopError, CdpInputError, CdpFleetStateError, UiStateGateError, OSError, TimeoutError, ValueError) as exc:
        _stop_run_log("loop_failed_closed", error_type=type(exc).__name__, tool_run_id=RUN_ID)
        try:
            _print_event("STOPPED_FAIL_CLOSED", error_type=type(exc).__name__, error=str(exc))
        except OSError as log_error:
            LOG_PATH = None
            print(json.dumps({
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "run_id": RUN_ID,
                "event": "STOPPED_FAIL_CLOSED_LOG_UNAVAILABLE",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "log_error_type": type(log_error).__name__,
            }, ensure_ascii=False), flush=True)
        return 2
    except BaseException as exc:
        # Outside the controlled tuple the process is dying anyway; leave a
        # stop row behind, then propagate the original exception unchanged.
        _stop_run_log("loop_crashed", error_type=type(exc).__name__, tool_run_id=RUN_ID)
        raise
    else:
        _stop_run_log("loop_completed", tool_run_id=RUN_ID)
        return result


def main(argv: list[str] | None = None) -> int:
    """Compatibility shim; all production commands route through the unified CLI."""
    from kancollebot.cli import main as unified_main

    forwarded = sys.argv[1:] if argv is None else argv
    return unified_main(["dispatch-once", *forwarded])


if __name__ == "__main__":
    raise SystemExit(main())
