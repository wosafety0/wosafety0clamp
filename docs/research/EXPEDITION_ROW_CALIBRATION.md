# Expedition Row Calibration (read-only)

## Why pixel measurement

The expedition list is drawn on a canvas. Verified against a live Poi webview
(1200×720, DPR 1):

- The accessibility tree exposes the whole game surface as **one** `application`
  node with `childIds: []`. Switching from HQ to the expedition screen leaves the
  tree byte-for-byte identical (146 nodes, 8 buttons, 10 list items — all of the
  latter belonging to the DMM page shell).
- No game control name (`出撃`, `補給`, `編成`, `遠征`, `決定`, `遠征開始`, …)
  appears anywhere in the tree, on either screen.
- `DOM.enable` / `DOM.getDocument` are not allowlisted, so the DOM domain is never
  enabled and `DOM.getBoxModel` fails for **every** node, including real DOM ones.

So there is no AX/DOM route to row geometry. It must be measured from pixels.
This tool measures it without numpy, OpenCV, clicks, or game input.

## Method

1. Decode the PNG with the standard library (no third-party imaging deps).
2. Project dark pixels (luminance < threshold) across a horizontal band.
3. Split the projection where the count falls below a floor, drop bands shorter
   than a height floor (these are in-row icon fragments, not rows), then number
   the survivors 1..N.
4. Fit `center = intercept + pitch * index` by ordinary least squares and report
   the residuals plus the adjacent-gap errors.

Two calibration facts drove the defaults:

- The table background contributes a **constant ~6 dark pixels per row**. A
  presence threshold of 4 therefore treats background as text; the default is 8.
- Rows are ~44px tall and the gap between two rows is ~1px, while in-row icon
  fragments are 4–6px tall. Merging across gaps welds the column into one band,
  so `merge_gap` defaults to 0 and a height floor of 12 removes the fragments.

## Live measurement (1200×720, 鎮守府海域, 8 rows)

| item | value |
|---|---|
| first center y | 261.0 |
| last center y | 576.0 |
| rows detected | 8 / 8 (100%) |
| mean pitch | 45.00 px |
| adjacent gap range | 44.5 .. 45.5 px |
| max adjacent-gap error | 0.50 px |
| max residual vs fit | 0.38 px |
| first row residual | −0.38 px |
| last row residual | −0.38 px |

Rows (click x = 350):

| display | top | bottom | center | click |
|---|---|---|---|---|
| 01 | 250 | 272 | 261.0 | (350, 261) |
| 02 | 285 | 328 | 306.5 | (350, 306) |
| 03 | 330 | 373 | 351.5 | (350, 352) |
| 04 | 375 | 418 | 396.5 | (350, 396) |
| 05 | 420 | 463 | 441.5 | (350, 442) |
| 06 | 465 | 508 | 486.5 | (350, 486) |
| 07 | 510 | 553 | 531.5 | (350, 532) |
| A1 | 565 | 587 | 576.0 | (350, 576) |

### First/last row special-casing

None detected. The search band (234..610) is 16px above the first band and 23px
below the last, so neither end is clipped. Row 1 reports a 23px band because its
icon fragment sits in a separate 4px band that the height floor drops; its center
still matches the fit to 0.38px, so it is safe to use.

### Cross-check against the recorded dispatch plan

The recorded plan clicks `(350, 350)` for mission 3. The grid puts row 3 at
center 351.5, i.e. the recorded point is **1.5px** above the measured center and
still inside the row. This independently confirms both the recorded coordinate
and the measurement.

## Usage

```
# measure the current screen (read-only screenshot, no clicks)
python -m tools.calibrate_expedition_rows --capture --labels 01,02,03,04,05,06,07,A1

# measure a saved screenshot and write an eyeball-check strip
python -m tools.calibrate_expedition_rows --image shot.png \
    --labels 01,02,03,04,05,06,07,A1 --annotate rows.png --json-out rows.json
```

Exit codes: `0` measured, `1` no rows found, `2` label count does not match the
detected row count (the tool refuses to guess a mapping).

## Limits

- Measured for the 鎮守府海域 tab only. Other area tabs reuse the same row grid,
  but each tab's first row offset has not been verified against a capture.
- No scrolling: the list shows one screenful. A scrolled list needs a re-capture.
- Viewport must be 1200×720 at DPR 1; other sizes require a fresh measurement
  (the tool takes the ranges as arguments).
- Rows are addressed by display order, not by `api_disp_no`; the caller supplies
  the label mapping and the tool refuses a partial one.
