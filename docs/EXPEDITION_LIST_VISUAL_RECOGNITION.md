# Expedition List Visual Recognition Contract

## Purpose

Provide a read-only visual adapter from one in-memory Poi screenshot to the existing multi-page mission-catalogue accumulator. The adapter uses calibrated row-number image templates and a calibrated scrollbar-thumb template. It does not use OCR, capture or write screenshots, scroll, click, access game APIs, or authorize dispatch.

## Implementation

- `src/kancollebot/expeditions/mission_catalog_vision.py` implements bounded OpenCV template matching.
- `MissionRowTemplate` maps a visually stable crop to a trusted mission ID and name.
- `list_region` bounds all mission row searches; `scrollbar_track_region` bounds the thumb search.
- The same mission template is allowed to be absent from a page. A detected match below threshold is ignored; a page with no recognized rows is rejected.
- Same-row collisions between mission templates require the configured confidence margin; otherwise the page is rejected.
- The scrollbar thumb must be uniquely recognized in the configured track.
- Screenshots and template images are decoded and processed in memory only.

## Scroll geometry model

The page accumulator needs normalized monotonic scroll geometry. This adapter uses track-space units:

- `content_height_px = scrollbar track height`
- `viewport_height_px = scrollbar thumb height`
- `scroll_top_px = thumb top offset within the track`

The proportional thumb model is only valid when the target scrollbar's thumb height scales with viewport/content length. It is therefore disabled by default. A profile must set `scrollbar_scale_verified=True` only after a real Poi screenshot sequence confirms the model. Without that explicit calibration, the recognizer fails closed and cannot produce complete-scan evidence.

The bottom is confirmed only when the unique thumb match reaches the calibrated track end within `bottom_tolerance_px`. Absence of new mission rows is never a bottom signal.

## Calibration inputs required later on Poi

1. A fixed, stable crop for each mission number/name marker, with its trusted mission ID/name mapping.
2. The expedition list viewport region and scrollbar track region at the calibrated 1200×720 viewport.
3. A scrollbar-thumb crop that remains visually stable while scrolling.
4. A real top/middle/bottom sequence to verify thumb proportionality and choose score, ambiguity, row-collision, and bottom tolerances.
5. Repeated captures at adjacent scroll steps to verify that list rows remain recognizable and the page accumulator sees no coverage gaps.

User-local templates should remain outside the public Git repository unless separately reviewed and intentionally approved for sharing. No live assets or calibration profile are currently included.

## Validation limits

Offline tests validate profile bounds, trusted label mapping, ambiguity rejection, thumb geometry, normalized page conversion, and bottom gating. They do not prove the visual templates match live Poi, OpenCV's live similarity distribution, or that every visible row can be recognized. No production dispatch path imports this adapter.
