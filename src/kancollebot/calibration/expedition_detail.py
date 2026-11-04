"""What the detail panel proves about *which* expedition is selected.

The expedition list's selected-state fingerprint (``MISSION_SELECTED`` in
``ooi_1200x720_fingerprints.json``) covers the whole right-hand panel, so it is a
*layout* check, not an identity check. Measured live on 2026-11-04: selecting
``04 対潜警戒任務`` scored **0.9871** against the mission-3 reference and would
have been accepted, so "the panel opened" never proved "the intended mission was
clicked". For the fixed mission 3 that did not matter much - the row was a
constant. For *any* expedition it is exactly the failure to catch.

Two independent facts are checked here, and both are needed before a click that
cannot be undone:

* **which mission** - the panel's name band renders deterministically. Two
  captures of the same mission are identical (similarity ``1.0000``); two
  different missions land at ``0.87 .. 0.91`` on the same descriptor. A 0.97
  threshold therefore has a wide margin on both sides, and the reference is
  compared against *every* known expedition, not only the intended one, so a
  click that landed on a neighbour reports that neighbour instead of passing.
* **whether it is dispatchable** - a mission that is already running replaces
  決定 with 遠征中止/帰還命令. That layout scores ``0.79`` against the 決定
  reference, well below threshold, so the "panel opened" case and the "panel can
  be confirmed" case are not conflated.

Both references are per-account state: the mission names are the same for every
player, but which expeditions exist in the list, and whether a fourth fleet is
unlocked, are not. They therefore live in data files beside the position index
and are written by the walker, never inferred.

Known limits, deliberately not papered over:

* The name band is a pixel descriptor, not OCR. It cannot report a name, only
  "this matches expedition 26 better than anything else".
* A fleet tab's rendering depends on whether that fleet is on an expedition (a
  live ``FLEET_3_SELECTED`` reference captured while idle scored 0.9632 against
  the same tab while busy). Each fleet therefore keeps a *list* of references and
  matches the best one; a state nobody has captured yet fails closed.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from kancollebot.adapters.poi.visual_fingerprint import (
    VisualFingerprintError,
    compute_grid_fingerprint,
    fingerprint_similarity,
)

# Measured on the live 1200x720 client on 2026-11-04. The name sits under the
# 遠征名 label; 190px covers about nine characters, which includes the part that
# distinguishes ミ船団護衛(一号船団) from ミ船団護衛(二号船団).
NAME_BAND_CROP = (850, 188, 190, 46)
NAME_BAND_GRID = (64, 15)
# The 決定 control spans roughly x 878..1144, y 639..683. This window keeps the
# button body and excludes the 遠征中止/帰還命令 control, whose lower edge reaches
# y=655 on the in-progress layout.
DECISION_CROP = (900, 650, 240, 40)
DECISION_GRID = (48, 8)
DECISION_POINT = (1030, 670)
FLEET_TAB_TOP = 145
FLEET_TAB_CROP_SIZE = (50, 60)
FLEET_TAB_GRID = (10, 12)
# Tab centres read from the live fleet-selection screen on 2026-11-04. Fleet 1 is
# included by the game's tab strip but cannot be sent on an expedition, so it is
# not a mark here.
FLEET_TAB_POINTS = {2: (590, 170), 3: (630, 170), 4: (664, 170)}
FLEET_IDS = tuple(sorted(FLEET_TAB_POINTS))

# Same-mission captures are pixel-identical (1.0). Across the 50 walked references
# the closest *different* pair scores 0.874, and the closest pair seen live (04 read
# while 12 was the runner-up) scores 0.9534 - still under the 0.97 bar, which is
# what actually rejects a wrong row. The margin is a secondary guard against two
# references that render alike, so it is set just under the observed gap.
MINIMUM_NAME_SCORE = 0.97
MINIMUM_NAME_MARGIN = 0.03
# The 決定 button is a flat control: identical captures, 0.79 against the
# in-progress layout. Fleet tabs are small and carry state markers, so their
# bar is slightly lower.
MINIMUM_DECISION_SCORE = 0.97
MINIMUM_FLEET_SCORE = 0.95
MINIMUM_FLEET_MARGIN = 0.02

DEFAULT_DETAIL_PATH = Path(__file__).resolve().parent / "data" / "expedition_details.json"
DEFAULT_MARKS_PATH = Path(__file__).resolve().parent / "data" / "dispatch_marks.json"
DETAIL_DOCUMENT_VERSION = 1
MARKS_DOCUMENT_VERSION = 1
DEFAULT_VIEWPORT = (1200, 720)


class ExpeditionDetailError(RuntimeError):
    """The selection cannot be identified from pixels. Callers must not click."""


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_crop(crop: Sequence[int]) -> tuple[int, int, int, int]:
    if len(crop) != 4 or any(isinstance(value, bool) or not isinstance(value, int) for value in crop):
        raise ExpeditionDetailError("crop must be four integers (x, y, width, height)")
    x, y, width, height = crop
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        raise ExpeditionDetailError("crop must be a non-empty rectangle")
    return (x, y, width, height)


def _check_grid(grid: Sequence[int]) -> tuple[int, int]:
    if len(grid) != 2 or any(isinstance(value, bool) or not isinstance(value, int) for value in grid):
        raise ExpeditionDetailError("grid must be two integers (columns, rows)")
    columns, rows = grid
    if not 1 <= columns <= 64 or not 1 <= rows <= 64:
        raise ExpeditionDetailError("grid dimensions must be within 1..64")
    return (columns, rows)


def encode_levels(values: Sequence[int]) -> str:
    """Pack 0..255 levels into URL-safe base64.

    Fifty name bands are 144,000 numbers; as JSON integers that is a 2 MB file,
    as one byte each it is 192 KB. They are byte values, so storing them as bytes
    is the honest representation, not a compression trick.
    """
    return base64.b64encode(bytes(values)).decode("ascii")


def decode_levels(text: str) -> tuple[int, ...]:
    try:
        raw = base64.b64decode(text, validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise ExpeditionDetailError("fingerprint payload is not valid base64") from error
    return tuple(raw)


def _check_fingerprint(values: Sequence[int], *, expected_length: int | None = None) -> tuple[int, ...]:
    if not isinstance(values, (list, tuple)) or not values:
        raise ExpeditionDetailError("a fingerprint must be a non-empty list of levels")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255
        for value in values
    ):
        raise ExpeditionDetailError("fingerprint levels must be integers in [0, 255]")
    if expected_length is not None and len(values) != expected_length:
        raise ExpeditionDetailError(
            f"fingerprint holds {len(values)} levels but its grid needs {expected_length}"
        )
    return tuple(values)


def expected_levels(grid: tuple[int, int]) -> int:
    """Cells times three channels; a stored descriptor must be exactly this long."""
    return grid[0] * grid[1] * 3


def _read_levels(record: Mapping[str, object], expected_length: int) -> tuple[int, ...]:
    """Read a descriptor stored either as base64 bytes or as a level list."""
    packed = record.get("fingerprint_b64")
    if isinstance(packed, str) and packed:
        return _check_fingerprint(decode_levels(packed), expected_length=expected_length)
    return _check_fingerprint(record.get("fingerprint", []), expected_length=expected_length)


def name_band_fingerprint(screenshot_base64: str) -> tuple[int, ...]:
    """The mission-name band of the detail panel, as cell averages."""
    return _band_fingerprint(screenshot_base64, NAME_BAND_CROP, NAME_BAND_GRID)


def _band_fingerprint(
    screenshot_base64: str,
    crop: tuple[int, int, int, int],
    grid: tuple[int, int],
) -> tuple[int, ...]:
    try:
        return compute_grid_fingerprint(screenshot_base64, crop=crop, grid=grid)
    except (VisualFingerprintError, ValueError) as error:
        raise ExpeditionDetailError(f"cannot read the name band: {error}") from error


def decision_fingerprint(screenshot_base64: str) -> tuple[int, ...]:
    """The 決定 control region, as cell averages."""
    try:
        return compute_grid_fingerprint(
            screenshot_base64, crop=DECISION_CROP, grid=DECISION_GRID
        )
    except (VisualFingerprintError, ValueError) as error:
        raise ExpeditionDetailError(f"cannot read the 決定 control region: {error}") from error


def fleet_tab_crop(fleet_id: int) -> tuple[int, int, int, int]:
    """The crop of one fleet tab, anchored on its measured centre."""
    point = FLEET_TAB_POINTS.get(fleet_id)
    if point is None:
        raise ExpeditionDetailError(f"fleet {fleet_id} has no calibrated tab")
    width, height = FLEET_TAB_CROP_SIZE
    return (point[0] - width // 2, FLEET_TAB_TOP, width, height)


def fleet_tab_fingerprint(screenshot_base64: str, fleet_id: int) -> tuple[int, ...]:
    try:
        return compute_grid_fingerprint(
            screenshot_base64, crop=fleet_tab_crop(fleet_id), grid=FLEET_TAB_GRID
        )
    except (VisualFingerprintError, ValueError) as error:
        raise ExpeditionDetailError(
            f"cannot read fleet {fleet_id}'s tab: {error}"
        ) from error


@dataclass(frozen=True, slots=True)
class DetailMatch:
    """The expedition the panel most looks like, and by how much."""

    expedition_id: str
    score: float
    runner_up: str | None
    runner_up_score: float

    @property
    def margin(self) -> float:
        return self.score - self.runner_up_score

    def to_dict(self) -> dict[str, object]:
        return {
            "expedition_id": self.expedition_id,
            "score": round(self.score, 4),
            "runner_up": self.runner_up,
            "runner_up_score": round(self.runner_up_score, 4),
            "margin": round(self.margin, 4),
        }


@dataclass(frozen=True, slots=True)
class MarkMatch:
    """How well one reference-matched control was recognised."""

    score: float
    threshold: float
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {"score": round(self.score, 4), "threshold": self.threshold, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class DetailReference:
    expedition_id: str
    fingerprint: tuple[int, ...]
    samples: int = 1
    captured_at: str = field(default_factory=_now_utc)

    def to_dict(self) -> dict[str, object]:
        return {
            "fingerprint_b64": encode_levels(self.fingerprint),
            "samples": self.samples,
            "captured_at": self.captured_at,
        }


class ExpeditionDetailIndex:
    """Per-expedition name-band references for one account's list."""

    def __init__(
        self,
        references: Mapping[str, DetailReference] | None = None,
        *,
        crop: tuple[int, int, int, int] = NAME_BAND_CROP,
        grid: tuple[int, int] = NAME_BAND_GRID,
        label: str | None = None,
        viewport: tuple[int, int] = DEFAULT_VIEWPORT,
    ) -> None:
        self._references: dict[str, DetailReference] = dict(references or {})
        self.crop = _check_crop(crop)
        self.grid = _check_grid(grid)
        self.label = label
        self.viewport = viewport

    def __len__(self) -> int:
        return len(self._references)

    @property
    def expedition_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._references))

    def bind(self, expedition_id: str, screenshot_base64: str, *, samples: int = 1) -> DetailReference:
        """Record (or replace) one expedition's name band from a live frame."""
        reference = DetailReference(
            expedition_id=expedition_id,
            fingerprint=_band_fingerprint(screenshot_base64, self.crop, self.grid),
            samples=samples,
        )
        self._references[expedition_id] = reference
        return reference

    def reference(self, expedition_id: str) -> DetailReference | None:
        return self._references.get(expedition_id)

    def match(self, screenshot_base64: str) -> DetailMatch:
        """Which known expedition the panel shows, with its runner-up.

        Every reference is scored, not only the intended one: that is what turns
        "the panel is the right shape" into "the panel is this mission and not
        the row above it".
        """
        if not self._references:
            raise ExpeditionDetailError(
                "no expedition detail references are loaded; walk the list first"
            )
        current = _band_fingerprint(screenshot_base64, self.crop, self.grid)
        scores = {
            expedition_id: fingerprint_similarity(current, reference.fingerprint)
            for expedition_id, reference in self._references.items()
        }
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        best_id, best_score = ranked[0]
        runner_up, runner_up_score = ranked[1] if len(ranked) > 1 else (None, -1.0)
        return DetailMatch(
            expedition_id=best_id,
            score=best_score,
            runner_up=runner_up,
            runner_up_score=runner_up_score,
        )

    def require(self, expedition_id: str, screenshot_base64: str) -> DetailMatch:
        """Prove the panel shows ``expedition_id``, or raise."""
        if self.reference(expedition_id) is None:
            raise ExpeditionDetailError(
                f"expedition {expedition_id} has no detail reference; "
                "walk that list before dispatching it"
            )
        match = self.match(screenshot_base64)
        if match.expedition_id != expedition_id:
            raise ExpeditionDetailError(
                f"the selected panel is {match.expedition_id} "
                f"({match.score:.4f}), not {expedition_id} "
                f"({self._score_of(expedition_id, screenshot_base64):.4f})"
            )
        if match.score < MINIMUM_NAME_SCORE:
            raise ExpeditionDetailError(
                f"the panel matches {match.expedition_id} only at {match.score:.4f}; "
                f"below {MINIMUM_NAME_SCORE}"
            )
        if match.margin < MINIMUM_NAME_MARGIN:
            raise ExpeditionDetailError(
                f"{match.expedition_id} outscores {match.runner_up} by only "
                f"{match.margin:.4f}; below {MINIMUM_NAME_MARGIN}"
            )
        return match

    def _score_of(self, expedition_id: str, screenshot_base64: str) -> float:
        reference = self._references[expedition_id]
        return fingerprint_similarity(
            _band_fingerprint(screenshot_base64, self.crop, self.grid), reference.fingerprint
        )

    def to_document(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "version": DETAIL_DOCUMENT_VERSION,
            "viewport": list(self.viewport),
            "name_band": {"crop": list(self.crop), "grid": list(self.grid)},
            "entries": {
                expedition_id: reference.to_dict()
                for expedition_id, reference in sorted(self._references.items())
            },
        }
        if self.label is not None:
            payload["label"] = self.label
        return payload

    def save(self, path: str | Path) -> None:
        location = Path(path)
        location.parent.mkdir(parents=True, exist_ok=True)
        # Compact on purpose: 50 descriptors of 2,880 bytes each are unreadable
        # as indented integers and would be a multi-megabyte diff.
        location.write_text(
            json.dumps(self.to_document(), ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )


def load_detail_index(path: str | Path = DEFAULT_DETAIL_PATH) -> ExpeditionDetailIndex:
    """Read a detail index; a missing file is an empty index, not an error."""
    location = Path(path)
    if not location.exists():
        return ExpeditionDetailIndex()
    payload = json.loads(location.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != DETAIL_DOCUMENT_VERSION:
        raise ExpeditionDetailError(
            f"{location} is not a version {DETAIL_DOCUMENT_VERSION} detail index"
        )
    band = payload.get("name_band")
    if not isinstance(band, dict):
        raise ExpeditionDetailError("the detail index has no name_band block")
    entries = payload.get("entries", {})
    if not isinstance(entries, dict):
        raise ExpeditionDetailError("detail index entries must be an object")
    references: dict[str, DetailReference] = {}
    crop = _check_crop(band.get("crop", NAME_BAND_CROP))
    grid = _check_grid(band.get("grid", NAME_BAND_GRID))
    for expedition_id, record in entries.items():
        if not isinstance(expedition_id, str) or not isinstance(record, dict):
            raise ExpeditionDetailError("detail index entries are malformed")
        references[expedition_id] = DetailReference(
            expedition_id=expedition_id,
            fingerprint=_read_levels(record, expected_levels(grid)),
            samples=int(record.get("samples", 1)),
            captured_at=str(record.get("captured_at", "")),
        )
    label = payload.get("label")
    viewport = payload.get("viewport", list(DEFAULT_VIEWPORT))
    return ExpeditionDetailIndex(
        references,
        crop=crop,
        grid=grid,
        label=label if isinstance(label, str) else None,
        viewport=(int(viewport[0]), int(viewport[1])),
    )


class DispatchMarks:
    """The two controls the dispatch path must recognise before it clicks.

    ``決定`` proves the panel is dispatchable; the fleet tabs prove the fleet the
    game thinks is selected. Each fleet keeps every reference captured so far,
    because the tab paints a state marker when its fleet is away.
    """

    def __init__(
        self,
        *,
        decision: tuple[int, ...] | None = None,
        fleet_tabs: Mapping[int, list[tuple[int, ...]]] | None = None,
        label: str | None = None,
        viewport: tuple[int, int] = DEFAULT_VIEWPORT,
    ) -> None:
        self.decision = decision
        self.fleet_tabs: dict[int, list[tuple[int, ...]]] = {
            int(fleet_id): [_check_fingerprint(values) for values in references]
            for fleet_id, references in (fleet_tabs or {}).items()
        }
        self.label = label
        self.viewport = viewport

    def fleets(self) -> tuple[int, ...]:
        return tuple(sorted(self.fleet_tabs))

    def capture_decision(self, screenshot_base64: str) -> None:
        self.decision = decision_fingerprint(screenshot_base64)

    def add_fleet_tab(self, fleet_id: int, screenshot_base64: str) -> None:
        fingerprint = fleet_tab_fingerprint(screenshot_base64, fleet_id)
        self.fleet_tabs.setdefault(fleet_id, []).append(fingerprint)

    def require_decision(self, screenshot_base64: str) -> MarkMatch:
        """Prove the 決定 control is on screen, i.e. the panel can be confirmed."""
        if self.decision is None:
            raise ExpeditionDetailError(
                "no 決定 reference is recorded; the panel cannot be proven dispatchable"
            )
        score = fingerprint_similarity(decision_fingerprint(screenshot_base64), self.decision)
        if score < MINIMUM_DECISION_SCORE:
            raise ExpeditionDetailError(
                f"the 決定 control scores {score:.4f}, below {MINIMUM_DECISION_SCORE}: "
                "the selected expedition is probably already running"
            )
        return MarkMatch(
            score=score,
            threshold=MINIMUM_DECISION_SCORE,
            detail="決定 control recognised",
        )

    def require_fleet(self, fleet_id: int, screenshot_base64: str) -> MarkMatch:
        """Prove the fleet-selection screen has ``fleet_id``'s tab selected."""
        if fleet_id not in FLEET_TAB_POINTS:
            raise ExpeditionDetailError(
                f"fleet {fleet_id} cannot run an expedition; supported fleets are "
                f"{list(FLEET_IDS)}"
            )
        if not self.fleet_tabs:
            raise ExpeditionDetailError("no fleet tab references are recorded")
        scores = {
            candidate: max(
                (
                    fingerprint_similarity(
                        fleet_tab_fingerprint(screenshot_base64, candidate), reference
                    )
                    for reference in references
                ),
                default=-1.0,
            )
            for candidate, references in self.fleet_tabs.items()
        }
        best = max(scores.values(), default=-1.0)
        if best < MINIMUM_FLEET_SCORE:
            raise ExpeditionDetailError(
                f"no fleet tab matched above {MINIMUM_FLEET_SCORE} "
                f"(best {best:.4f}); the selection cannot be proven"
            )
        if scores.get(fleet_id, -1.0) < best - MINIMUM_FLEET_MARGIN:
            others = sorted(
                ((score, candidate) for candidate, score in scores.items() if candidate != fleet_id),
                reverse=True,
            )
            leader = others[0][1] if others else None
            raise ExpeditionDetailError(
                f"fleet {leader} is selected, not {fleet_id} "
                f"({scores.get(fleet_id, -1.0):.4f} vs {best:.4f})"
            )
        return MarkMatch(
            score=scores[fleet_id],
            threshold=MINIMUM_FLEET_SCORE,
            detail=f"fleet {fleet_id} tab recognised",
        )

    def to_document(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "version": MARKS_DOCUMENT_VERSION,
            "viewport": list(self.viewport),
            "decision": {
                "crop": list(DECISION_CROP),
                "grid": list(DECISION_GRID),
                "point": list(DECISION_POINT),
                "fingerprint_b64": encode_levels(self.decision) if self.decision else None,
            },
            "fleet_tabs": {
                str(fleet_id): {
                    "point": list(FLEET_TAB_POINTS[fleet_id]),
                    "crop": list(fleet_tab_crop(fleet_id)),
                    "grid": list(FLEET_TAB_GRID),
                    "references_b64": [encode_levels(reference) for reference in references],
                }
                for fleet_id, references in sorted(self.fleet_tabs.items())
                if fleet_id in FLEET_TAB_POINTS
            },
        }
        if self.label is not None:
            payload["label"] = self.label
        return payload

    def save(self, path: str | Path) -> None:
        location = Path(path)
        location.parent.mkdir(parents=True, exist_ok=True)
        # Compact on purpose: 50 descriptors of 2,880 bytes each are unreadable
        # as indented integers and would be a multi-megabyte diff.
        location.write_text(
            json.dumps(self.to_document(), ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )


def load_marks(path: str | Path = DEFAULT_MARKS_PATH) -> DispatchMarks:
    """Read the dispatch marks; a missing file yields marks with no references."""
    location = Path(path)
    if not location.exists():
        return DispatchMarks()
    payload = json.loads(location.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != MARKS_DOCUMENT_VERSION:
        raise ExpeditionDetailError(
            f"{location} is not a version {MARKS_DOCUMENT_VERSION} dispatch marks file"
        )
    decision_block = payload.get("decision")
    if not isinstance(decision_block, dict):
        raise ExpeditionDetailError("the dispatch marks have no decision block")
    raw_decision = decision_block.get("fingerprint_b64") or decision_block.get("fingerprint")
    decision = (
        _check_fingerprint(
            decode_levels(raw_decision)
            if isinstance(raw_decision, str)
            else raw_decision,
            expected_length=expected_levels(_check_grid(decision_block.get("grid", DECISION_GRID))),
        )
        if raw_decision
        else None
    )
    tabs = payload.get("fleet_tabs", {})
    if not isinstance(tabs, dict):
        raise ExpeditionDetailError("fleet_tabs must be an object")
    fleet_tabs: dict[int, list[tuple[int, ...]]] = {}
    for fleet_text, record in tabs.items():
        try:
            fleet_id = int(fleet_text)
        except (TypeError, ValueError) as error:
            raise ExpeditionDetailError(f"fleet tab key {fleet_text!r} is not an integer") from error
        if not isinstance(record, dict):
            raise ExpeditionDetailError("fleet tab entries must be objects")
        tab_grid = _check_grid(record.get("grid", FLEET_TAB_GRID))
        if "references_b64" in record:
            packed = record.get("references_b64")
            if not isinstance(packed, list):
                raise ExpeditionDetailError("references_b64 must be a list")
            fleet_tabs[fleet_id] = [
                _check_fingerprint(decode_levels(text), expected_length=expected_levels(tab_grid))
                for text in packed
            ]
            continue
        references = record.get("references", [])
        if not isinstance(references, list):
            raise ExpeditionDetailError("fleet tab references must be a list")
        fleet_tabs[fleet_id] = [
            _check_fingerprint(values, expected_length=expected_levels(tab_grid))
            for values in references
        ]
    label = payload.get("label")
    viewport = payload.get("viewport", list(DEFAULT_VIEWPORT))
    return DispatchMarks(
        decision=decision,
        fleet_tabs=fleet_tabs,
        label=label if isinstance(label, str) else None,
        viewport=(int(viewport[0]), int(viewport[1])),
    )
