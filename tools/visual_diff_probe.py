"""Offline pixel-difference probe for supplied screenshots; never captures or controls the game."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from typing import Any

def compare_arrays(image_a: Any, image_b: Any, *, roi: tuple[int, int, int, int] | None = None, threshold: int = 0) -> dict[str, Any]:
    """Compare same-sized RGB arrays and return deterministic, JSON-safe metrics."""
    import numpy as np
    a = np.asarray(image_a)
    b = np.asarray(image_b)
    if a.shape != b.shape or a.ndim != 3 or a.shape[2] != 3:
        raise ValueError("images must be same-sized HxWx3 RGB arrays")
    if threshold < 0 or threshold > 255:
        raise ValueError("threshold must be between 0 and 255")
    if roi is None:
        x, y, width, height = 0, 0, a.shape[1], a.shape[0]
    else:
        x, y, width, height = roi
        if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > a.shape[1] or y + height > a.shape[0]:
            raise ValueError("ROI is outside image bounds")
    crop_a = a[y:y + height, x:x + width].astype(np.int16)
    crop_b = b[y:y + height, x:x + width].astype(np.int16)
    delta = np.abs(crop_a - crop_b)
    changed = np.any(delta > threshold, axis=2)
    mean_a = crop_a.mean(axis=(0, 1))
    mean_b = crop_b.mean(axis=(0, 1))
    mean_delta = np.abs(mean_a - mean_b)
    return {
        "image_width": int(a.shape[1]),
        "image_height": int(a.shape[0]),
        "roi": {"x": x, "y": y, "width": width, "height": height},
        "threshold_per_channel": threshold,
        "changed_pixel_percent": round(float(changed.mean() * 100.0), 6),
        "mean_rgb_a": [round(float(v), 6) for v in mean_a],
        "mean_rgb_b": [round(float(v), 6) for v in mean_b],
        "absolute_mean_rgb_delta": [round(float(v), 6) for v in mean_delta],
    }

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image_a", type=Path)
    parser.add_argument("image_b", type=Path)
    parser.add_argument("--roi", nargs=4, type=int, metavar=("X", "Y", "W", "H"))
    parser.add_argument("--threshold", type=int, default=0, help="per-channel change threshold (0-255)")
    args = parser.parse_args()
    import cv2
    image_a = cv2.imread(str(args.image_a), cv2.IMREAD_COLOR)
    image_b = cv2.imread(str(args.image_b), cv2.IMREAD_COLOR)
    if image_a is None or image_b is None:
        parser.error("could not read one or both image files")
    image_a = cv2.cvtColor(image_a, cv2.COLOR_BGR2RGB)
    image_b = cv2.cvtColor(image_b, cv2.COLOR_BGR2RGB)
    try:
        result = compare_arrays(image_a, image_b, roi=tuple(args.roi) if args.roi else None, threshold=args.threshold)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
