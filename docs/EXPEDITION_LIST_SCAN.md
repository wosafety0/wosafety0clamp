# Expedition List Scan Contract

## Purpose

Build a complete, read-only mission catalogue from a scrollable expedition list without assuming that the initially visible rows are the entire catalogue.

## Current implementation boundary

- `src/kancollebot/expeditions/mission_catalog_scan.py` aggregates normalized observations from successive visible pages.
- It deduplicates overlapping mission IDs while preserving first-seen order.
- It rejects conflicting names, duplicate rows within a page, backward/repeated scrolling, geometry changes, and scroll jumps larger than one viewport.
- Completion requires both measured bottom geometry and an independent `bottom_confirmed` visual-gate result.
- The module does not capture screenshots, perform OCR/template matching, scroll the game UI, or send input. It is pure offline logic.

## Page adapter contract

A future visual adapter supplies:

1. The measured list `scroll_top_px`, `viewport_height_px`, and `content_height_px`.
2. The visible rows as `MissionRowObservation(mission_id, name)`.
3. `bottom_confirmed=True` only after a positive visual end-of-list check (for example, a calibrated scrollbar-thumb-at-track-end gate), never merely because no new row was detected.

Every page must contain at least one recognized mission row. Adjacent scroll positions must advance by more than the configured tolerance and by no more than one viewport, so a single page transition cannot silently skip more than one viewport of content. Overlapping rows are allowed and deduplicated. A repeated mission ID with a different name blocks the scan.

## Completion semantics and limits

`COMPLETE` means that the supplied page observations reached a visually confirmed bottom with consistent scroll geometry. It does not prove that OCR or row recognition found every row on each page. Live screenshot calibration, row-recognition precision/recall, scrollbar geometry, and actual scroll-input behavior remain unverified until Poi is available.

The scanner is deliberately not wired into production dispatch. Its output is catalogue data only and cannot authorize a game action.
