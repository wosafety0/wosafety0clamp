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
import os
import sys
import tempfile
import time
from collections.abc import Callable
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
    VisualFingerprintError,
    classify_screenshot_fingerprints,
    classify_supply_checkbox_state,
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
from kancollebot.core.task_attribution import merge_trusted_event_fields  # noqa: E402
from kancollebot.observability.audit_policy import audit_gate  # noqa: E402
from kancollebot.runtime_config import RUNTIME_CONFIG  # noqa: E402

# Long enough for the Home Port scene to finish settling before a frame is
# classified; the same settle the state gate uses between its two samples.
HOME_REFERENCE_SETTLE_SECONDS = 0.3


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


def _recover_home_port(game_session, attempts: int = 3, force_click: bool = False) -> None:
    """Use bounded top-left navigation retries, requiring state evidence after each click.

    The recovery cycles a bounded set of interior points within the large
    top-left 母港 button, then verifies the result with the UI state gate. An
    incorrect layout fails closed after the bounded set is exhausted.
    """
    if not force_click:
        try:
            _require_ui_state(game_session, UiState.HOME_PORT)
            _print_event("home_port_recovery_confirmed", attempt=0)
            return
        except (UiStateGateError, LoopError) as exc:
            _print_event("home_port_state_not_confirmed", attempt=0, error=str(exc))
    settlement_screen = _reward_screen_on_any_layer(game_session)
    if settlement_screen is not None:
        raise LoopError(
            f"The account is showing {settlement_screen}, a reward screen that advances on a "
            "click anywhere. Home Port recovery will not send that click: the settlement "
            "controller owns the reward chain because it proves every screen it advances."
        )
    candidates = home_port_candidates(HOME_PORT)
    for attempt in range(1, attempts + 1):
        point = candidates[(attempt - 1) % len(candidates)]
        _print_event("ui_action_precondition", action="click_home_port", attempt=attempt,
                     force_click=force_click, point=point.label)
        game_session.input.click(point)
        _print_event("ui_click_sent", point=point.label, attempt=attempt)
        time.sleep(2.0)
        _print_event("ui_action_wait_completed", point=point.label, attempt=attempt,
                     wait_seconds=2.0)
        try:
            _require_ui_state(game_session, UiState.HOME_PORT)
            _print_event("home_port_recovery_confirmed", attempt=attempt)
            return
        except (UiStateGateError, LoopError) as exc:
            _print_event("home_port_recovery_retry", attempt=attempt, error=str(exc))
    # The 母港 control is part of the UI layer it hides, so no number of retries
    # on it can restore the layer. One click on the port background does - but
    # only after the frame has been proven to be a faded port and nothing else.
    if _home_port_ui_layer_is_hidden(game_session):
        wake = RecordedPoint(600, 300, "home-port-ui-wake")
        _print_event("ui_action_precondition", action="wake_home_port_ui_layer",
                     attempt=1, force_click=False, point=wake.label)
        game_session.input.click(wake)
        _print_event("ui_click_sent", point=wake.label, attempt=1)
        time.sleep(1.8)
        _print_event("ui_action_wait_completed", point=wake.label, attempt=1,
                     wait_seconds=1.8)
        try:
            _require_ui_state(game_session, UiState.HOME_PORT)
        except (UiStateGateError, LoopError) as exc:
            _print_event("home_port_ui_wake_failed", error=str(exc))
        else:
            _print_event("home_port_recovery_confirmed", attempt=attempts + 1,
                         evidence="ui_layer_woken_by_area_click")
            return
    # The named-page proof failed on a page it cannot name. Click and observe
    # instead - bounded, and never while a reward screen is up.
    _recover_home_port_by_observation(game_session)


def _recover_home_port_by_observation(
    game_session, attempts: int = 6, interval_seconds: float = 3.0
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
    candidates = home_port_candidates(HOME_PORT)
    for attempt in range(1, attempts + 1):
        try:
            _require_ui_state(game_session, UiState.HOME_PORT)
            _print_event("home_port_recovery_confirmed", attempt=attempt,
                         evidence="observed_after_return_click")
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
            time.sleep(interval_seconds)
            continue
        point = candidates[(attempt - 1) % len(candidates)]
        _print_event("ui_action_precondition", action="click_home_port_observed",
                     attempt=attempt, force_click=False, point=point.label)
        game_session.input.click(point)
        _print_event("ui_click_sent", point=point.label, attempt=attempt)
        time.sleep(interval_seconds)
        _print_event("ui_action_wait_completed", point=point.label,
                     attempt=attempt, wait_seconds=interval_seconds)
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
    from kancollebot.expeditions.startup_settlement import StartupSettlementRefused
    from kancollebot.expeditions.recovery_classification import RecoveryPauseGate
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
    try:
        target_fleet_id = args.fleet_id
        target_mission_id = args.mission_id
        expedition_id = DEFAULT_CATALOG.number_of_mission_id(target_mission_id)
        if expedition_id is None:
            raise LoopError(
                f"Mission id {target_mission_id} is not in the master-data table; "
                "refusing to guess which row it is"
            )
        _validate_dispatch_fleet_id(target_fleet_id)
        # Account fact check, before the game webview and before any click.
        # An account without Fleet 3/4 must fail closed here rather than have
        # the monitor demand an observation for a fleet that does not exist.
        unlocked_fleet_ids = _probe_unlocked_fleet_ids()
        try:
            resolve_fleet_availability(
                unlocked_fleet_ids, (target_fleet_id,), strict=True
            )
        except FleetAvailabilityError as exc:
            raise LoopError(
                f"Fleet availability check failed: {exc}"
            ) from exc
        _print_event(
            "account_fleet_availability_verified",
            unlocked_fleet_ids=list(unlocked_fleet_ids),
            target_fleet_id=target_fleet_id,
            source="poi-cdp-fleet-snapshot",
        )
        with connect_game_webview(timeout=3.0) as game_session:
            requirement = (
                None
                if args.settle_only
                else _mission_requirement(args, target_fleet_id, target_mission_id)
            )
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
                _recover_home_port(game_session)
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

            runtime = build_runtime(
                database_path=args.database,
                observer_script=args.observer_script,
                observer_timeout_seconds=args.observer_timeout_seconds,
                max_state_age_seconds=args.max_state_age_seconds,
                dispatch_fleet_ids=(target_fleet_id,),
                mission_requirements=(
                    None if requirement is None else {target_fleet_id: requirement}
                ),
                unlocked_fleet_ids=unlocked_fleet_ids,
                fleet_availability_strict=True,
                task_kind=TaskKind.EXPEDITION,
                enable_ui_refresh=args.enable_ui_refresh,
                ui_input=game_session.input if args.enable_ui_refresh else None,
                ui_state_verifier=refresh_verify_state if args.enable_ui_refresh else None,
                adopt_unplanned_missions=args.adopt_observed_mission,
                run_log_dir=args.run_log_dir,
            )

            recovery_gate = getattr(runtime, "recovery_gate", None)
            if not isinstance(recovery_gate, RecoveryPauseGate):
                recovery_gate = RecoveryPauseGate()

            def capture_frame() -> str:
                with recovery_gate.action_permit():
                    game_session.transport.send(
                        "Input.dispatchMouseEvent",
                        {"type": "mouseMoved", "x": 1195, "y": 715},
                    )
                time.sleep(HOME_REFERENCE_SETTLE_SECONDS)
                return game_session.input.capture_screenshot()

            scratch = Path(tempfile.mkdtemp(prefix="dispatch_row_")) / "frame.png"

            def scan_list():
                payload = capture_frame()
                scratch.write_bytes(base64.b64decode(payload))
                return scan_page(decode_png(scratch))

            def click_list_point(point, label):
                with recovery_gate.action_permit():
                    game_session.input.click(RecordedPoint(point[0], point[1], label))

            list_session = ExpeditionListSession(
                scan=scan_list,
                click=click_list_point,
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
                locate_row=list_session.locate_any_tab,
                supported_fleets=(2, 3, 4),
                neutralize_pointer=lambda: (
                    recovery_gate.require_actions_allowed(),
                    game_session.transport.send(
                        "Input.dispatchMouseEvent",
                        {"type": "mouseMoved", "x": 1195, "y": 715},
                    ),
                ),
                action_guard=recovery_gate.require_actions_allowed,
                action_permit=recovery_gate.action_permit,
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
            ui_arbiter = runtime.ui_arbiter or UiOperationArbiter(
                action_guard=recovery_gate.require_actions_allowed,
                action_permit=recovery_gate.action_permit,
            )
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
                    audit_gate=audit_gate(runtime.monitor.event_sink),
                    dispatch_registry=dispatch_registry,
                    dispatch_source="supervisor",
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
                    # Same gate as dispatch: a settlement that cannot be
                    # recorded must not click a reward chain.
                    audit_gate=audit_gate(runtime.monitor.event_sink),
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
            # ``is True`` on purpose: a test double (or any Namespace-like) that
            # carries a non-boolean attribute must not silently enable a path
            # that sends UI inputs.
            drain_requested = getattr(args, "global_drain", False) is True
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
                    )
                    supervisor_drain_session = SettlementDrainSession(
                        PoiSettlementDrainObserver(ui_driver),
                        PoiSettlementDrainDriver(ui_driver),
                        event_sink=runtime.monitor.event_sink,
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
                recovery_gate=recovery_gate,
                **supervisor_kwargs,
            )

            _print_event(
                "production_supervisor_started",
                dispatch_fleets=[target_fleet_id],
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
                # Entry-level startup settlement. The gate compares the Fleet's
                # observed ship-ID set with the composition recorded when it
                # was dispatched; a missing or different set refuses with zero
                # game actions. Only after that proof does the controller own
                # the chain, and it re-proves the screen read-only before the
                # first click. The monitor is not involved in this decision.
                if settlement_controller is None:
                    raise LoopError(
                        "startup reward screen detected but no settlement controller "
                        "is wired; refusing to click the chain"
                    )
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
                        game_api_requests=False,
                    )

            def pause(result, reason: str, tick_index: int) -> int:
                _print_event(
                    "PRODUCTION_SUPERVISOR_PAUSED",
                    reason=result.paused_reason or reason,
                    dispatch_count=len(result.dispatches),
                    game_actions_sent=0,
                    tick=tick_index,
                )
                return 2

            # One tick is the historical contract: dispatch (or explain why not)
            # and exit. --wait-for-return turns the same tick into a bounded
            # wait, which is what makes a full round trip observable in one
            # process instead of requiring a human to re-run the tool later.
            max_ticks = args.max_ticks if args.wait_for_return else 1
            for tick_index in range(1, max_ticks + 1):
                try:
                    result = supervisor.tick()
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
                                f"fleet_{target_fleet_id}_no_trusted_observation_"
                                "within_max_ticks"
                            ),
                            dispatch_count=0,
                            game_actions_sent=0,
                            tick=tick_index,
                        )
                        return 2
                    time.sleep(args.tick_interval_seconds)
                    continue
                report_actions(tick_index, result)
                if result.paused_reason is not None:
                    return pause(result, "", tick_index)
                if not result.dispatches:
                    if not args.wait_for_return:
                        return pause(
                            result, f"fleet_{target_fleet_id}_not_ready", tick_index
                        )
                    # Nothing to do and nothing wrong: the fleet is out and the
                    # return has not been observed yet. Keep waiting, but only
                    # while the scheduler itself says that is the truth.
                    waiting = runtime.scheduler.fleet(target_fleet_id)
                    if not (
                        waiting.phase is FleetPhase.EXPEDITION_ACTIVE
                        and waiting.home_port_verified
                    ):
                        return pause(
                            result,
                            f"fleet_{target_fleet_id}_not_dispatchable_in_phase_"
                            f"{waiting.phase.value}",
                            tick_index,
                        )
                    if tick_index == max_ticks:
                        return pause(
                            result,
                            f"fleet_{target_fleet_id}_return_not_observed_within_max_ticks",
                            tick_index,
                        )
                    _print_event(
                        "production_waiting_for_return",
                        tick=tick_index,
                        fleet_id=target_fleet_id,
                        mission_id=waiting.mission.mission_id,
                        final_time_ms=waiting.mission.final_time_ms,
                        next_tick_seconds=args.tick_interval_seconds,
                    )
                    time.sleep(args.tick_interval_seconds)
                    continue

                if len(result.dispatches) != 1:
                    raise LoopError(
                        f"Expected exactly one Fleet {target_fleet_id} dispatch, "
                        f"observed {len(result.dispatches)}"
                    )
                dispatch = result.dispatches[0]
                state = runtime.scheduler.fleet(target_fleet_id)
                if (
                    dispatch.fleet_id != target_fleet_id
                    or dispatch.mission_id != target_mission_id
                    or state.phase is not FleetPhase.EXPEDITION_ACTIVE
                    or not state.home_port_verified
                ):
                    raise LoopError(
                        f"Supervisor returned without the exact Fleet {target_fleet_id} / "
                        f"mission {target_mission_id} active tuple and verified Home Port state"
                    )
                _print_event(
                    "PRODUCTION_UI_CONTROLLER_INTEGRATION_VERIFIED",
                    fleet_id=target_fleet_id,
                    mission_id=target_mission_id,
                    final_time_ms=dispatch.mission.final_time_ms,
                    ship_ids=list(dispatch.mission.ship_ids),
                    home_port_verified=True,
                    tick=tick_index,
                    closed_loop=bool(result.settlements),
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
        if runtime is not None:
            runtime.provider.close(join_timeout_seconds=1.0)


def run(args: argparse.Namespace) -> int:
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
        default=45.0,
        help="Wall-clock budget for one global drain",
    )
    parser.add_argument(
        "--drain-action-interval-seconds",
        type=float,
        default=2.5,
        help="Observation window after each drain input; must exceed the page transition",
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
        default=RUNTIME_CONFIG.production_dispatch_fleet_id,
        help="Fleet to send (2, 3 or 4); must actually be unlocked on this "
             "account, and Fleet 4 needs an explicit verified composition "
             "(--minimum-ships/--flagship-level) until it is calibrated live",
    )
    parser.add_argument(
        "--mission-id",
        type=int,
        default=RUNTIME_CONFIG.production_mission_id,
        help="Master-data mission id; defaults to the configured production mission",
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
    args = parser.parse_args(argv)
    global LOG_PATH, RUN_ID, HOME_REFERENCE_BASE64
    LOG_PATH = args.log_path.expanduser().resolve()
    HOME_REFERENCE_BASE64 = None
    RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _print_event("run_started", log_path=str(LOG_PATH), output_policy="append-only-jsonl; no screenshots or other runtime files")
        return run(args)
    except (LoopError, CdpInputError, CdpFleetStateError, UiStateGateError, OSError, TimeoutError, ValueError) as exc:
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


def main(argv: list[str] | None = None) -> int:
    """Compatibility shim; all production commands route through the unified CLI."""
    from kancollebot.cli import main as unified_main

    forwarded = sys.argv[1:] if argv is None else argv
    return unified_main(["dispatch-once", *forwarded])


if __name__ == "__main__":
    raise SystemExit(main())
