# KCAuto Custom Visual Recognition Research

Date: 2026-10-30
Status: SOURCE_REVIEWED; KancolleBot integration NOT_IMPLEMENTED; runtime validation NOT_RUN
Scope: Public source inspection only. No KancolleBot code changed and no game input sent.

## Summary

KCAuto Custom's general UI recognition path is template matching over grayscale screenshots, not whole-screen pixel-difference thresholds. It searches named image assets inside configured screen regions, evaluates OpenCV's normalized correlation score, and offers polling helpers to wait for an asset to appear or disappear. Navigation and recovery code combine specific templates with flow logic and, in some paths, browser crash events.

Primary upstream repository: https://github.com/XVs32/kcauto_custom
Reviewed upstream branch: develop
Relevant files:
- https://github.com/XVs32/kcauto_custom/blob/develop/kcauto/util/pyvisauto.py
- https://github.com/XVs32/kcauto_custom/blob/develop/kcauto/util/kca.py
- https://github.com/XVs32/kcauto_custom/blob/develop/kcauto/constants.py
- https://github.com/XVs32/kcauto_custom/blob/develop/kcauto/nav/nav.py
- https://github.com/XVs32/kcauto_custom/blob/develop/kcauto/nav/nodes.py
- https://github.com/XVs32/kcauto_custom/blob/develop/kcauto/util/recovery.py
- https://github.com/XVs32/kcauto_custom/blob/develop/kcauto/kca_enums/ocr_enums.py

## Source-confirmed mechanics

1. `ImageMatch._match_template` loads template assets in grayscale (unless an array is already supplied), captures the selected region, converts the capture to grayscale, and calls `cv2.matchTemplate(..., cv2.TM_CCOEFF_NORMED)`.
2. `find` selects the highest-scoring location via `cv2.minMaxLoc` and raises `FindFailed` when the best score is below the supplied threshold. `exists` uses the same maximum-score threshold check; `find_all` returns locations meeting the threshold.
3. `wait` polls until a match is found or times out. Its default scan interval is 0.05 seconds, while the KCA wrapper sets the region scan interval to 0.5 seconds for wait operations. `wait_vanish` succeeds as soon as a poll does not find the template; it does not itself require multiple stable frames.
4. Threshold presets in `kcauto/constants.py`: `EXACT=0.994`, `FLEET_ID_ICON=0.99`, `NEAR_EXACT=0.95`, `VISUAL_DAMAGE=0.95`, `DEFAULT=0.8`. These are normalized template-correlation thresholds, not percentages of changed pixels and not guaranteed accuracy rates.
5. Recognition is region-scoped. Navigation checks specific assets such as `nav|home_menu_sortie.png`, `nav|side_menu_home.png`, and `nav|top_menu_home.png`. The asset tree includes targeted templates for expedition selection/dispatch, result-page next buttons, global reference points, and error/recovery states.
6. A `cached=True` search assumes the region image was already captured and has not changed. Polling helpers normally capture fresh frames. Cached captures must not be reused across UI transitions.
7. `Recovery.attempt_recovery` combines templates for result screens, catbombs, and Chrome crashes with navigation/recovery logic. Some direct-control recovery behavior sends keyboard input; it is not a safe default to copy into KancolleBot.
8. `ocr_enums.py` defines Tesseract command-line profiles for numbers, timers, and expedition-related text. A repository search did not locate direct `pytesseract`, `image_to_string`, or `image_to_data` calls, so this review does not establish how broadly those profiles are used.

## Implications for KancolleBot

- Do not use whole-screen changed-pixel percentage as the primary UI state classifier: animated ship artwork and rotating port backgrounds can dominate the score.
- Prefer a small set of stable, semantically meaningful templates within known regions (e.g. menu icons or panel-specific markers), combined with the existing Redux readback and action-before/after checks.
- Keep state recognition distinct from click authorization. A template match can be supporting evidence, not sole proof that a consequential action is safe.
- Require a unique, sufficiently strong target match and a fresh screenshot. If templates overlap, scores are ambiguous, the viewport differs, or Redux and vision disagree, return UNKNOWN and send no input.
- Improve on KCA's basic polling for safety-sensitive flows: require agreement across multiple fresh frames or a bounded stability window, while recognizing that stable pixels alone do not prove semantic completion.
- Treat error-dialog recognition as a separate, high-priority gate. Do not click a guessed coordinate or imitate KCA's broad keyboard-based recovery.
- Validate template robustness against scale, anti-aliasing, UI theme changes, animation, and visually similar elements using labeled local samples before choosing thresholds.

## KancolleBot current integration state

- Existing `src/kancollebot/adapters/poi/ui_state_gate.py` classifies only explicit accessibility-tree text; it does not inspect pixels. When the game canvas exposes no suitable accessibility labels, it returns UNKNOWN and fails closed.
- Template-matching integration is NOT_IMPLEMENTED.
- Offline comparison of two historical screenshots previously measured 21.51% changed pixels at a per-pixel max-channel difference threshold greater than 1/255. Those files were not confirmed as labeled home-port and expedition-panel samples, so the result is not valid evidence for a scene threshold.
- No live game interaction, new expedition, dependency installation, or production-code change was performed as part of this research.

## Verification and limitations

- Status: source-reviewed, not runtime-tested against KancolleBot screenshots.
- No upstream source code was copied into KancolleBot; this document records observed design mechanics and proposed adaptations.
- Template-matching accuracy and suitable regions/thresholds remain to be tested offline on correctly labeled samples.


## 2026-10-30 — OpenCV offline template-matching probe

- **Status: TESTED, preliminary only.** Python 3.13.16 successfully imported OpenCV 5.0.0 and NumPy 2.5.4 installed into a temporary directory. No dependency was added to `pyproject.toml`; no KancolleBot source was changed.
- The two existing 1200x720 screenshots `poi_cdp_game.png` and `poi_cdp_game_current.png` were visually inspected; both show the home-port screen. They are not home-port/expedition-panel samples.
- Grayscale comparison: mean absolute difference 6.5547 intensity levels; 19.85% pixels differed by more than 1 gray level; 14.67% differed by more than 5.
- Using `cv2.matchTemplate(..., cv2.TM_CCOEFF_NORMED)`, the selected top navigation mission region (775,44,130,58), top navigation items region (540,44,118,58), and sortie-menu region (176,340,245,135) each scored 1.00000 at the same location and as a full-image search. The selected ship-art region scored 0.86783 at the same location (best full-image score 0.93811 at x=520,y=108); the background-window region scored 0.85482.
- **Interpretation:** in this pair, selected UI regions were pixel-identical while ship artwork/background changed. This supports testing region-scoped visual evidence instead of whole-screen changed-pixel percentages. It does not validate home-vs-expedition classification, a click threshold, or false-positive rates; the high ship-art score also illustrates that visually similar content can match strongly.
- Grayscale changed-pixel rates are not directly comparable to the earlier RGB per-channel metric because the color space and threshold definition differ.
- **Safety:** offline image processing only; no game input, dispatch, reward collection, or page refresh occurred. The temporary script is outside the repository and the project dependencies remain unchanged.
- **Next:** use correctly labeled offline home-port and expedition-panel samples; test small panel-specific templates in bounded regions, record positive/negative score distributions and ambiguity, and keep uncertain or conflicting states as UNKNOWN. Visual scores must not independently authorize clicks.
