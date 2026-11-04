# Developer Handoff

**Updated:** 2026-11-04  
**Repository:** wosafety0/wosafety0clamp  
**Working branch:** dev/expedition-closed-loop-mvp  
**Current code baseline:** 46c76b3e20a83302edc7cdee82789dfc2e497492

This document is the concise handoff entry point. The long design and progress records remain useful for implementation history, but older entries are point-in-time records and may be superseded by later entries.

## 1. Start here

1. Read the authoritative global rules: [wosafety0/Agents-global main/AGENTS.md](https://github.com/wosafety0/Agents-global/blob/main/AGENTS.md). Current retrieved version: 2.1.0-rc1 (Candidate).
2. Check out dev/expedition-closed-loop-mvp. The GitHub default branch main currently contains only .github; it is not the application source baseline.
3. Read this file, then [MVP_EXPEDITION_SINGLE_LOOP.md](MVP_EXPEDITION_SINGLE_LOOP.md) and [POI_PORT_RESPONSE_OBSERVER.md](POI_PORT_RESPONSE_OBSERVER.md).
4. Before coding, run git status --short --branch and preserve any existing local modifications. Do not assume another checkout or worktree is clean.
5. Keep local credentials, cookies, game data, screenshots, logs containing private state, and machine-specific paths out of the repository.

## 2. Repository and CI baseline

- Repository visibility: **public**.
- Active code branch: **dev/expedition-closed-loop-mvp**.
- Current code baseline: **46c76b3e20a83302edc7cdee82789dfc2e497492** — fail-closed reward claim authorization gate and current handoff/design documentation.
- Latest verified CI for this exact code baseline: [run 41769006678](https://github.com/wosafety0/wosafety0clamp/actions/runs/41769006678), conclusion **success**. The test job passed JavaScript static tests, passive observer syntax check, package/CLI smoke checks, Python compile, and the complete Python unit suite (**593 tests; 20 skipped; all remaining passed**).
- The default branch has not been changed, no PR has been created or merged as part of this handoff, and no collaborator permissions have been changed. Public read access does not grant push access; a new developer needs repository write permission to push.

## 3. Architecture map

| Area | Location | Responsibility |
|---|---|---|
| CDP transport and allowlist | src/kancollebot/adapters/poi/cdp_websocket.py | Loopback WebSocket, command allowlist, fixed read-only snapshot expression, input and passive events |
| Fleet snapshot | src/kancollebot/adapters/poi/cdp_fleet_state.py | Parse the fixed Poi Redux fleet snapshot |
| Poi freshness | src/kancollebot/adapters/poi/port_freshness.py, port_response_observer.py, background_port_snapshot_provider.py | Passive natural-response witness, exact state comparison, and condition-based waiting for a witness newer than a UI action boundary |
| UI state and visual gates | src/kancollebot/adapters/poi/visual_state_gate.py, visual_fingerprint.py, visual_target.py | In-memory screenshot classification and bounded visual target localization |
| Semantic DOM targets | src/kancollebot/adapters/poi/cdp_semantic_targets.py | AX/DOM targeting for actual DOM nodes; not suitable for Canvas-only game controls |
| Fleet state and safety policy | src/kancollebot/adapters/poi/expedition_state.py, expedition_state_v2.py | Normalize ship/fleet state, supply and damage attributes |
| Scheduling and dispatch gates | src/kancollebot/expeditions/ | Fleet lifecycle, planner, durable intent/recovery, controller and supervisor; controller waits for passive freshness after dispatch and Home Port navigation, and exposes a due-time return observer that verifies status 2 against the exact dispatched mission tuple; the supervisor invokes it for active fleets with verified Home Port before considering another dispatch |
| Mission catalogue scan aggregation | src/kancollebot/expeditions/mission_catalog_scan.py | Offline page aggregation, overlap deduplication, scroll-coverage and verified-bottom gates; no UI input |
| Mission catalogue visual adapter | src/kancollebot/expeditions/mission_catalog_vision.py | Bounded OpenCV matching of locally supplied mission-marker and scrollbar templates; requires explicit scrollbar-scale calibration; no screenshots written or UI input |
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
- A fail-closed reward claim authorization gate was added at `src/kancollebot/expeditions/reward_claim_gate.py`. It requires an explicitly approved template fingerprint/calibration ID, unique high-confidence in-frame target localization, two consecutive BONUS_ITEMS checks, and a frame-bound authorization. The default calibration allowlist is empty; it does not click and cannot be used until real Poi reward-button calibration is obtained.
- Pre-dispatch Taiha (大破) detection is implemented: HP at or below 25% is classified with integer arithmetic (api_nowhp * 4 <= api_maxhp). The dispatch controller blocks before durable intent creation and before UI input.
- The offline mission-catalogue scan accumulator is implemented. It deduplicates overlapping visible pages, rejects conflicting row identities and unsafe scroll progression, and only marks a scan complete when measured bottom geometry agrees with an independent visual bottom confirmation. Its output is not connected to dispatch.
- A read-only OpenCV visual adapter is implemented for bounded mission-marker and scrollbar-thumb template matching. Mocked-frame integration tests exercise screenshot decoding, row matching, scrollbar location, and ambiguity rejection. The adapter requires a manually verified proportional scrollbar model and remains disconnected from dispatch.
- The dispatch controller waits for the background passive observer to capture a port witness whose response-received and body-captured timestamps are both later than the dispatch-completion boundary, and repeats this wait after Home Port navigation. It then reads Poi and verifies the exact active mission tuple. The wait uses an in-process condition variable, not repeated MCP reads; timeout fails closed and does not retry UI input.
- Added `ContinuousDispatchController.wait_for_return()`: it is available only after the scheduler confirms both an active mission and verified Home Port. It refuses to observe before the mission's `final_time_ms`, waits for a natural port witness newer than that deadline, requires a fresh Poi Redux observation newer than the deadline, and confirms returned status 2 plus exact mission ID, return timestamp, reserved slot, and ordered ship roster before moving the durable scheduler to `RETURNED_PENDING_UI`.
- Tightened `visual_state_gate.py` settlement classification: `RETURN_RESULT` and `BONUS_ITEMS` now reject conflicting `sortie_menu_title.png` evidence in addition to their existing return-notice/home/result guards. Added regression tests for a bonus-title/return-notice collision and a result/sortie-title collision. GitHub Actions run 41767679742 passed **571 Python tests; 20 skipped**; all remaining Python tests, 17 Node tests, observer syntax, CLI smoke checks, and compile passed. This is offline classifier validation only; it does not calibrate or authorize any reward click.

## 5. Explicitly not verified / blocked

Do not describe the project as a completed automatic expedition loop.

- **No end-to-end live dispatch acceptance:** the expedition-start action has not been verified in the current production acceptance record. Fresh post-dispatch readback, return/result progression, reward collection, idle confirmation, and the complete repeat cycle are not accepted.
- **Return and settlement observation are offline-tested only:** the controller waits for a natural post-deadline port witness and confirms the exact returned mission tuple before `RETURNED_PENDING_UI`. A new read-only `SettlementStateObserver` validates structured two-frame evidence for `RETURN_RESULT` and `BONUS_ITEMS`; the supervisor advances only `RETURNED_PENDING_UI → RETURN_RESULT → BONUS_ITEMS`, and refuses to attribute a shared Poi screen when multiple fleets are pending. The production one-shot entry wires the observer to the existing two-frame visual verifier. GitHub Actions run 41768376887 passed **582 Python tests, 20 skipped**, plus all other workflow checks. No live return or settlement screen was observed; this integration is not live-verified and performs no settlement clicks. Reward collection remains blocked.
- **Settlement observer:** `src/kancollebot/expeditions/settlement_observer.py` validates the verifier's structured two-frame evidence and does not send input. `ContinuousExpeditionSupervisor` performs at most one phase transition per tick and pauses the shared UI queue throughout settlement. `BONUS_ITEMS` remains pending until a separately verified reward-claim action exists.
- **Reward claim authorization gate:** added `src/kancollebot/expeditions/reward_claim_gate.py`. It issues a non-executable authorization only for the exact `bonus_claim_button.png` asset with an explicitly approved live-calibration hash/ID, unique current-frame localization, confidence ≥0.96, score margin ≥0.10, in-viewport bounds, and two consecutive `BONUS_ITEMS` checks. Authorization is bound to frame ID and SHA-256 and becomes invalid if the frame changes. The default calibration allowlist is empty, so it refuses all reward targets until real Poi calibration is supplied. It exposes no click/execute operation. CI run 41768901495 passed **592 Python tests; 20 skipped**, plus all other workflow checks.
- **Taiha gate is offline-tested only:** no live Taiha snapshot or live dispatch-block attempt has been recorded. Healthy-ship replacement is deliberately deferred and is not implemented.
- **Unattended scheduler is not accepted:** the read-only monitor is separate from the one-shot UI runner. Do not infer that the continuous supervisor is a ready-to-run unattended game process.
- **Mission list discovery remains incomplete:** the offline page aggregator and OpenCV adapter are implemented, but no local mission-marker templates, calibrated visual profile, live Canvas row-recognition sequence, or verified scrollbar proportionality evidence is available in GitHub. `scrollbar_scale_verified` defaults to false, so screenshot scanning cannot produce complete-scan evidence until Poi calibration. The previously observed eight rows (01 through A1) are not the complete mission catalogue.
- **Post-action freshness waiting has offline test coverage only:** natural-response waiting is wired through the production reader/controller interfaces, but no live dispatch or post-navigation response-wait sequence has been performed. A timeout leaves the transaction blocked for manual reconciliation; it does not resend a click.
- **Canvas controls are not AX targets:** a read-only inspection on both Home Port and Expedition Selection reported an unchanged AX tree (146 nodes / 117 valid). The game area appeared as an application leaf with no children; visible game buttons and expedition rows had no AX matches. This is operator-observed evidence from 2026-11-03, not a new independent run by this handoff author.
- **The DOM geometry chain is incomplete:** the current CDP allowlist contains Accessibility.getFullAXTree, DOM.pushNodesByBackendIdsToFrontend, and DOM.getBoxModel, but not DOM.enable or DOM.getDocument. Therefore the DOM node/BoxModel path is not end-to-end usable as currently configured. Even after fixing that for real DOM nodes, it will not create DOM targets for Canvas-rendered game controls.
- **Keep arbitrary JavaScript forbidden:** Runtime.evaluate must remain restricted to the exact fixed fleet-snapshot expression. Do not broaden it to solve DOM or Canvas targeting.
- **Resolution constraint:** the existing visual fingerprint and bounded visual-target code is calibrated to a 1200×720 screenshot; visual_target.py explicitly rejects other viewport dimensions. This is a current implementation constraint, not proof that Poi itself must always use that resolution.
- **Runtime configuration is centralized:** the production target is Fleet 3 / mission 3 in src/kancollebot/runtime_config.py. Fleet 2-specific helpers and tests retained in tools/one_expedition_ui_loop.py are legacy/test compatibility paths and are not invoked by the production CLI. Keep this distinction explicit.

## 6. Recommended next work

1. Keep the unified CLI and runtime defaults as the only production entry/configuration source; add tests whenever the target or defaults change.
2. When Poi is available, calibrate the visual adapter using operator-local mission number crops and a scrollbar-thumb crop. Verify the 1200×720 list/track regions, marker thresholds, scrollbar proportionality, and the bottom tolerance with real top/middle/bottom captures. Keep those assets local unless separately approved for sharing.
3. Validate row identity and localization against each current screenshot. Recompute every row target after every scroll; never reuse pre-scroll coordinates. Feed page observations into the offline accumulator and do not treat A1 or the current eight rows as the end.
4. After Poi access is restored and visual calibration is available, perform a separately authorized single live dispatch acceptance. Confirm the post-dispatch and post-navigation witnesses, then exercise `wait_for_return()` only after the recorded return deadline; verify the status-2 tuple and durable `RETURNED_PENDING_UI` transition. No retry is allowed on ambiguous clicks or missing freshness evidence.
5. Keep the supervisor paused at BONUS_ITEMS until real Poi reward-button calibration exists. The offline authorization gate is implemented, but there is no click executor. After real captures, validate the target template and calibration, then implement a separate executor with immediate same-frame revalidation and independent post-click state confirmation. Do not invent a reward click target from the title marker alone.
6. Keep AX/DOM targeting for genuine DOM nodes only. Decide separately whether the incomplete DOM protocol chain is worth enabling for shell controls; do not use it as a substitute for Canvas recognition.
7. Add live read-only verification for the Taiha gate before considering any dispatch attempt. Healthy-ship replacement remains a separate, later feature requiring explicit fleet-composition and safety tests.
8. Only after all state gates and the post-dispatch / return / reward / idle path are independently verified should the full expedition loop be considered for live acceptance.

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


## Documentation pointer

- [Expedition list scan and aggregation contract](EXPEDITION_LIST_SCAN.md)
- [Expedition list visual recognition contract](EXPEDITION_LIST_VISUAL_RECOGNITION.md)

## 2026-11-04 — Reward authorization dual-frame provenance hardening

- **IMPLEMENTED:** `RewardClaimTarget` now requires two distinct, strictly increasing state-frame IDs and two distinct SHA-256 image fingerprints. The target's own frame ID and fingerprint must exactly match the second verified `BONUS_ITEMS` frame.
- **FAIL-CLOSED:** repeated frame IDs, repeated image hashes, out-of-order frame IDs, or a target localized on any frame other than the second verified frame are rejected before authorization.
- **TESTS ADDED:** regression cases cover reused/out-of-order frame IDs, duplicate image fingerprints, and target-to-second-frame mismatch.
- **LIMITS:** this validates caller-supplied provenance metadata; it does not itself capture screenshots or prove that a caller hashed the actual raw Poi frame. The production capture pipeline must supply IDs and hashes directly from the same in-memory frame objects. No click executor or live calibration was added.

## 2026-11-04 — Exact-byte in-memory Poi frame capture primitive

- **IMPLEMENTED:** `CdpInput.capture_frame()` returns an immutable `CapturedFrame` containing a process-monotonic frame ID, monotonic capture timestamp, exact PNG bytes, and SHA-256 computed internally from those bytes. It rejects invalid base64 and non-PNG payloads; the original screenshot string API remains available for existing callers.
- **TESTS ADDED:** verify exact-byte digest, monotonically increasing IDs, base64 round-trip, non-PNG rejection, and fabricated-digest rejection.
- **LIMITS (updated by the later settlement integration below):** the settlement verifier now consumes captured frames, but reward target localization still does not consume the retained second frame. End-to-end reward provenance and authorization are therefore incomplete. No click path was added.

## 2026-11-04 — Settlement verifier consumes exact captured Poi frames

- **IMPLEMENTED:** `_require_ui_state` can now capture immutable `CapturedFrame` objects for settlement verification. The settlement-specific verifier uses that path and returns the exact two matching frame objects alongside the state evidence. Dispatch checkpoint verification retains its prior screenshot/accessibility behavior and does not require frame objects.
- **IMPLEMENTED:** `SettlementStateObserver` now rejects missing/non-`CapturedFrame` evidence, duplicate or non-increasing frame IDs, duplicate image fingerprints, and non-increasing monotonic capture timestamps. `SettlementObservation` retains both frame objects for a later target-localization step.
- **CORRECTED:** PNG signature validation and its fixtures now use actual PNG signature bytes rather than double-escaped text. This closes a false-positive in the earlier capture tests.
- **TESTED:** GitHub Actions run [41770859932](https://github.com/wosafety0/wosafety0clamp/actions/runs/41770859932) passed 601 Python tests (20 skipped), plus workflow syntax, CLI smoke, and compile checks.
- **LIMITS:** reward target localization still does not consume the retained second `CapturedFrame`; `RewardClaimTarget` still accepts caller-supplied metadata. The chain is improved through settlement observation, but not yet end-to-end at reward target detection. No reward click executor, live calibration, or game interaction was enabled.
