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
    #: How many cells wide the box is drawn (``00:00`` is five).
    cells: int = 5
    #: Descriptor grid used when the glyphs were recorded. It has to travel with
    #: the templates: a descriptor recorded on one grid is meaningless on another.
    grid: tuple[int, int] = (8, 12)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.roi, tuple)
            or len(self.roi) != 4
            or any(isinstance(v, bool) or not isinstance(v, int) for v in self.roi)
        ):
            raise ValueError("roi must be an integer (x, y, width, height) tuple")
        if self.cell_width <= 0 or self.cell_height <= 0 or self.cells <= 0:
            raise ValueError("cell_width, cell_height and cells must be positive")
        if self.cell_width * self.cells > self.roi[2]:
            raise ValueError("the cells do not fit inside the configured roi")
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
        except (KeyError, TypeError, ValueError) as exc:
            raise CountdownTemplateError(
                "cell_width, cell_height and cells must be integers"
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
                grid=(int(raw_grid[0]), int(raw_grid[1])),
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
        x, y, _width, _height = self.roi
        return [
            (x + index * self.cell_width, y, self.cell_width, self.cell_height)
            for index in range(self.cells)
        ]


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
        return "".join(characters)

    @staticmethod
    def _classify(
        descriptor: tuple[int, ...], templates: CountdownTemplates
    ) -> tuple[str | None, float, float]:
        scored: list[tuple[str, float]] = []
        for name, reference in templates.glyphs.items():
            try:
                scored.append((name, fingerprint_similarity(descriptor, reference)))
            except VisualFingerprintError:
                continue
        if not scored:
            return None, 0.0, 0.0
        scored.sort(key=lambda item: item[1], reverse=True)
        best_name, best_score = scored[0]
        runner_up = scored[1][1] if len(scored) > 1 else 0.0
        return best_name, best_score, runner_up
