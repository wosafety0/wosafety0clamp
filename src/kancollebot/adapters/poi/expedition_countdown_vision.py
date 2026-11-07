"""Read the *displayed* expedition countdown from one frame, and fail closed.

Why this exists: the documented early-completion mechanic applies when the
expedition screen shows less than one minute remaining. Both wikis warn that an
external timer drifts from what the client displays, so the displayed text is the
only admissible input. This module is the reader for that text - nothing else.

It is deliberately a *reader*, not a decision: it returns the string and its
parsed seconds, and ``expedition_early_return`` owns the rule.

Design constraints:

* fixed-width digit cells inside a configured region of interest - the game
  renders the countdown in a monospace box, so no general OCR is needed;
* each cell is compared with stored glyph descriptors using the same grid
  fingerprint + similarity the page classifier already uses, so thresholds have
  one meaning in this codebase;
* **uncalibrated means unusable**: without a template document (or with a cell
  that does not match confidently) the reader returns ``None``. It never guesses
  a number, and it never falls back to a locally computed timer.

Calibration is a live step: ``tools/capture_expedition_countdown.py`` records the
region and the glyph descriptors from real frames. Until that has been run
against this client build, every read returns ``None`` and the early-return
attempt logs ``displayed_remaining_time_unreadable`` instead of acting.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from kancollebot.adapters.poi.visual_fingerprint import (
    VisualFingerprintError,
    _decode_png_rgba,
    compute_grid_fingerprint,
    fingerprint_similarity,
)

__all__ = [
    "CountdownReader",
    "CountdownTemplateError",
    "CountdownTemplates",
    "DEFAULT_COUNTDOWN_TEMPLATE_PATH",
    "GLYPH_ALPHABET",
]

#: Digits plus the separator. The game never renders letters in this box.
GLYPH_ALPHABET = "0123456789:"

DEFAULT_COUNTDOWN_TEMPLATE_PATH = (
    Path(__file__).resolve().parent / "expedition_countdown_glyphs.json"
)

#: "Ink" descriptor grid. Each cell is binarised (darker than a fraction of its
#: own median = glyph ink) and box-filtered into this many bins. Because the
#: result is size-normalised and background-invariant, the same digit reads the
#: same whether it is drawn at the larger HH:MM size or the smaller SS size, and
#: the green gradient behind it does not leak into the descriptor. This is what
#: the real two-row countdown box needs; the plain grid fingerprint treats the
#: background and font size as signal and cannot bind one descriptor per digit.
INK_GRID: tuple[int, int] = (8, 12)
INK_THRESHOLD_RATIO = 0.65


def _cell_ink(
    payload: str, box: tuple[int, int, int, int], grid: tuple[int, int]
) -> tuple[int, ...]:
    """Binarised, size-normalised ink coverage of one cell, as per mille ints."""
    width, height, rgba = _decode_png_rgba(payload)
    x, y, w, h = box
    gw, gh = grid
    if w <= 0 or h <= 0 or gw <= 0 or gh <= 0:
        raise VisualFingerprintError("cell box and grid must be positive")
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(width, x + w), min(height, y + h)
    bw, bh = x1 - x0, y1 - y0
    if bw <= 0 or bh <= 0:
        raise VisualFingerprintError("cell box lies outside the frame")
    luminances: list[list[float]] = []
    for yy in range(y0, y1):
        base = (yy * width) * 4
        row: list[float] = []
        for xx in range(x0, x1):
            index = base + xx * 4
            row.append(
                0.299 * rgba[index] + 0.587 * rgba[index + 1] + 0.114 * rgba[index + 2]
            )
        luminances.append(row)
    flat = sorted(value for row in luminances for value in row)
    median = flat[len(flat) // 2]
    threshold = median * INK_THRESHOLD_RATIO
    ink = [0] * (gw * gh)
    counts = [0] * (gw * gh)
    for j, row in enumerate(luminances):
        gy = min(gh - 1, j * gh // bh)
        for i, value in enumerate(row):
            gx = min(gw - 1, i * gw // bw)
            cell = gy * gw + gx
            counts[cell] += 1
            if value < threshold:
                ink[cell] += 1
    return tuple(
        int(round(1000.0 * ink[cell] / counts[cell])) if counts[cell] else 0
        for cell in range(gw * gh)
    )


def _ink_similarity(left: tuple[int, ...], right: tuple[int, ...]) -> float:
    if not left or len(left) != len(right):
        raise VisualFingerprintError(
            "ink descriptors must be non-empty and of equal length"
        )
    total = sum(abs(a - b) for a, b in zip(left, right))
    return 1.0 - total / (len(left) * 1000.0)


class CountdownTemplateError(RuntimeError):
    """The template document exists but is unusable."""


@dataclass(frozen=True, slots=True)
class CountdownTemplates:
    """Where the countdown is drawn, and what its glyphs look like."""

    #: ``(x, y, width, height)`` of the whole countdown box.
    roi: tuple[int, int, int, int]
    cell_width: int
    cell_height: int
    glyphs: Mapping[str, tuple[int, ...]]
    #: Similarity a cell must reach before its glyph is accepted.
    minimum_score: float = 0.93
    #: How many cells the box is drawn as, total. ``00:00`` is five; the
    #: client's ``HH:MM:SS`` countdown is eight.
    cells: int = 5
    #: How many rows the cells are stacked into. ``1`` (the default) reproduces
    #: the historical single horizontal row. The client renders ``HH:MM:SS`` in
    #: vertically layered cells, so a real calibration uses ``cell_rows`` such
    #: that ``cell_rows * cols == cells`` (for a single vertical column,
    #: ``cell_rows == cells`` and ``cols == 1``).
    cell_rows: int = 1
    #: Descriptor grid used when the glyphs were recorded. It has to travel with
    #: the templates: a descriptor recorded on one grid is meaningless on another.
    grid: tuple[int, int] = (8, 12)
    #: Explicit per-cell boxes ``(x, y, width, height)`` in reading order. When
    #: set they replace the uniform ``rows x cols`` tiling: the real client lays
    #: ``HH:MM`` on one line and ``SS`` on the next, a shape no uniform grid can
    #: express. ``None`` keeps the historical uniform behaviour.
    boxes: tuple[tuple[int, int, int, int], ...] | None = None
    #: How to assemble the read glyphs into text. ``None`` concatenates them; a
    #: tuple such as ``(2, 2, 2)`` reads HH/MM/SS groups and joins with ``':'``
    #: so a six-cell layout still yields ``HH:MM:SS``.
    groups: tuple[int, ...] | None = None
    #: ``"grid"`` (the historical full-crop fingerprint) or ``"ink"`` (binarised,
    #: size-normalised, background-invariant). The real two-row countdown needs
    #: ``"ink"``: one grid fingerprint cannot cover a digit drawn at two sizes.
    descriptor: str = "grid"

    def __post_init__(self) -> None:
        if self.descriptor not in ("grid", "ink"):
            raise ValueError("descriptor must be 'grid' or 'ink'")
        if (
            not isinstance(self.roi, tuple)
            or len(self.roi) != 4
            or any(isinstance(v, bool) or not isinstance(v, int) for v in self.roi)
        ):
            raise ValueError("roi must be an integer (x, y, width, height) tuple")
        if self.cell_width <= 0 or self.cell_height <= 0 or self.cells <= 0:
            raise ValueError("cell_width, cell_height and cells must be positive")
        if self.cell_rows <= 0 or self.cells % self.cell_rows != 0:
            raise ValueError("cell_rows must be a positive divisor of cells")
        if self.boxes is None:
            cols = self.cells // self.cell_rows
            if self.cell_width * cols > self.roi[2]:
                raise ValueError("the cells do not fit horizontally inside the roi")
            if self.cell_height * self.cell_rows > self.roi[3]:
                raise ValueError("the cells do not fit vertically inside the roi")
        else:
            if not isinstance(self.boxes, tuple) or len(self.boxes) != self.cells:
                raise ValueError("boxes must be a tuple with one entry per cell")
            for box in self.boxes:
                if (
                    not isinstance(box, tuple)
                    or len(box) != 4
                    or any(isinstance(v, bool) or not isinstance(v, int) for v in box)
                ):
                    raise ValueError(
                        "each box must be an integer (x, y, width, height) tuple"
                    )
                if box[2] <= 0 or box[3] <= 0:
                    raise ValueError("box width and height must be positive")
        if self.groups is not None:
            if (
                not isinstance(self.groups, tuple)
                or not self.groups
                or any(
                    isinstance(v, bool) or not isinstance(v, int) or v <= 0
                    for v in self.groups
                )
            ):
                raise ValueError("groups must be a tuple of positive integers")
            if sum(self.groups) != self.cells:
                raise ValueError("groups must sum to cells")
        if not 0 < self.minimum_score <= 1:
            raise ValueError("minimum_score must be in (0, 1]")
        if (
            not isinstance(self.grid, tuple)
            or len(self.grid) != 2
            or any(
                isinstance(v, bool) or not isinstance(v, int) or v <= 0
                for v in self.grid
            )
        ):
            raise ValueError("grid must be two positive integers")
        if not self.glyphs:
            raise ValueError("glyphs must not be empty")
        for name, descriptor in self.glyphs.items():
            if not isinstance(name, str) or not name:
                raise ValueError("glyph names must be non-empty strings")
            if not descriptor:
                raise ValueError(f"glyph {name!r} has an empty descriptor")

    @classmethod
    def from_document(cls, document: object) -> CountdownTemplates:
        """Build from the JSON document written by the calibration tool."""
        if not isinstance(document, Mapping):
            raise CountdownTemplateError("the countdown template document must be an object")
        if document.get("version") != 1:
            raise CountdownTemplateError("unsupported countdown template version")
        roi = document.get("roi")
        if (
            not isinstance(roi, list)
            or len(roi) != 4
            or any(isinstance(v, bool) or not isinstance(v, int) for v in roi)
        ):
            raise CountdownTemplateError("roi must be a list of four integers")
        try:
            cell_width = int(document["cell_width"])
            cell_height = int(document["cell_height"])
            cells = int(document.get("cells", 5))
            cell_rows = int(document.get("cell_rows", 1))
        except (KeyError, TypeError, ValueError) as exc:
            raise CountdownTemplateError(
                "cell_width, cell_height, cells and cell_rows must be integers"
            ) from exc
        raw_glyphs = document.get("glyphs")
        if not isinstance(raw_glyphs, Mapping):
            raise CountdownTemplateError("glyphs must be an object")
        glyphs: dict[str, tuple[int, ...]] = {}
        for name, descriptor in raw_glyphs.items():
            if not isinstance(name, str) or name not in GLYPH_ALPHABET:
                raise CountdownTemplateError(f"unsupported glyph name: {name!r}")
            if (
                not isinstance(descriptor, list)
                or not descriptor
                or any(
                    isinstance(value, bool) or not isinstance(value, int)
                    for value in descriptor
                )
            ):
                raise CountdownTemplateError(
                    f"glyph {name!r} must be a list of integers"
                )
            glyphs[name] = tuple(descriptor)
        raw_grid = document.get("grid", [8, 12])
        if (
            not isinstance(raw_grid, list)
            or len(raw_grid) != 2
            or any(isinstance(v, bool) or not isinstance(v, int) for v in raw_grid)
        ):
            raise CountdownTemplateError("grid must be a list of two integers")
        raw_boxes = document.get("boxes")
        boxes: tuple[tuple[int, int, int, int], ...] | None = None
        if raw_boxes is not None:
            if not isinstance(raw_boxes, list) or not raw_boxes:
                raise CountdownTemplateError("boxes must be a non-empty list")
            parsed_boxes: list[tuple[int, int, int, int]] = []
            for item in raw_boxes:
                if (
                    not isinstance(item, (list, tuple))
                    or len(item) != 4
                    or any(isinstance(v, bool) or not isinstance(v, int) for v in item)
                ):
                    raise CountdownTemplateError("each box must be four integers")
                parsed_boxes.append((int(item[0]), int(item[1]), int(item[2]), int(item[3])))
            boxes = tuple(parsed_boxes)
        raw_groups = document.get("groups")
        groups: tuple[int, ...] | None = None
        if raw_groups is not None:
            if (
                not isinstance(raw_groups, list)
                or not raw_groups
                or any(
                    isinstance(v, bool) or not isinstance(v, int) or v <= 0
                    for v in raw_groups
                )
            ):
                raise CountdownTemplateError(
                    "groups must be a non-empty list of positive integers"
                )
            groups = tuple(int(v) for v in raw_groups)
        descriptor = document.get("descriptor", "grid")
        if descriptor not in ("grid", "ink"):
            raise CountdownTemplateError("descriptor must be 'grid' or 'ink'")
        score = document.get("minimum_score", 0.93)
        try:
            minimum_score = float(score)
        except (TypeError, ValueError) as exc:
            raise CountdownTemplateError("minimum_score must be a number") from exc
        try:
            return cls(
                roi=(int(roi[0]), int(roi[1]), int(roi[2]), int(roi[3])),
                cell_width=cell_width,
                cell_height=cell_height,
                glyphs=glyphs,
                minimum_score=minimum_score,
                cells=cells,
                cell_rows=cell_rows,
                grid=(int(raw_grid[0]), int(raw_grid[1])),
                boxes=boxes,
                groups=groups,
                descriptor=descriptor,
            )
        except ValueError as exc:
            raise CountdownTemplateError(str(exc)) from exc

    @classmethod
    def load(cls, path: str | Path = DEFAULT_COUNTDOWN_TEMPLATE_PATH) -> CountdownTemplates:
        """Load the template document; a missing file is an error, not a default."""
        target = Path(path)
        if not target.is_file():
            raise CountdownTemplateError(f"countdown template document is missing: {target}")
        try:
            document = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CountdownTemplateError(
                f"countdown template document is unreadable: {exc}"
            ) from exc
        return cls.from_document(document)

    def cell_boxes(self) -> list[tuple[int, int, int, int]]:
        if self.boxes is not None:
            return list(self.boxes)
        x, y, _width, _height = self.roi
        cols = self.cells // self.cell_rows
        boxes: list[tuple[int, int, int, int]] = []
        for index in range(self.cells):
            row = index // cols
            column = index % cols
            boxes.append(
                (
                    x + column * self.cell_width,
                    y + row * self.cell_height,
                    self.cell_width,
                    self.cell_height,
                )
            )
        return boxes


class CountdownReader:
    """Read one countdown from one freshly captured frame.

    ``camera`` must return a base64 PNG of the current game surface. The reader
    never clicks, never navigates and never caches a previous reading.
    """

    def __init__(
        self,
        *,
        capture: Callable[[], str],
        templates: CountdownTemplates | None = None,
    ) -> None:
        if not callable(capture):
            raise ValueError("capture must be callable")
        if templates is not None and not isinstance(templates, CountdownTemplates):
            raise ValueError("templates must be CountdownTemplates")
        self._capture = capture
        self._templates = templates

    @property
    def calibrated(self) -> bool:
        return self._templates is not None

    def read_text(self) -> str | None:
        """Return the displayed countdown, or ``None`` when it cannot be read."""
        templates = self._templates
        if templates is None:
            return None
        try:
            payload = self._capture()
            if templates.descriptor == "ink":
                cells = [
                    _cell_ink(payload, box, templates.grid)
                    for box in templates.cell_boxes()
                ]
            else:
                cells = [
                    compute_grid_fingerprint(payload, crop=box, grid=templates.grid)
                    for box in templates.cell_boxes()
                ]
        except (VisualFingerprintError, ValueError, OSError):
            return None
        characters: list[str] = []
        for descriptor in cells:
            best_name, best_score, runner_up = self._classify(descriptor, templates)
            if best_name is None or best_score < templates.minimum_score:
                return None
            # A digit that is not clearly ahead of its runner-up is not a read:
            # one ambiguous cell would silently shift the whole value.
            if best_score - runner_up < 0.01:
                return None
            characters.append(best_name)
        if templates.groups is None:
            return "".join(characters)
        parts: list[str] = []
        index = 0
        for size in templates.groups:
            parts.append("".join(characters[index:index + size]))
            index += size
        return ":".join(parts)

    @staticmethod
    def _classify(
        descriptor: tuple[int, ...], templates: CountdownTemplates
    ) -> tuple[str | None, float, float]:
        scored: list[tuple[str, float]] = []
        for name, reference in templates.glyphs.items():
            try:
                if templates.descriptor == "ink":
                    score = _ink_similarity(descriptor, reference)
                else:
                    score = fingerprint_similarity(descriptor, reference)
                scored.append((name, score))
            except VisualFingerprintError:
                continue
        if not scored:
            return None, 0.0, 0.0
        scored.sort(key=lambda item: item[1], reverse=True)
        best_name, best_score = scored[0]
        runner_up = scored[1][1] if len(scored) > 1 else 0.0
        return best_name, best_score, runner_up
