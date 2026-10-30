"""In-memory template matching for the game canvas when CDP accessibility is unavailable.

No image is written to disk. Template assets are local operator-provided references.
OpenCV is an optional runtime dependency; without it, classification fails closed.
"""
from __future__ import annotations

import base64
import math
from pathlib import Path
from typing import Mapping


class VisualStateError(RuntimeError):
    """The image cannot be classified with sufficient confidence."""


def classify_template_scores(
    scores: Mapping[str, float],
    *,
    minimum_score: float = 0.92,
    minimum_margin: float = 0.08,
) -> str:
    """Return a conservative state label from normalized template-match scores."""
    if not math.isfinite(minimum_score) or not 0 < minimum_score <= 1:
        raise ValueError("minimum_score must be finite and in (0, 1]")
    if not math.isfinite(minimum_margin) or not 0 <= minimum_margin <= 1:
        raise ValueError("minimum_margin must be finite and in [0, 1]")
    for name, score in scores.items():
        if not isinstance(name, str) or not isinstance(score, (int, float)) or isinstance(score, bool) or not math.isfinite(score):
            raise ValueError("template scores must map names to finite numbers")

    home = scores.get("home_sortie_menu.png", -1.0)
    home_nav = scores.get("negative_home_other_menu.png", -1.0)
    expedition_menu = scores.get("home_expedition_menu.png", -1.0)
    list_panel = scores.get("expedition_list_panel.png", -1.0)
    dispatch_button = scores.get("expedition_dispatch_button.png", -1.0)
    result_scores = {
        "expedition_result_success.png": scores.get("expedition_result_success.png", -1.0),
        "expedition_result_failure.png": scores.get("expedition_result_failure.png", -1.0),
        "return_notice.png": scores.get("return_notice.png", -1.0),
    }
    result_ranked = sorted(result_scores.items(), key=lambda item: item[1], reverse=True)
    _, result = result_ranked[0]
    second_result = result_ranked[1][1]
    bonus_scores = (
        scores.get("result_next_button.png", -1.0),
        scores.get("bonus_title.png", -1.0),
    )
    bonus = max(bonus_scores)

    candidates: list[tuple[str, float]] = []
    if (
        home >= minimum_score
        and home_nav >= minimum_score
        and expedition_menu < minimum_score
        and list_panel < minimum_score
        and dispatch_button < minimum_score
        and result < minimum_score
        and bonus < minimum_score
    ):
        candidates.append(("HOME_PORT", home))
    if (
        home < minimum_score
        and bonus < minimum_score
        and result >= minimum_score
        and result - second_result >= minimum_margin
    ):
        candidates.append(("RETURN_RESULT", result))
    if home < minimum_score and result < minimum_score and bonus >= minimum_score:
        candidates.append(("BONUS_ITEMS", bonus))

    if len(candidates) != 1:
        return "UNKNOWN"
    label, best = candidates[0]
    state_markers = {
        "home_sortie_menu.png",
        "negative_home_other_menu.png",
        "expedition_result_success.png",
        "expedition_result_failure.png",
        "return_notice.png",
        "result_next_button.png",
        "bonus_title.png",
    }
    competing = max(
        (score for name, score in scores.items()
         if name not in state_markers and not name.startswith("negative_")),
        default=-1.0,
    )
    if competing >= best - minimum_margin:
        return "UNKNOWN"
    return label


def classify_screenshot_png(
    screenshot_base64: str,
    template_dir: str | Path,
    *,
    cv2_module=None,
    numpy_module=None,
) -> tuple[str, dict[str, float]]:
    """Classify a CDP screenshot payload entirely in memory."""
    if not isinstance(screenshot_base64, str) or not screenshot_base64:
        raise VisualStateError("empty screenshot payload")
    if cv2_module is None or numpy_module is None:
        try:
            import cv2 as cv2_module  # type: ignore[no-redef]
            import numpy as numpy_module  # type: ignore[no-redef]
        except ImportError as exc:
            raise VisualStateError("OpenCV and NumPy are required for visual state checks") from exc
    try:
        raw = base64.b64decode(screenshot_base64, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise VisualStateError("screenshot payload is not valid base64") from exc
    image = cv2_module.imdecode(
        numpy_module.frombuffer(raw, dtype=numpy_module.uint8),
        cv2_module.IMREAD_GRAYSCALE,
    )
    if image is None:
        raise VisualStateError("screenshot payload is not a decodable image")

    directory = Path(template_dir)
    templates: dict[str, Path] = {
        name: directory / name
        for name in (
            "home_sortie_menu.png",
            "expedition_result_success.png",
            "expedition_result_failure.png",
            "result_next_button.png",
            "home_expedition_menu.png",
            "expedition_list_panel.png",
            "expedition_dispatch_button.png",
            "negative_home_other_menu.png",
            "negative_expedition_other_row.png",
        )
    }
    for pattern, label in (("*202405.png", "return_notice.png"), ("*202556.png", "bonus_title.png")):
        matches = sorted(directory.glob(pattern))
        if matches:
            templates[label] = matches[0]

    scores: dict[str, float] = {}
    for name, path in templates.items():
        if not path.is_file():
            continue
        try:
            encoded = numpy_module.frombuffer(path.read_bytes(), dtype=numpy_module.uint8)
        except OSError:
            continue
        template = cv2_module.imdecode(encoded, cv2_module.IMREAD_GRAYSCALE)
        if template is None:
            continue
        if template.shape[0] > image.shape[0] or template.shape[1] > image.shape[1]:
            continue
        match = cv2_module.matchTemplate(image, template, cv2_module.TM_CCOEFF_NORMED)
        _, maximum, _, _ = cv2_module.minMaxLoc(match)
        scores[name] = float(maximum)
    return classify_template_scores(scores), scores
