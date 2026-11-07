# Developer Handoff

- **Updated:** 2026-11-07 (current remote state added above; older sections remain historical)
- **Repository:** wosafety0/wosafety0clamp
- **Working branch at the time of this handoff (historical):** dev/expedition-closed-loop-mvp
- **Code baseline before this documentation update:** 1aba35893daa3974c55a6ac54aec2c9dcac8cccb

## Current remote integration and safety-candidate state (2026-11-07)

- **Application source base:** `feat/production-settlement-resupply-wiring-20261105` @ `cdac285d2bc30ac32ceaa2e87cd56f9414ccc0a2`. Default `main` remains a rules/workflow-only branch containing `.github`.
- **Current narrow candidate:** Draft PR [#43](https://github.com/wosafety0/wosafety0clamp/pull/43), head branch `feat/run-log-critical-ui-gate-current-base-20261107`, current code/test head `5f9dc881a7a442721c5123117b18e1065985a0de`, base `cdac285d2bc30ac32ceaa2e87cd56f9414ccc0a2`. It is open/unmerged.
- **Verified CI for the code/test head:** Push [42274851506](https://github.com/wosafety0/wosafety0clamp/actions/runs/42274851506) passed 1,936 Python tests (22 optional vision skips) and Node 19/19. Feature push [42274851416](https://github.com/wosafety0/wosafety0clamp/actions/runs/42274851416) and Feature PR-context [42274862200](https://github.com/wosafety0/wosafety0clamp/actions/runs/42274862200) passed all four Python 3.12/3.13 × vision with/without jobs.
- **PR #43 scope:** sticky run-log degraded latch; critical-event sink preflight; pre-click attempt/sent audit for visible refresh and expedition-list tab/scroll; production session receives the shared runtime event sink. The test/CI follow-up changed no production behavior.
- **Home Port recovery audit follow-up:** Draft PR [#44](https://github.com/wosafety0/wosafety0clamp/pull/44), code/test head `9228d5330f65fb2b49224079cc5a3dcae841b67f`, is stacked on PR #43 head `ae84367ed6e3a4e2f5ba76c4d9d8d8897b62bd33`. Subsequent documentation-only archive commits are progress `ef7d612c4165fdf1d20f3220c4f51a22ba070f6f` and HANDOFF `05012a8406285055d3bb47c44fedea19e91fc46f`; the current PR branch head is the latest commit shown on PR #44. The change classifies `home_port_recovery_click_attempted` and `home_port_recovery_click_sent` as critical, and makes `_recovery_emit(gating=True)` reject any event absent from the critical set. This closes the gap where a failed recovery-attempt log append could be swallowed before a click.
- **Verified PR #44 CI:** Code/test-head Feature PR-context [42279342415](https://github.com/wosafety0/wosafety0clamp/actions/runs/42279342415) passed all four Python 3.12/3.13 × vision with/without jobs. Each ran 1,939 Python tests; vision-with jobs skipped 4 optional tests and vision-without jobs skipped 22. Node static tests passed 19/19. Documentation archive-head Feature PR-context [42280132844](https://github.com/wosafety0/wosafety0clamp/actions/runs/42280132844) also passed all four jobs with the same 1,939-test matrix and Node 19/19. Initial run [42278373559](https://github.com/wosafety0/wosafety0clamp/actions/runs/42278373559) exposed only brittle store-append-count assertions; those were replaced with click-boundary assertions before the passing runs.
- **Do not conflate older stacks:** PR #42 is on a different, older identity-contract line. PR #43 is a narrow port to the current production-settlement base; it is not a merge of #42.
- **Live acceptance boundary:** the 2026-11-06 evidence includes one Fleet 2 closed-loop cycle, followed by a separate stability attempt that stopped fail-closed at `unknown_page`. Three-cycle stability, multi-fleet acceptance and Fleet 4 supply acceptance are not established. Do not change the drain observation window or thresholds without the separate user decision recorded in the progress log.
- **Run-log policy:** production run-log wiring remains opt-in/default-off. PR #44 closes the Home Port recovery click classification gap only; the `dispatch_id` attempt/lifecycle distinction and `event_key` semantic deduplication remain unresolved and are not silently changed by this candidate.
- **Visual fingerprint threshold update (2026-11-07):** `rank_screenshot_fingerprints()`, `classify_screenshot_fingerprints()`, `classify_supply_checkbox_state()`, and `classify_fingerprints()` now all default to `minimum_score=0.90`; `minimum_margin=0.04` remains unchanged. The generic classifier's `0.90` default was aligned in commit `207d1ff3e114507a32cdad9295114027bbe5149f`, with a regression test proving a clear ~0.91 match is accepted in `3c30a178b987d8cf5777e0c8b6670ed681de183d`. GitHub readback is required; CI is pending, so the threshold change is implemented but not yet test-verified.
- **This scheduled development run is repository/CI-only:** no Poi/CDP connection, game input, page execution, Runtime.evaluate, getter access, account operation or system change.

For the authoritative chronology and exact evidence, read [PROJECT_kancollebot_Progress.md](PROJECT_kancollebot_Progress.md), then the game-rule record [EXPEDITION_SETTLEMENT.md](game-rules/EXPEDITION_SETTLEMENT.md). The older 2026-11-04 section below is historical, not the current remote branch state.


## Historical integration state (2026-11-04)

The body of this file is a **2026-11-03 point-in-time handoff**. Its branch names, baseline SHA and CI run describe that moment only; they are not the current integration target.

| Role | Branch | Local HEAD | Remote HEAD |
| --- | --- | --- | --- |
| Integration target | `dev/dispatch-observer-safety-current-mvp-20261103` | `3bd949b` | `86f294c` |
| Preservation / active work line | `feat/closed-loop-settlement-20261104` | `3bd949b` | `caa3e5a` |
| Verified fast-forward snapshot (local, temporary) | `temp/integration-ff-20261104` | `caa3e5a` | n/a |

The 2026-11-04 integration was a **local-only** pure fast-forward (81 commits, no new merge commit). **No push, PR or merge to a remote branch has occurred.** `3bd949b` is a local-only commit and must not be described as an integrated remote version; a fresh clone cannot contain it. The GitHub default branch **main** (`a4f3777`) still contains only `.github`. Re-check with `git ls-remote origin <branch>` before relying on any SHA here.

`dev/expedition-closed-loop-mvp` is a historical MVP branch that has diverged from the current line and cannot be fast-forwarded. For the authoritative chronological record see `docs/PROJECT_kancollebot_Progress.md`.

This document is the concise handoff entry point. The long design and progress records remain useful for implementation history, but older entries are point-in-time records and may be superseded by later entries.

## 1. Start here

1. Read the authoritative global rules: [wosafety0/Agents-global main/AGENTS.md](https://github.com/wosafety0/Agents-global/blob/main/AGENTS.md). Current retrieved version: 2.1.0-rc1 (Candidate).
2. Read the "Current integration state" section above before checking out any branch; the `dev/expedition-closed-loop-mvp` named in the original handoff is historical. The GitHub default branch main contains only .github; it is not the application source baseline.
3. Read this file, then [MVP_EXPEDITION_SINGLE_LOOP.md](MVP_EXPEDITION_SINGLE_LOOP.md) and [POI_PORT_RESPONSE_OBSERVER.md](POI_PORT_RESPONSE_OBSERVER.md).
4. For expedition settlement work, read [EXPEDITION_SETTLEMENT.md](game-rules/EXPEDITION_SETTLEMENT.md) before changing result handling, reward verification, or the UI settlement chain.
4. Before coding, run git status --short --branch and preserve any existing local modifications. Do not assume another checkout or worktree is clean.
5. Keep local credentials, cookies, game data, screenshots, logs containing private state, and machine-specific paths out of the repository.

## 2. Repository and CI baseline

- Repository visibility: **public**.
- Active code branch at the time of this handoff (historical, superseded): **dev/expedition-closed-loop-mvp**.
- All baseline SHA, CI run and test-count figures in this section describe 2026-11-03 only; the current integration head is listed in "Current integration state" above.
- Baseline commit: **1aba35893daa3974c55a6ac54aec2c9dcac8cccb** — “Block expedition dispatch on Taiha ships”.
- Latest verified CI for that commit: [run 41637134894](https://github.com/wosafety0/wosafety0clamp/actions/runs/41637134894), conclusion **success**. The test job passed JavaScript static tests, passive observer syntax check, Python compile, and Python unit tests.
- The project progress record reports **502 Python tests passed** for the Taiha-gate change. CI success is independently verified; the exact test count is recorded in the progress log.
- The default branch has not been changed, no PR has been created or merged as part of this handoff, and no collaborator permissions have been changed. Public read access does not grant push access; a new developer needs repository write permission to push.

## 3. Architecture map

| Area | Location | Responsibility |
|---|---|---|
| CDP transport and allowlist | src/kancollebot/adapters/poi/cdp_websocket.py | Loopback WebSocket, command allowlist, fixed read-only snapshot expression, input and passive events |
| Fleet snapshot | src/kancollebot/adapters/poi/cdp_fleet_state.py | Parse the fixed Poi Redux fleet snapshot |
| Poi freshness | src/kancollebot/adapters/poi/port_freshness.py, port_response_observer.py, background_port_snapshot_provider.py | Passive natural-response witness and freshness comparison |
| UI state and visual gates | src/kancollebot/adapters/poi/visual_state_gate.py, visual_fingerprint.py, visual_target.py | In-memory screenshot classification and bounded visual target localization |
| Semantic DOM targets | src/kancollebot/adapters/poi/cdp_semantic_targets.py | AX/DOM targeting for actual DOM nodes; not suitable for Canvas-only game controls |
| Fleet state and safety policy | src/kancollebot/adapters/poi/expedition_state.py, expedition_state_v2.py | Normalize ship/fleet state, supply and damage attributes |
| Scheduling and dispatch gates | src/kancollebot/expeditions/ | Fleet lifecycle, planner, durable intent/recovery, controller and supervisor |
| Unified runtime configuration | src/kancollebot/runtime_config.py | Single source for production Fleet 3 / mission 3, monitor fleet set, endpoints, timeouts, and runtime paths |
| Runtime CLI | src/kancollebot/cli.py | Canonical monitor and one-shot dispatch command router |
| One-shot UI implementation | tools/one_expedition_ui_loop.py | Guarded visible-UI preparation and one-shot dispatch transaction; direct script invocation is a compatibility shim |
| Fleet-wide supply | src/kancollebot/adapters/poi/full_supply.py, tools/full_supply_ui_action.py | Guarded per-fleet supply action and postcondition checks |
| Read-only monitor implementation | tools/run_continuous_expedition_monitor.py | Background freshness provider + Poi reader + read-only monitor; direct script invocation is a compatibility shim |
| Tests | tests/ | Offline unit and integration-contract tests |
| CI | .github/workflows/ci.yml | Offline validation; not a persistent game runtime |

## 4. Verified achievements

### Live Poi and UI evidence

- A natural, successful, non-cache /kcsapi/api_port/port response was captured by the passive CDP Network observer. A background provider was integrated with the actual Poi reader/MCP path; a later live run reached FRESHNESS_WITNESS_MATCHED after 31 polls / 35.55 seconds and compared three fleets. This is point-in-time freshness evidence, not a guarantee of permanent freshness.
- Live UI navigation HOME_PORT → SORTIE_MENU → EXPEDITION_LIST → HOME_PORT passed the two-frame visual state gates using ordinary UI navigation.
- Fleet-wide resupply was live-verified across the three unlocked fleets: 13/13 assigned ships reached their master maximum fuel and ammunition, with per-fleet resource deltas independently checked. One damaged ship remained damaged; supply does not repair.
- Fleet 2 and Fleet 3 ship-selection checkbox checked/unchecked states were individually calibrated and verified. These checkbox tests do not authorize supply or dispatch.
- The production UI driver has been exercised through pre-dispatch preparation and Home Port recovery. The irreversible expedition-start click was not sent in the latest recorded acceptance boundary.

### Offline implementation and tests

- Passive port witness, exact current-state matching, background observation, durable fleet state, dispatch-intent recovery, UI arbitration, visual state gates, and guarded supply logic have offline test coverage.
- Delayed supply verification was corrected to tolerate later store propagation within a bounded read window, without issuing a second click. Its focused regression tests passed.
- Pre-dispatch Taiha (大破) detection is implemented: HP at or below 25% is classified with integer arithmetic (api_nowhp * 4 <= api_maxhp). The dispatch controller blocks before durable intent creation and before UI input. The latest recorded full Python suite is 502/502; latest CI for the baseline commit passed.

## 5. Explicitly not verified / blocked

Do not describe the project as a completed automatic expedition loop.

- **No end-to-end live dispatch acceptance:** the expedition-start action has not been verified in the current production acceptance record. Fresh post-dispatch readback, return/result progression, reward collection, idle confirmation, and the complete repeat cycle are not accepted.
- **Taiha gate is offline-tested only:** no live Taiha snapshot or live dispatch-block attempt has been recorded. Healthy-ship replacement is deliberately deferred and is not implemented.
- **Unattended scheduler is not accepted:** the read-only monitor is separate from the one-shot UI runner. Do not infer that the continuous supervisor is a ready-to-run unattended game process.
- **Mission list discovery is incomplete:** the currently observed expedition page showed eight visible rows (from 01 through A1), but the list can scroll and later missions may be unlocked beyond the visible range. The visible eight rows are not the complete mission catalogue. No complete scroll-and-scan index has been verified.
- **Canvas controls are not AX targets:** a read-only inspection on both Home Port and Expedition Selection reported an unchanged AX tree (146 nodes / 117 valid). The game area appeared as an application leaf with no children; visible game buttons and expedition rows had no AX matches. This is operator-observed evidence from 2026-11-03, not a new independent run by this handoff author.
- **The DOM geometry chain is incomplete:** the current CDP allowlist contains Accessibility.getFullAXTree, DOM.pushNodesByBackendIdsToFrontend, and DOM.getBoxModel, but not DOM.enable or DOM.getDocument. Therefore the DOM node/BoxModel path is not end-to-end usable as currently configured. Even after fixing that for real DOM nodes, it will not create DOM targets for Canvas-rendered game controls.
- **Keep arbitrary JavaScript forbidden:** Runtime.evaluate must remain restricted to the exact fixed fleet-snapshot expression. Do not broaden it to solve DOM or Canvas targeting.
- **Resolution constraint:** the existing visual fingerprint and bounded visual-target code is calibrated to a 1200×720 screenshot; visual_target.py explicitly rejects other viewport dimensions. This is a current implementation constraint, not proof that Poi itself must always use that resolution.
- **Runtime configuration is centralized:** the production target is Fleet 3 / mission 3 in src/kancollebot/runtime_config.py. Fleet 2-specific helpers and tests retained in tools/one_expedition_ui_loop.py are legacy/test compatibility paths and are not invoked by the production CLI. Keep this distinction explicit.

## 6. Recommended next work

1. Keep the unified CLI and runtime defaults as the only production entry/configuration source; add tests whenever the target or defaults change.
2. Build a **read-only, scroll-aware expedition scanner**: capture visible rows, identify mission number/name, detect scroll progression, deduplicate across pages, and stop only after a verified end-of-list condition. Do not treat A1 or the current eight rows as the end.
3. Validate row identity and localization against the current Canvas screenshot. Recompute target location after every scroll; never reuse pre-scroll coordinates.
4. Keep AX/DOM targeting for genuine DOM nodes only. Decide separately whether the incomplete DOM protocol chain is worth enabling for shell controls; do not use it as a substitute for Canvas recognition.
5. Add live read-only verification for the Taiha gate before considering any dispatch attempt. Healthy-ship replacement remains a separate, later feature requiring explicit fleet-composition and safety tests.
6. Only after all state gates and the post-dispatch / return / reward / idle path are independently verified should the full expedition loop be considered for live acceptance.

## 7. Setup and validation

The project requires Python >=3.11. CI currently uses Python 3.12. The optional visual extra provides OpenCV and NumPy.

    py -3.12 -m venv .venv
    .\.venv\Scripts\Activate.ps1
    python -m pip install -e .
    # Optional, only for visual localization:
    python -m pip install -e ".[vision]"

    $env:PYTHONPATH = "src"
    python -m unittest discover -s tests -v
    node --test tests/static_decoder_extract.test.cjs tests/verify_cdp_snapshot.test.cjs
    node --check tools/hand_sandbox/poi_port_response_observer_20261029.js
    python -m compileall -q src tests tools
    git diff --check

CI success proves the checked offline workflow only. It does not connect to Poi, click the game, or verify live expedition behavior.

## 8. Non-negotiable safety contract

- CDP endpoints and WebSockets must remain credential-free loopback.
- Runtime.evaluate is only for the exact fixed read-only fleet snapshot expression.
- Passive Network observation may capture naturally occurring responses; it must not initiate or replay game requests.
- Game-state changes use normal visible UI only. No internal game functions or direct server API calls.
- Every state-changing click needs fresh preconditions and postconditions. Ambiguous input outcomes must not be blindly retried.
- Stale, incomplete, conflicting, or ambiguous evidence blocks the action.
- Never dispatch a fleet containing a Taiha ship. Replacement is not implemented; do not silently substitute ships.
- Keep screenshots in memory unless a specific diagnostic artifact is explicitly needed and safe to retain.

## 9. Handover checklist

- [ ] New developer has read the global rules and this handoff.
- [ ] New developer has write permission if they need to push (public read access is not sufficient).
- [ ] Local checkout is on dev/expedition-closed-loop-mvp and its working tree has been inspected.
- [ ] Baseline CI and offline tests have been rerun or the existing run has been reviewed.
- [ ] Fleet target mismatch is resolved before using the one-shot runner.
- [ ] No live dispatch or unattended operation is enabled before the remaining acceptance gates pass.

## 10. Outstanding GitHub pull requests and branches

Read-only inventory checked on 2026-11-03:

- There are **19 open pull requests** in the repository. No PR was merged or closed during this handoff.
- The active development branch has many related and historical development branches. No branch was deleted, force-moved, or bulk-renamed.
- PR [#5](https://github.com/wosafety0/wosafety0clamp/pull/5) is a draft with the current development branch as its head and dev/expedition-planner-mvp as its base. It contains 641 commits and 136 changed files; it is not a PR targeting main. Do not merge it as a routine cleanup step.
- PR [#27](https://github.com/wosafety0/wosafety0clamp/pull/27), [#26](https://github.com/wosafety0/wosafety0clamp/pull/26), and [#24](https://github.com/wosafety0/wosafety0clamp/pull/24) are open drafts targeting dev/expedition-closed-loop-mvp. Their recorded base SHAs predate the current handoff head. At the time of inspection, #27 and #26 reported mergeable=false; #24 reported mergeable=true. Review their diffs against the current base before deciding whether to rebase, cherry-pick, merge, or close.
- Additional open drafts and historical PRs remain (including #15, #17, #20, #22, #25). They overlap in Poi freshness, dispatch recovery, safety, and earlier integration work. Do not bulk-close or merge them without a per-PR diff and dependency review.

The purpose of this inventory is to prevent a new developer from mistaking an old draft, a stale base SHA, or a historical branch for the current integrated baseline. It is not a merge recommendation.

As of 2026-11-04 the local integration target is `dev/dispatch-observer-safety-current-mvp-20261103` (local `3bd949b`, remote `86f294c`); the local fast-forward updated no remote branch.
