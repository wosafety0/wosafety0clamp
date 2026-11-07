"""Dependency-free, in-memory visual fingerprints for canvas-rendered game UI.

Only compact per-cell RGB descriptors are produced; screenshot bytes are never
written to disk. The caller is responsible for supplying a human-verified state
label when creating a reference descriptor.
"""
from __future__ import annotations

import base64
import math
import struct
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MAX_COMPRESSED_BYTES = 16 * 1024 * 1024
_MAX_DECODED_BYTES = 64 * 1024 * 1024
_MAX_DIMENSION = 4096


class VisualFingerprintError(ValueError):
    """The screenshot or fingerprint descriptor is invalid."""


def _paeth_predictor(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    left_distance = abs(estimate - left)
    above_distance = abs(estimate - above)
    upper_left_distance = abs(estimate - upper_left)
    if left_distance <= above_distance and left_distance <= upper_left_distance:
        return left
    if above_distance <= upper_left_distance:
        return above
    return upper_left


def _decode_png_rgba(payload_base64: str) -> tuple[int, int, bytes]:
    if not isinstance(payload_base64, str) or not payload_base64:
        raise VisualFingerprintError("screenshot payload must be a non-empty base64 string")
    try:
        raw = base64.b64decode(payload_base64, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise VisualFingerprintError("screenshot payload is not valid base64") from exc
    if len(raw) > _MAX_COMPRESSED_BYTES or not raw.startswith(_PNG_SIGNATURE):
        raise VisualFingerprintError("screenshot must be a bounded PNG payload")

    offset = len(_PNG_SIGNATURE)
    width = height = bit_depth = color_type = None
    compressed = bytearray()
    saw_end = False
    while offset + 12 <= len(raw):
        length = struct.unpack_from(">I", raw, offset)[0]
        offset += 4
        chunk_type = raw[offset:offset + 4]
        offset += 4
        if length > _MAX_COMPRESSED_BYTES or offset + length + 4 > len(raw):
            raise VisualFingerprintError("PNG chunk length is invalid")
        chunk_data = raw[offset:offset + length]
        offset += length
        expected_crc = struct.unpack_from(">I", raw, offset)[0]
        offset += 4
        actual_crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise VisualFingerprintError("PNG chunk checksum mismatch")

        if chunk_type == b"IHDR":
            if width is not None or length != 13:
                raise VisualFingerprintError("PNG header is duplicated or malformed")
            width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", chunk_data
            )
            if (
                not 1 <= width <= _MAX_DIMENSION
                or not 1 <= height <= _MAX_DIMENSION
                or bit_depth != 8
                or color_type not in {0, 2, 4, 6}
                or compression != 0
                or filtering != 0
                or interlace != 0
            ):
                raise VisualFingerprintError("PNG format is outside the supported 8-bit non-interlaced subset")
        elif chunk_type == b"IDAT":
            if width is None:
                raise VisualFingerprintError("PNG image data appeared before its header")
            compressed.extend(chunk_data)
            if len(compressed) > _MAX_COMPRESSED_BYTES:
                raise VisualFingerprintError("PNG compressed image data exceeds the size limit")
        elif chunk_type == b"IEND":
            saw_end = True
            break

    if width is None or height is None or not compressed or not saw_end:
        raise VisualFingerprintError("PNG is incomplete")
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
    stride = width * channels
    expected_size = height * (stride + 1)
    if expected_size > _MAX_DECODED_BYTES:
        raise VisualFingerprintError("PNG decoded image exceeds the size limit")
    try:
        decoded = zlib.decompress(bytes(compressed))
    except zlib.error as exc:
        raise VisualFingerprintError("PNG image data cannot be decompressed") from exc
    if len(decoded) != expected_size:
        raise VisualFingerprintError("PNG decompressed size does not match its dimensions")

    rgba = bytearray(width * height * 4)
    previous = bytearray(stride)
    source_offset = 0
    target_offset = 0
    for _row in range(height):
        filter_type = decoded[source_offset]
        source_offset += 1
        scanline = bytearray(decoded[source_offset:source_offset + stride])
        source_offset += stride
        if filter_type not in {0, 1, 2, 3, 4}:
            raise VisualFingerprintError("PNG uses an unsupported scanline filter")
        for index in range(stride):
            left = scanline[index - channels] if index >= channels else 0
            above = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) // 2
            elif filter_type == 4:
                predictor = _paeth_predictor(left, above, upper_left)
            else:
                predictor = 0
            scanline[index] = (scanline[index] + predictor) & 0xFF

        for pixel in range(width):
            source = pixel * channels
            if color_type == 6:
                red, green, blue, alpha = scanline[source:source + 4]
            elif color_type == 2:
                red, green, blue = scanline[source:source + 3]
                alpha = 255
            elif color_type == 4:
                red = green = blue = scanline[source]
                alpha = scanline[source + 1]
            else:
                red = green = blue = scanline[source]
                alpha = 255
            rgba[target_offset:target_offset + 4] = bytes((red, green, blue, alpha))
            target_offset += 4
        previous = scanline
    return width, height, bytes(rgba)


def _fingerprint_from_rgba(
    width: int,
    height: int,
    rgba: bytes,
    crop: tuple[int, int, int, int],
    grid: tuple[int, int],
) -> tuple[int, ...]:
    if (
        not isinstance(crop, tuple)
        or len(crop) != 4
        or any(isinstance(value, bool) or not isinstance(value, int) for value in crop)
    ):
        raise VisualFingerprintError("crop must be an integer (x, y, width, height) tuple")
    x, y, crop_width, crop_height = crop
    if x < 0 or y < 0 or crop_width <= 0 or crop_height <= 0 or x + crop_width > width or y + crop_height > height:
        raise VisualFingerprintError("crop must be a non-empty rectangle inside the screenshot")
    if (
        not isinstance(grid, tuple)
        or len(grid) != 2
        or any(isinstance(value, bool) or not isinstance(value, int) for value in grid)
    ):
        raise VisualFingerprintError("grid must be an integer (columns, rows) tuple")
    columns, rows = grid
    if not 1 <= columns <= 64 or not 1 <= rows <= 64 or columns > crop_width or rows > crop_height:
        raise VisualFingerprintError("grid dimensions are outside the supported crop bounds")

    fingerprint: list[int] = []
    for grid_y in range(rows):
        y0 = y + (grid_y * crop_height) // rows
        y1 = y + ((grid_y + 1) * crop_height) // rows
        for grid_x in range(columns):
            x0 = x + (grid_x * crop_width) // columns
            x1 = x + ((grid_x + 1) * crop_width) // columns
            red_sum = green_sum = blue_sum = alpha_sum = 0
            for pixel_y in range(y0, y1):
                offset = (pixel_y * width + x0) * 4
                for _pixel_x in range(x0, x1):
                    red, green, blue, alpha = rgba[offset:offset + 4]
                    red_sum += red * alpha
                    green_sum += green * alpha
                    blue_sum += blue * alpha
                    alpha_sum += alpha
                    offset += 4
            if alpha_sum:
                fingerprint.extend((
                    (red_sum + alpha_sum // 2) // alpha_sum,
                    (green_sum + alpha_sum // 2) // alpha_sum,
                    (blue_sum + alpha_sum // 2) // alpha_sum,
                ))
            else:
                fingerprint.extend((0, 0, 0))
    return tuple(fingerprint)


def compute_grid_fingerprint(
    screenshot_base64: str,
    *,
    crop: tuple[int, int, int, int],
    grid: tuple[int, int] = (24, 16),
) -> tuple[int, ...]:
    """Return row-major RGB cell averages for a crop of an in-memory PNG."""
    width, height, rgba = _decode_png_rgba(screenshot_base64)
    return _fingerprint_from_rgba(width, height, rgba, crop, grid)


# Live 2026-11-06 (L4 soak): the bonus-items page shares its layout with the
# expedition-success page, so every fixed region scores high on both - only
# the centre content differs (0.64 similarity). But that content crop is
# expedition-specific (expedition 1 vs 3 scored 0.9021 on one template),
# which stranded every non-captured expedition on fail-closed. Variant
# states carry per-expedition fingerprints of the same content crop and
# merge into the canonical state (max) before ranking, so the classifier
# still answers with one name. A variant never wins the ranking itself: its
# evidence travels under the canonical name, and on a *success* page the
# merged score stays low (0.6732 / 0.6427), leaving that decision unchanged.
CANONICAL_STATE_VARIANTS = {
    "BONUS_ITEMS": ("BONUS_ITEMS_M1", "BONUS_ITEMS_M2"),
    # Live 2026-11-06 (L4 soak): the expedition-list panel changes its centre
    # content with the selected mission, so a whole-panel descriptor captured
    # from one mission scores a stable but sub-threshold 0.9546-0.9607 on
    # other missions (all other states' scores stayed constant, ruling out a
    # DPI / ROI / preprocessing shift). A descriptor recaptured from those
    # pages merges into the canonical name exactly like the bonus variants do:
    # it never wins the ranking under its own name, and it changes no threshold.
    "MISSION_SELECTED": ("MISSION_SELECTED_M1",),
}


@dataclass(frozen=True)
class FingerprintRanking:
    """The decision made from ONE screenshot, with its two highest states.

    ``accepted`` is false whenever the best score misses ``minimum_score`` or
    the Top 1 / Top 2 gap misses ``minimum_margin``. Callers use ``state`` for
    the fail-closed answer; ``scores`` is the full per-state audit trail.
    """

    best_name: str | None
    best_score: float
    second_name: str | None
    second_score: float
    scores: dict[str, float]
    accepted: bool

    @property
    def state(self) -> str:
        return self.best_name if self.accepted and self.best_name is not None else "UNKNOWN"


def rank_screenshot_fingerprints(
    screenshot_base64: str,
    document: Mapping[str, object],
    *,
    minimum_score: float = 0.90,
    minimum_margin: float = 0.02,
) -> FingerprintRanking:
    """Score EVERY state from ONE screenshot and rank Top 1 / Top 2.

    This is a single-frame probe. It never relaxes the decision thresholds and
    its result is never, by itself, authority to send a fixed-coordinate click:
    the click gates still require two independent consecutive frames.

    The body is the historical ``classify_screenshot_fingerprints`` decision,
    kept verbatim so that delegating to it cannot change any existing verdict.
    """
    if not math.isfinite(minimum_score) or not 0 < minimum_score <= 1:
        raise ValueError("minimum_score must be finite and in (0, 1]")
    if not math.isfinite(minimum_margin) or not 0 <= minimum_margin <= 1:
        raise ValueError("minimum_margin must be finite and in [0, 1]")
    if not isinstance(document, Mapping) or document.get("version") != 1:
        raise VisualFingerprintError("fingerprint document has an unsupported schema")
    width, height, rgba = _decode_png_rgba(screenshot_base64)
    if document.get("viewport") != [width, height]:
        raise VisualFingerprintError("fingerprint viewport does not match the screenshot")
    states = document.get("states")
    if not isinstance(states, Mapping) or not states:
        return FingerprintRanking(None, -1.0, None, -1.0, {}, False)

    scores: dict[str, float] = {}
    for name, template in states.items():
        if not isinstance(name, str) or not name or not isinstance(template, Mapping):
            raise VisualFingerprintError("fingerprint state entries are malformed")
        crop = template.get("crop")
        grid = template.get("grid")
        reference = template.get("fingerprint")
        if not isinstance(crop, list) or not isinstance(grid, list) or not isinstance(reference, list):
            raise VisualFingerprintError(f"fingerprint descriptor for {name} is malformed")
        current = _fingerprint_from_rgba(width, height, rgba, tuple(crop), tuple(grid))
        scores[name] = fingerprint_similarity(current, reference)

    canonical_variants = {
        variant: canonical
        for canonical, variants in CANONICAL_STATE_VARIANTS.items()
        for variant in variants
    }
    for variant, canonical in canonical_variants.items():
        if variant in scores and canonical in scores:
            scores[canonical] = max(scores[canonical], scores[variant])
    # A variant is only suppressed from the ranking while its canonical state
    # exists in the document; a lone variant keeps its own name.
    suppressed = {
        variant for variant, canonical in canonical_variants.items()
        if canonical in scores
    }
    ranked = sorted(
        (item for item in scores.items() if item[0] not in suppressed),
        key=lambda item: item[1],
        reverse=True,
    )
    if not ranked:
        return FingerprintRanking(None, -1.0, None, -1.0, scores, False)
    best_name, best_score = ranked[0]
    # Supply fleet selections are child states of the supply page. Their
    # shared header intentionally resembles SUPPLY_MENU, so do not let that
    # parent checkpoint erase an otherwise unique selected-fleet match.
    #
    # The expedition return notice is the same situation one level up: it *is*
    # the Home Port plus a bubble, so every Home Port crop scores just as high
    # on it as on an idle port. Without the exemption the two states would
    # always fall inside the margin and every return would classify as UNKNOWN.
    parent_state = {
        "SUPPLY_FLEET_1_SELECTED": "SUPPLY_MENU",
        "SUPPLY_FLEET_2_SELECTED": "SUPPLY_MENU",
        "SUPPLY_FLEET_3_SELECTED": "SUPPLY_MENU",
        "SUPPLY_FLEET_4_SELECTED": "SUPPLY_MENU",
        "HOME_PORT_RETURN_NOTICE": "HOME_PORT",
    }.get(best_name)
    competitors = [
        (name, score) for name, score in ranked[1:]
        if name != parent_state
    ]
    second_name, second_score = competitors[0] if competitors else (None, -1.0)
    accepted = (
        best_score >= minimum_score
        and best_score - second_score >= minimum_margin
    )
    return FingerprintRanking(
        best_name=best_name,
        best_score=best_score,
        second_name=second_name,
        second_score=second_score,
        scores=scores,
        accepted=accepted,
    )


def classify_screenshot_fingerprints(
    screenshot_base64: str,
    document: Mapping[str, object],
    *,
    minimum_score: float = 0.90,
    minimum_margin: float = 0.02,
) -> tuple[str, dict[str, float]]:
    """Classify one screenshot against state-specific crops stored as RGB descriptors."""
    ranking = rank_screenshot_fingerprints(
        screenshot_base64,
        document,
        minimum_score=minimum_score,
        minimum_margin=minimum_margin,
    )
    return ranking.state, ranking.scores


def classify_supply_checkbox_state(
    screenshot_base64: str,
    parent_document: Mapping[str, object],
    checkbox_document: Mapping[str, object],
    *,
    fleet_index: int = 2,
    ship_index: int = 1,
    minimum_score: float = 0.90,
    minimum_margin: float = 0.04,
) -> tuple[str, dict[str, float]]:
    """Classify one fleet ship checkbox after its selected-fleet parent matches."""
    if isinstance(fleet_index, bool) or not isinstance(fleet_index, int) or fleet_index not in (2, 3):
        raise ValueError("fleet_index must be 2 or 3")
    if isinstance(ship_index, bool) or not isinstance(ship_index, int) or ship_index not in (1, 2, 3, 4):
        raise ValueError("ship_index must be an integer from 1 through 4")
    parent_name = f"SUPPLY_FLEET_{fleet_index}_SELECTED"
    parent_state, parent_scores = classify_screenshot_fingerprints(
        screenshot_base64, parent_document
    )
    combined_scores = {
        f"parent:{name}": score for name, score in parent_scores.items()
    }
    if parent_state != parent_name:
        return parent_state, combined_scores

    if not math.isfinite(minimum_score) or not 0 < minimum_score <= 1:
        raise ValueError("minimum_score must be finite and in (0, 1]")
    if not math.isfinite(minimum_margin) or not 0 <= minimum_margin <= 1:
        raise ValueError("minimum_margin must be finite and in [0, 1]")
    if not isinstance(checkbox_document, Mapping) or checkbox_document.get("version") != 1:
        raise VisualFingerprintError("checkbox fingerprint document has an unsupported schema")

    width, height, rgba = _decode_png_rgba(screenshot_base64)
    if checkbox_document.get("viewport") != [width, height]:
        raise VisualFingerprintError("checkbox fingerprint viewport does not match the screenshot")
    states = checkbox_document.get("states")
    checked_name = f"SUPPLY_FLEET_{fleet_index}_SHIP_{ship_index}_CHECKED"
    unchecked_name = f"SUPPLY_FLEET_{fleet_index}_SHIP_{ship_index}_UNCHECKED"
    required_states = {checked_name, unchecked_name}
    if not isinstance(states, Mapping) or not required_states.issubset(states):
        raise VisualFingerprintError(
            f"checkbox document must contain checked and unchecked states for fleet {fleet_index} ship {ship_index}"
        )

    checkbox_scores: dict[str, float] = {}
    for name in sorted(required_states):
        template = states[name]
        if not isinstance(template, Mapping):
            raise VisualFingerprintError(f"checkbox fingerprint descriptor for {name} is malformed")
        crop = template.get("crop")
        grid = template.get("grid")
        reference = template.get("fingerprint")
        if not isinstance(crop, list) or not isinstance(grid, list) or not isinstance(reference, list):
            raise VisualFingerprintError(f"checkbox fingerprint descriptor for {name} is malformed")
        current = _fingerprint_from_rgba(width, height, rgba, tuple(crop), tuple(grid))
        checkbox_scores[name] = fingerprint_similarity(current, reference)

    combined_scores.update({
        f"checkbox:{name}": score for name, score in checkbox_scores.items()
    })
    ranked = sorted(checkbox_scores.items(), key=lambda item: item[1], reverse=True)
    best_name, best_score = ranked[0]
    second_score = ranked[1][1]
    if best_score < minimum_score or best_score - second_score < minimum_margin:
        return "UNKNOWN", combined_scores
    return best_name, combined_scores


def average_fingerprints(samples: Sequence[Sequence[int]]) -> tuple[int, ...]:
    """Average multiple descriptors of the same manually confirmed screen."""
    if not samples:
        raise VisualFingerprintError("at least one fingerprint sample is required")
    length = len(samples[0])
    if length == 0 or any(len(sample) != length for sample in samples):
        raise VisualFingerprintError("fingerprint samples must have the same non-zero length")
    for sample in samples:
        if any(isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255 for value in sample):
            raise VisualFingerprintError("fingerprint values must be integers in [0, 255]")
    return tuple(
        (sum(sample[index] for sample in samples) + len(samples) // 2) // len(samples)
        for index in range(length)
    )


def fingerprint_similarity(left: Sequence[int], right: Sequence[int]) -> float:
    """Return normalized mean absolute RGB difference (1.0 means identical)."""
    if not left or len(left) != len(right):
        raise VisualFingerprintError("fingerprints must have equal non-zero lengths")
    for fingerprint in (left, right):
        if any(isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255 for value in fingerprint):
            raise VisualFingerprintError("fingerprint values must be integers in [0, 255]")
    difference = sum(abs(a - b) for a, b in zip(left, right)) / (len(left) * 255)
    return max(0.0, min(1.0, 1.0 - difference))


def classify_fingerprints(
    current: Sequence[int],
    templates: Mapping[str, Sequence[int]],
    *,
    minimum_score: float = 0.90,
    minimum_margin: float = 0.02,
) -> tuple[str, dict[str, float]]:
    """Classify only one clearly separated fingerprint match; otherwise UNKNOWN."""
    if not math.isfinite(minimum_score) or not 0 < minimum_score <= 1:
        raise ValueError("minimum_score must be finite and in (0, 1]")
    if not math.isfinite(minimum_margin) or not 0 <= minimum_margin <= 1:
        raise ValueError("minimum_margin must be finite and in [0, 1]")
    if not isinstance(templates, Mapping) or not templates:
        return "UNKNOWN", {}
    scores = {
        name: fingerprint_similarity(current, reference)
        for name, reference in templates.items()
        if isinstance(name, str) and name
    }
    if len(scores) != len(templates):
        raise VisualFingerprintError("template names must be non-empty strings")
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_name, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else -1.0
    if best_score < minimum_score or best_score - second_score < minimum_margin:
        return "UNKNOWN", scores
    return best_name, scores