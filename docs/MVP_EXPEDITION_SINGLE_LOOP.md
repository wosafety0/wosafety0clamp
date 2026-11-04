# Continuous Dual-Fleet Expedition Scheduler — Design Contract

Current status (2026-11-04): see [HANDOFF.md](HANDOFF.md) for the authoritative handover summary, verified evidence, current limitations, and next steps. This file is the detailed design contract and chronological implementation record; dated sections below describe the state at the time they were written and may be superseded by later entries.

Branch: dev/expedition-closed-loop-mvp

## 2026-11-04 - Fail-closed expedition return observation (offline)

- **IMPLEMENTED:** Added `ContinuousDispatchController.wait_for_return(fleet_id)`. It is callable only for a fleet whose dispatch tuple is confirmed and whose two-frame HOME_PORT gate is already verified. It refuses to observe before the exact `final_time_ms` deadline.
- **FRESHNESS / IDENTITY GATES:** After the deadline, it waits on the already-armed passive observer for a natural port witness newer than the deadline, reads Poi Redux only after that witness, requires the observation to be source-fresh and newer than the deadline, and compares mission status 2, mission ID, return timestamp, reserved slot, and ordered ship roster with the originally dispatched tuple.
- **STATE / RECOVERY:** Only an exact match advances the durable scheduler from `EXPEDITION_ACTIVE` to `RETURNED_PENDING_UI`. Early calls do not read or wait. Missing witnesses, stale state, wrong status, or tuple mismatch fail closed and block the fleet; no UI input, game request, or retry is performed.
- **SUPERVISOR INTEGRATION:** Each supervisor tick now checks active fleets with verified Home Port after the monitor tick and before selecting another dispatch. The controller's due-time guard makes pre-deadline checks read-free; a confirmed return transitions to the existing settlement-pending pause, so the shared UI is not reassigned before result/reward settlement.
- **TESTED:** Added six controller tests and supervisor integration regressions for successful return confirmation, early-call rejection, missing-witness blocking, roster mismatch, unverified Poi state, incorrect mission status, and pausing dispatch after a due return. GitHub Actions run [41767117657](https://github.com/wosafety0/wosafety0clamp/actions/runs/41767117657) passed the full suite: **569 tests, 20 skipped, all remaining passed**, plus Node static tests, observer syntax, CLI smoke checks, and Python compile.
- **LIMITS / NOT VERIFIED:** This is an offline-tested controller capability, not a live return observation. No expedition was dispatched or awaited. RETURN_RESULT / BONUS_ITEMS progression, reward-claim control and confirmation, resupply integration, and the complete expedition loop remain unimplemented or unverified.
- **NEXT:** When Poi access is available, separately authorize one live dispatch, then verify this return observer after the actual mission deadline. Continue only with independently calibrated result/reward UI targets; the existing bonus-title marker alone is not click authorization.


## 2026-11-02 - SORTIE_MENU visual checkpoint

- Added the dedicated `SORTIE_MENU` template classifier using the live `出撃選択` title crop. The 205×56 PNG is bundled at `src/kancollebot/adapters/poi/assets/sortie_menu_title.png`; an operator-provided template directory takes precedence, with the bundled asset as fallback.
- The classifier requires a strong title match and rejects conflicting Home Port, result, or reward evidence. Weak and ambiguous matches remain `UNKNOWN`.
- Offline visual-state tests passed **14/14**. Live navigation verified `HOME_PORT → SORTIE_MENU → EXPEDITION_LIST → HOME_PORT`; every state passed two consecutive Ooi fingerprint/template frames. Three ordinary navigation clicks were sent; no mission was selected, no fleet was dispatched or resupplied, and no screenshot files were written.
- This closes the `SORTIE_MENU` recognition gap and verifies the expedition-list navigation path only. It does not establish a fresh natural `api_port/port` witness, dispatch authorization, or production unattended readiness. The production startup guard remains mandatory.

## 2026-11-02 - Live natural Poi port freshness witness

- **Capture:** while the reviewed passive CDP Network observer was armed, normal UI navigation traversed `HOME_PORT → SORTIE_MENU → EXPEDITION_LIST → HOME_PORT`. The observer captured a naturally occurring successful `/kcsapi/api_port/port` response (HTTP 200, non-cache witness) and the in-memory Poi Redux comparison returned `FRESHNESS_WITNESS_MATCHED` at `2026-11-02T04:03:48.635886Z` (12:03:48 Beijing time), covering three fleets.
- **Readiness:** the same verified observation classified Fleet 1 as READY (still prohibited from dispatch), Fleet 2 as `blocked_damaged_ships`, and Fleet 3 as `needs_resupply` due to fuel/ammunition. No fleet was dispatch-authorized; no repair, resupply, or dispatch was attempted.
- **Safety:** the observer used only CDP Network events and did not evaluate page JavaScript, initiate game requests, or send input. The three clicks were ordinary navigation only. All screenshots stayed in memory; no screenshot files were written.
- **Initial integration gap:** the diagnostic's synchronous provider waits up to 130 seconds. The background provider described below removes that blocking behavior as a reusable adapter, but it has not yet been wired into the production scheduler composition; cached, expired, or state-mismatched witnesses remain non-authoritative.

## Background passive freshness provider — offline implementation

- Added `src/kancollebot/adapters/poi/background_port_snapshot_provider.py`. It lazily starts a daemon observer worker, returns the latest in-memory witness without blocking `read_fleets()`, and rearms the one-shot passive observer after each capture. It does not initiate game requests or send UI input.
- The reader remains responsible for the existing response timestamp, cache-flag, and exact fleet/ship equality checks. An old or mismatched cached witness therefore remains `FRESHNESS_UNVERIFIED`; the provider does not extend its validity.
- Focused background-provider tests passed **6/6** and cancellable-observer tests **10/10**; the full Python suite passed **396/396**; Node static tests passed **19/19**; compileall and `git diff --check` passed.
- GitHub Actions push CI run **41387568457** for commit `2c110bc4cb1e83ed80ebd333889de2b40322f81a` completed successfully; its `test` job and all listed steps passed.
- **Integration boundary:** this adapter is wired into the read-only monitor runtime described below, but not the production dispatch/supervisor composition. `PassivePortResponseObserver.capture_cancellable()` allows `close()` to terminate an in-flight Node observer child; generic injected observers exposing only `capture()` cannot be interrupted. Do not treat this utility as production unattended readiness or remove the production runner guard.

## Live background witness reader integration

- **Integration path:** instantiated the actual `BackgroundPortSnapshotProvider` with the reviewed `PassivePortResponseObserver` and injected it into `PoiExpeditionReader`, backed by the local Poi MCP read-only client. The reader polled while the passive observer independently awaited a natural response; each poll returned without waiting for the observer timeout.
- **Live result:** after normal UI navigation `HOME_PORT → SORTIE_MENU → EXPEDITION_LIST → HOME_PORT`, the reader reached `FRESHNESS_WITNESS_MATCHED` after **31 polls / 35.55 seconds**, at `2026-11-02T04:20:54.811656Z`. Three fleet records were source-verified and the read-only assessment returned Fleet 1 READY (still prohibited), Fleet 2 `blocked_damaged_ships`, and Fleet 3 `needs_resupply`.
- **UI/safety:** all three UI clicks were ordinary navigation; each page state passed its two-frame gate and the sequence ended at HOME_PORT. The reader reported `game_actions_enabled: false`. No mission selection, dispatch, repair, resupply, reward claim, game API request, or screenshot file write occurred.
- **Boundary:** this verifies the background provider with the actual Poi reader/MCP path, not a persistent scheduler process or production dispatch driver. The production runner remains fail-closed.

## Read-only continuous monitor runtime

- Added `tools/run_continuous_expedition_monitor.py` as a read-only runtime composition: `BackgroundPortSnapshotProvider` → `PoiExpeditionReader` → `ContinuousExpeditionMonitor`, with shared SQLite fleet-state, dispatch-intent, and event stores. Startup conservatively recovers or reports unresolved dispatch intents.
- Fleet roles are fixed to Fleet 2 and Fleet 3 as dispatch candidates, Fleet 1 read-only, and Fleet 4 deferred. Mission composition requirements remain Fleet 2 → mission 1 and Fleet 3 → mission 3.
- The runtime does not instantiate `ContinuousDispatchController`, `ContinuousExpeditionSupervisor`, or any UI driver. It does not open a game CDP input session and cannot click, dispatch, collect rewards, or resupply. The CLI explicitly reports `game_actions_enabled: false`.
- Offline runtime-composition tests passed **4/4**. Full Python suite passed **400/400**; Node static tests **19/19**; compileall, observer `node --check`, and `git diff --check` passed.
- A live read-only run of five monitor ticks without additional UI navigation returned `FRESHNESS_UNVERIFIED` on all five ticks. No game input or state-changing action occurred. This confirms the monitor remains fail-closed when a new natural witness is absent; the separate 31-poll real-reader integration above remains the positive live freshness evidence.
- This is a persistent read-only monitor, not the production unattended scheduler. Production dispatch, settlement/reward progression, resupply, and the production UI driver remain blocked by their existing guards.

## Authoritative operating scope

- Fleet 2 and Fleet 3 are independent dispatch candidates.
- Fleet 1 is read-only and must never be dispatched.
- Fleet 4 is deferred until the game unlocks it; its absence must not block Fleet 2 / Fleet 3 scheduling.
- Mission assignments remain explicit configuration, currently Fleet 2 → mission 1 and Fleet 3 → mission 3. The scheduler must not silently change formations, missions, repair policy, or supply policy.
- The design supports additional configured fleets without making Fleet 4 a startup dependency.

## Fleet lifecycle

Each dispatchable fleet has an independent lifecycle:

`IDLE → READY → DISPATCHING → EXPEDITION_ACTIVE → RETURNED_PENDING_UI → RETURN_RESULT → BONUS_ITEMS → RESUPPLY_REQUIRED → READY`

Any uncertain observation or failed invariant transitions that fleet to `BLOCKED`; no automatic retry of an ambiguous action is allowed.

## Mandatory dispatch-to-Home-Port invariant

After the UI reports dispatch success and Poi confirms the requested active mission, the client must immediately return to `HOME_PORT`. Before waiting for the expedition timer, the controller must:

1. Confirm the visible UI state is `HOME_PORT` using the accepted two-frame state gate.
2. Re-read Poi fleet state.
3. Compare the exact active mission tuple: status, mission ID, final-time timestamp, reserved slot, and ordered ship membership.
4. Permit timer monitoring only when both the Home Port state and unchanged tuple are confirmed.

A failure or mismatch blocks that fleet and stops further UI actions. The client must never remain on the expedition list while the timer runs.

## Shared UI arbitration

- Fleet lifecycle state is independent; Poi UI and CDP input are shared.
- Every visible UI operation must pass through one process-wide serialized arbiter.
- One Poi state reader and one CDP input session are shared by the runtime.
- A fleet waiting for its return timer does not hold the UI arbiter; the other fleet may be processed.
- Result settlement, reward handling, resupply, and dispatch operations are serialized. After each settlement the client returns to Home Port and rescans all configured fleets.

## Safety and recovery

- Never dispatch Fleet 1 or require Fleet 4 before unlock.
- Never infer dispatch acceptance from a click return; require a newer Poi observation.
- Never wait after dispatch until the Home Port + unchanged mission tuple postcondition passes.
- Never collect a reward until the expected return/result state and BONUS_ITEMS state each pass the production two-frame gate.
- Never use notification-template coordinates as click authorization. A located visual element is evidence of location only; it does not prove that the region is a valid control or safe progression target.
- Never send or replay game API/server requests. Use only normal visible game UI actions.
- Unknown, stale, conflicting, or incomplete evidence fails closed.

## Current implementation boundary

- Implemented: `src/kancollebot/expeditions/continuous_scheduler.py` provides the pure fleet state machine, mission tuple equality gate, Fleet 1 read-only / Fleet 4 deferred role separation, and a shared-window mutex.
- Implemented: `src/kancollebot/expeditions/continuous_supervisor.py` coordinates one monitor tick and sequential READY-fleet dispatch in configured order. It pauses for BLOCKED, unresolved DISPATCHING, pending settlement, or missing Home Port proof; each dispatch must reach EXPEDITION_ACTIVE with verified Home Port before the next fleet is attempted. It is a single-tick offline coordinator, not a persistent process or production UI driver.
- Updated: the passive port-response observer emits the compact freshness witness to stdout without writing desktop files when `KCB_POI_FRESHNESS_WITNESS_STDOUT=1`; standalone snapshot-export mode retains its explicit file output.
- Implemented: `tests/test_continuous_scheduler.py` covers independent Fleet 2 / Fleet 3 lifecycles, the mandatory post-dispatch Home Port gate, mission tuple stability, settlement order, reward confirmation, resupply, blocked state, and serialized UI access.
- Corrected: `visual_target_gate.py` returns a `LocatedVisualTarget`; visual localization no longer grants CDP click authority.
- Implemented: `src/kancollebot/expeditions/fleet_state_store.py` provides SQLite persistence for fleet lifecycle states. Scheduler transitions are persisted before the in-memory state advances; restart restores active, returned, blocked, and interrupted DISPATCHING states.
- Implemented (verification pending): `src/kancollebot/expeditions/dispatch_recovery.py` persists the intended mission, baseline idle observation, ordered ship roster, preparation time, and UI-action completion marker before/around dispatch. On restart it reconciles only a fresh source-verified Poi Redux observation that positively matches the intended active mission; the Home Port postcondition remains unverified and timer waiting stays forbidden.
- Fail-closed recovery: fresh idle state, already-returned state, mission/roster mismatch, or orphaned intent blocks the fleet and never authorizes automatic retry. Already-returned recovery requires a separate manual UI settlement path.
- Planner snapshots now retain the `api_mission[3]` reserved slot so reconciliation can compare it rather than silently defaulting it.
- The existing action journal still covers dispatch attempts separately from lifecycle state persistence.
- Not integrated: the coordinator is not yet connected to the visible UI controller or production runner; no game action is enabled by this module.
- Verified offline: controller, monitor, and Poi normalization focused tests (43/43); regression across the intended archive scope (377/377, excluding the separate unarchived supervisor prototype); Python compileall; JavaScript static tests (17/17); passive observer syntax; and git diff --check. NumPy 2.5.4 and OpenCV 5.0.0 were available. The controller and monitor share the PoiExpeditionReader.read_fleets contract, explicit per-fleet mission requirements, and append-only SQLite event logging. This is offline verification only; no production UI driver or unattended loop is connected.
- Live UI checkpoint (2026-11-02): two consecutive in-memory CDP screenshots of the current game WebView classified as `RETURN_RESULT` using the existing template classifier (success 0.9983, failure 0.5651); no screenshot files were written and no input was sent.
- Freshness boundary (same session): the passive Network observer was armed for 120 seconds while the UI remained on `RETURN_RESULT`; it observed no Kancolle server response and no `/kcsapi/api_port/port` event. The Poi Redux snapshot therefore remained `FRESHNESS_UNVERIFIED`, and all Fleet 1/2/3 planning assessments were suppressed. Do not treat this cached snapshot as action authority.
- Production guard: a live two-frame `BONUS_ITEMS` checkpoint has now passed (title-template score 0.9794 in both frames), followed by two-frame `HOME_PORT` verification (score 0.9982). Keep the production fail-closed guard until production integration and required end-to-end acceptance pass.

---

# Single Expedition MVP — Execution Contract

Status: IN_PROGRESS (2026-11-03; production monitor/controller/supervisor composition implemented for fixed Fleet 3 / mission 3; mission selection and Fleet 2/Fleet 3 pre-dispatch visual gates verified live; production driver preparation returned to Home Port; actual expedition-start click, post-dispatch fresh Poi readback, return/result/reward/idle, resupply, and unattended loop remain NOT_VERIFIED)
Branch: dev/expedition-closed-loop-mvp

## One acceptance criterion
One fixed fleet performs one fixed expedition, with observed transitions:
ready -> dispatched -> completed -> reward collected -> ready.

## Strict scope
1. Read the fixed fleet's expedition state from Poi.
2. Identify readiness and expedition-in-progress/completed states using observed fields.
3. Identify the shortest feasible action mechanism for one dispatch and one reward collection.
4. Implement guarded single-dispatch and reward collection only after separate user authorization for live game actions.
5. Immediately after the active mission tuple is verified, navigate back to HOME_PORT through the visible UI, require two consecutive in-memory HOME_PORT fingerprints, and re-read Poi to confirm the same mission remains active. Never leave the client parked on the expedition list while the timer runs.
6. Observe return to ready; stop. No continuous re-dispatch is required.

## Stop conditions
Unknown or stale state, mismatched fleet/expedition, ambiguous completion, unavailable action interface, or any unexpected response -> STOP without game actions.

## Current evidence and blockers
- Historical Poi read-only evidence: info.fleets (3 fleets) and info.ships (91 ships), positive fleet-ship references matched.
- Live local Poi MCP bridge was reachable and reported a ready Redux store. `poi_describe(info.fleets)` confirmed `api_mission` is part of the fleet records; one read-only `poi_get(info.fleets)` returned three cached values `[0,0,0,0]`. This is a current cache observation only: no source-freshness witness or before/after expedition transition was established.
- Existing Python MCP client is read-only; poi_get is allowlisted; poi_hensei_save is explicitly excluded.
- Fleet/ship reference consistency is not expedition-state recognition.
- CDP direct WebSocket connection to the embedded KanColle webview target and Page.captureScreenshot: VERIFIED. On 2026-11-03, HOME_PORT, SORTIE_MENU, EXPEDITION_LIST, mission-3 selection, FLEET_2_SELECTED, FLEET_3_SELECTED, and return to HOME_PORT passed two-consecutive-frame in-memory visual gates. Fleet 2 and Fleet 3 descriptors now crop only their respective tab labels; each positive state rejects the opposite fleet state. The production driver's prepare_dispatch() path completed the same navigation sequence and returned to Home Port. Screenshot bytes remained in memory; no screenshot files were written. This verifies pre-dispatch navigation only, not the irreversible start click.
- Production dispatch is now wired through ContinuousDispatchController and ContinuousExpeditionSupervisor for the fixed Fleet 3 / mission 3 target. The irreversible EXPEDITION_START click has NOT been executed through the production runner; post-dispatch natural freshness and Poi mission-tuple readback remain NOT_VERIFIED. Reward collection is not yet accepted end-to-end.
- End-to-end live expedition loop: NOT_VERIFIED.
- Current verification: 487 Python unit tests passed; 19 Node tests passed; Python compileall, production CLI help, and git diff --check passed.
- This round used only visible UI navigation clicks for pre-dispatch calibration. No expedition-start click, reward collection, resupply, or resource-consuming action was executed.

## Immediate next implementation task
The production UI controller integration is now in place. The next live acceptance is one explicitly authorized Fleet 3 / mission 3 dispatch through tools/one_expedition_ui_loop.py --authorize-ui-actions, and only when a current natural api_port/port freshness witness, Fleet 3 readiness/full-supply/composition checks, and all visual/viewport gates pass. The runner stops after fresh active-mission readback and Home Port proof; timer, return/result, reward collection, idle confirmation, and resupply remain the next P0 closure work. Do not call game APIs, construct/send game-server requests, or use page/PIXI injection. Any stale or ambiguous state must stop without retry.

## Explicitly out of scope
AI model comparisons, generalized Bot reverse engineering, multi-fleet scheduling, optimal expeditions, battle automation, generic frameworks, extensive security investigations, or GitHub Actions.

## Workflow
Cloud GitHub first; no local checkout required. Read before write. Record verified commits and unresolved blockers; never claim unrun tests passed.

## 2026-10-26 — Read-only evidence checkpoint
- GitHub code search on the repository default-branch index for `api_mission`, `expedition`, and `poi_get` returned no matches. This search is not exhaustive for this development branch.
- Mainline MCP client reviewed directly: `poi_get` is allowlisted; no dispatch or reward-collection tool is implemented.
- No field-level observed before/after expedition evidence was established in this round.
- Decision: do not infer expedition state from unverified field semantics. Next obtain minimal passive observations of the same fixed fleet before dispatch, after manual dispatch, and after manual reward collection. Capture only relevant status fields, redact identifiers/secrets, and do not issue game actions.
- Test execution: NOT_RUN. Game actions: DISABLED. No local checkout required.

## 2026-10-26 — Batched local verification workflow
- User reports Poi is logged in.
- Default workflow: continue cloud-side development and progress logging on `dev/expedition-closed-loop-mvp`; do not require a local repository unless a test specifically needs the Windows/Poi runtime.
- Batch local checks only when they are necessary and ready; prefer passive/read-only observation. After the batch, return to the GitHub mainline, record actual outcomes, and verify remote readback.
- Keep local test results, GitHub commits, and real-game validation as separate statuses. Never report a test as passed without execution evidence.
- MVP scope remains unchanged: one fixed fleet, one fixed expedition, one complete loop. Poi login alone does not authorize game actions.

## 2026-10-26 — Existing implementation found; avoid duplicate work
- Mainline already contains `PoiExpeditionReader.read_fleets()` and `normalize_poi_fleets()`; do not create a second reader or another generic state-shape layer.
- Existing reader fetches `info.fleets`, `info.ships`, and referenced ship-master fuel/ammo fields, then emits normalized fleet snapshots.
- The normalizer currently assumes `api_mission[0]` is a status and `api_mission[1]` is a mission ID when status > 0. The progress log explicitly records that this slot interpretation is not yet authoritatively confirmed.
- Historical sanitized observations show fleet 3 with positive values in the first three `api_mission` slots and zero in the fourth, but values/semantics were not confirmed; another report observed a different inferred mission ID. Do not use this to authorize an action.
- Therefore the immediate blocker is not absence of a reader: it is confirming mission-field semantics and current-state transitions using one bounded, passive live observation batch.
- Batch plan: read health/initialize, then only `info.fleets` and `sortie` (if supported), report sanitized key/type/slot-position shapes and numeric status/mission fields only if they are needed; do not print personal IDs, names, raw payloads, or secrets. No dispatch/write tools.
- After the local batch, return to this GitHub branch, record the exact outcome, and continue the single-expedition MVP. Tests remain NOT_RUN unless actually executed.

## 2026-10-27 — Semantic target discovery implementation
- Added a read-only CDP accessibility-tree locator that resolves an exact accessible name and role to current viewport coordinates through backend DOM node IDs and box geometry.
- Added only read-only accessibility/DOM geometry methods to the CDP allowlist. Mouse input remains the only input method; arbitrary JavaScript evaluation remains blocked.
- Ambiguous names, ignored nodes, missing DOM IDs, malformed geometry, and off-viewport targets fail closed. The locator itself sends no click.
- GitHub Actions will verify the offline unit tests. This implementation does not prove the live game UI exposes useful accessible names, and it does not implement expedition dispatch or result collection.

## 2026-10-27 — Local accessibility scan prepared
- Added a bounded read-only diagnostic that connects to the selected local renderer, reads the accessibility tree, and prints only unique role/name pairs matching expedition-related terms.
- The diagnostic sends no mouse input, performs no JavaScript evaluation, and makes no game-state changes. Output is capped and excludes page URL, account data, and unrelated accessible labels.
- This tool is prepared in the cloud and must not be described as having run on the user's machine. Its purpose is to determine whether the current UI exposes stable semantic labels without manually annotating screenshots or coordinates.
- If the live UI exposes no matching accessible labels, do not invent labels or fall back to a large manual coordinate/template inventory. Reassess the shortest supported client-mediated route based on evidence.

## 2026-10-30 — Bounded stop-loss status
- The CDP/PIXI injection and manual-coordinate interaction routes are stopped by the project's explicit stop-loss gate.
- Static public references support the `api_mission` shape `[state, mission_id, absolute Unix-ms final time, reserved]` and document states 0=idle, 1=active, 2=returned, 3=recalled. The v2 normalizer now converts the third slot from Unix milliseconds to timezone-aware UTC `return_at` for supported states 1 and 2; offline regression tests cover conversion and fail-closed handling of unrepresentable values. State 3 remains rejected. This verifies offline normalization only; runtime edge cases and the action interface remain unverified, and no game action is enabled.
- Read-only inventory did not find a saved current-server `api_mission` transition capture. Existing tests are synthetic, not Taiwan-server runtime verification.
- The installed forwarder is passive only; the Python Poi adapter is read-only. Dispatch and reward-collection mechanisms remain NOT_VERIFIED.
- Resume condition: obtain one manually operated, passively observed expedition's sanitized before/after `api_mission` evidence, and independently identify a low-risk, authorized action mechanism. Until then, do not implement new state/action code or claim the single-expedition loop is operational. No game input or server request was sent during this review.

## 2026-10-30 — Evidence capture acceptance protocol (planned; not yet run)
This section defines the minimum evidence needed to unblock implementation; it is a protocol definition, not evidence that a live capture has occurred.
1. **Before action:** with the user operating the game manually, capture one fresh natural `api_port/port` response and its matching Poi Redux read. Record observation timestamps, freshness age, fixed fleet index, and only the sanitized `api_mission` slot values needed for comparison. Do not save raw payloads, account identifiers, ship names, or secrets.
2. **Operator event marker:** the user records which manual event occurred (dispatch or reward collection) and its approximate time. The observer itself must not click, refresh, invoke game functions, or issue game-server requests.
3. **After action:** wait for a new natural port response, not merely a repeated cache read. Require a new source timestamp/freshness witness and record the same fleet's sanitized `api_mission` values using the same schema.
4. **Compare, do not infer:** report the exact before/after slot values and whether the tuple changed. Do not map a numeric state to a meaning, infer success from a timer alone, or treat a different fleet/cache snapshot as a transition. If freshness, fleet identity, or event ordering is ambiguous, classify the capture as INCONCLUSIVE and repeat only when a naturally occurring response is available.
5. **Separate action gate:** even a well-supported state transition validates observation semantics only. Before any bot-initiated action can be enabled, independently identify a client-mediated, low-risk action interface, document its authorization and fail-closed behavior, and test it separately under explicit user authorization. No direct game-server calls.
6. **Acceptance record:** retain only the sanitized event label, before/after tuples, source/freshness timestamps, result (MATCHED / INCONCLUSIVE), and observer version. Keep the capture status, action-interface status, offline-test status, and MVP status as separate fields.
**Unblock rule:** implementation may resume only after one unambiguous manually operated before/after transition is captured and the separate action-interface prerequisite is satisfied. Until then, mission-state expansion and action integration remain BLOCKED; tests not actually run must remain NOT_RUN.

## 2026-10-30 — Live UI dispatch proof and revised gate
- **Verified live action:** after the prior returned mission was collected through the visible UI, Poi Redux showed all three fleets idle. Fleet 2 (four ships) was selected for expedition 3, `警備任務`.
- **Explicit authorization:** the user authorized normal game UI actions except direct packet sending. The dispatch used one left-button press/release through the local CDP input domain against the visible game page. No game API method or direct game-server request was initiated by the automation.
- **Before/after evidence:** fleet 2 changed from `api_mission=[0,0,0,0]` to `[1,3,1793354078676,0]`; fleets 1 and 3 remained idle. The game displayed `遠征に向かいました`. The observed third slot is `2026-10-30T09:54:38.676Z` in Unix milliseconds.
- **Gate decision:** the action interface is now verified for this specific visible UI click path, but it is not yet integrated into the project Bot and viewport/target guards are not yet implemented. The new mission has not returned; the full same-mission loop and automatic re-dispatch remain pending. Do not mark the MVP complete.
- **Next acceptance:** observe fleet 2 mission 3 return, collect its reward via the normal UI, read back `api_mission=[0,0,0,0]`, then trigger a new UI-mediated dispatch from Bot-owned guarded automation and verify the new active state. No direct game-server packet/API route is permitted.

## 2026-10-30 — Fixed-fleet controller implementation checkpoint
- Added a fixed-expression read-only Poi fleet-state reader and a bounded controller for fleet 2 / mission 3. The controller is guarded by an explicit `--authorize-ui-actions` flag and exact starting mission timestamp; it uses single-click UI input and verifies each state transition before proceeding.
- Offline verification: **175/175 Python unit tests passed**, plus `compileall` and `git diff --check`. The read-only fleet reader also passed a live Poi runtime check against the active mission tuple.
- **Live loop status remains IN_PROGRESS.** The same mission's return, reward collection, and re-dispatch have not yet been verified by this controller. Its return-confirmation point remains provisional until observed on the actual return screen.

## 2026-10-30 — Return-time refresh gate correction (live verification pending)
- The previous live run remained at mission status 1 after the recorded return timestamp passed. The controller was changed to trigger a visible UI refresh after the scheduled return time and then require the same mission tuple in returned status before one reward-confirmation click.
- Verification so far: controller syntax check passed; fixed-expression fleet-reader tests passed 6/6. The changed live sequence, reward coordinate, idle transition, and re-dispatch remain NOT_VERIFIED. MVP remains IN_PROGRESS.

## 2026-10-30 — Passive Poi freshness observer runtime check
- Ran the existing read-only `tools/verify_poi_freshness_bridge.py` against the local Poi runtime from worktree HEAD `ee13c36475e415912427adbe32a471d27f36b899` (`dev/cdp-screenshot-transport-20261030`; this worktree is 16 commits behind the target branch's configured upstream).
- Result: exit code 0; `FRESHNESS_WITNESS_MATCHED`; `source_freshness_verified=true`; observer status `captured`; three fleets observed; no game actions enabled. The sanitized planning summary contained one `completed_awaiting_reward` assessment and two `ready_to_dispatch` assessments; all had `dispatch_eligible=false`.
- This confirms the passive freshness-witness path worked in this runtime check. Because the run used a different local worktree revision and the report intentionally omitted fleet identifiers and mission tuples, it does **not** prove that fleet 2 / mission 3 is the returned fleet, and it does not authorize reward collection or re-dispatch.
- No game input, refresh, game API call, source edit, or local commit was performed. Clipboard copy was unavailable in the DC session; the diagnostic itself completed successfully.
- MVP remains `IN_PROGRESS`; exact target-fleet state, normal-UI reward collection, idle readback, and guarded re-dispatch remain unverified.

## 2026-10-30 — Dispatch preconditions and post-dispatch return-to-port gate
- **Dispatch precondition — supplies:** before dispatching any fleet, inspect fuel and ammunition for every ship in the target fleet. If either is not full, use the normal in-game UI to replenish, then re-read and verify the supplies are full. If replenishment cannot be confirmed, fail closed and do not dispatch.
- **Dispatch action — normal UI only:** select the intended fleet and expedition through the visible game UI. Do not use direct game-server requests or game API calls to dispatch.
- **Dispatch verification:** after the UI action, read the fleet state and verify the target fleet's `api_mission` is active and its mission ID matches the requested expedition. A click or screenshot alone is not sufficient evidence of success.
- **Post-dispatch navigation:** after confirming dispatch, leave the expedition screen and return to the home port through the normal UI. Verify the game is back at the home port before considering the dispatch workflow complete.
- **Acceptance gate:** a dispatch is workflow-complete only when supply preconditions are verified, the correct active mission tuple is observed, and the UI has returned to the home port. If any check is missing or ambiguous, report the run as incomplete rather than successful.
- **Evidence status:** these are user-confirmed operational requirements added to the MVP acceptance criteria. They do not claim that supply-check automation or post-dispatch navigation has already been implemented or independently live-tested.

## 2026-10-30 — Simulated timer trigger and home-port state observation
- **Trigger:** recorded as `timer_expired` with `trigger_mode=simulated`; this was a test marker, not a real timer wake-up.
- **Visible UI sequence:** through the local CDP input domain, a click at the corrected visible Sortie-button coordinate entered the sortie selection screen; a subsequent click at the top-left navigation coordinate returned the game to the home port. Screenshots were captured after both actions. No direct game API call or game-server request was issued.
- **Read-only Poi observation:** the Poi MCP health endpoint reported `ok` and `storeReady=true`. A sanitized read of `info.fleets` and `info.ships` succeeded at `2026-10-30T15:57:46.052Z` (UTC). Fleet 2 reported `api_mission=[2,3,1793375434202,0]`; fleet 3 reported `[2,1,1793375753312,0]`; fleet 1 reported `[0,0,0,0]`.
- **Evidence boundary:** this confirms the UI navigation sequence and that Poi's current Redux store exposed the recorded tuples after navigation. The observer did not independently match a newly received natural `api_port/port` response to this snapshot, so source freshness and causal attribution to the home-port navigation are **not independently verified**. Treat this as a useful simulated-trigger/UI-navigation observation, not full refresh-freshness acceptance.
- **Actions intentionally omitted:** no reward was collected and no fleet was re-dispatched. Supply checks, reward-to-idle transition, and the complete automated loop remain unverified; MVP remains `IN_PROGRESS`.

## 2026-10-31 — Fleet 2 expedition 1 dispatch via visible UI
- **Reward collection:** the pending expedition-result screens for fleet 2 and fleet 3 were advanced through the visible game UI to completion; the home-port view subsequently showed the normal resource/header area. This was a manual UI interaction, not a Bot-owned reward-collection implementation.
- **Supply gate:** selected fleet 2 in the normal supply screen and executed the fleet-wide supply action. A subsequent sanitized Poi snapshot showed fleet 2 idle before dispatch and its four ships at fuel/ammunition values `15/20`, `15/15`, `15/20`, and `15/20`; the supply-screen bars were visibly full before dispatch.
- **Dispatch:** selected expedition ID 01, “練習航海” (15-minute expedition), selected fleet 2, and activated the visible “遠征開始!” button. A read-only Poi snapshot at `2026-10-30T16:06:52.510Z` UTC reported fleet 2 `api_mission=[1,1,1793377300383,0]`; a second snapshot at `2026-10-30T16:07:50.721Z` UTC reported the same tuple. This is evidence of an active mission state with mission ID 1 after the UI dispatch. No direct game API call or game-server request was issued.
- **Return-to-port verification:** after the active tuple was verified, the top-left navigation click was sent through the visible UI. The immediate screenshot showed a black/loading transition, but a later screenshot confirmed the home-port screen was restored. The dispatch, active mission tuple, and return-to-home-port UI checks are therefore observed for this manual run.
- **Scope boundary:** this validates one manually UI-dispatched fleet 2 / expedition 1 action and a matching read-only state tuple. It does not validate automated Bot dispatch, timer-triggered wake-up, passive source-freshness correlation, or the full closed loop. MVP remains `IN_PROGRESS`.

## 2026-10-31 — Fleet 3 Northern Sea expedition 17 dispatch via visible UI
- **Intervening return/reward:** while navigating from the home port, the visible UI presented fleet 2's returned expedition 1 reward screen. The reward screen was advanced with the normal UI and the game returned to the home port; no direct game API call or game-server request was issued.
- **Supply gate:** selected fleet 3 in the normal supply UI and used fleet-wide replenishment. A subsequent read-only Poi snapshot confirmed the four original fleet-3 destroyers at fuel/ammunition (15/15) each before formation changes.
- **Mission and formation preconditions:** the Northern Sea list showed expedition ID 17, “敵地偵察作戦”, duration 45 minutes, requiring a six-ship expedition. Fleet 3 originally had four destroyers. Through the visible formation UI, moved Jintsuu from fleet 1 to fleet 3 and made her flagship, then added the unassigned destroyer Inazuma to slot 6. Fleet 3 now has six ships: Jintsuu, Kisaragi, Mutsuki, Satsuki, Nagatsuki, and Inazuma. Fleet 1 was left with five ships. No ship was scrapped or remodeled.
- **Supply recheck:** after the composition change, a read-only Poi snapshot showed fleet 3 ship fuel/ammunition values of (25/25), (15/15), (15/15), (15/15), (15/15), and (15/20). These matched the ships' full values visible in the UI; no warning markers were shown in the fleet selection screen.
- **Dispatch and state evidence:** selected fleet 3 and activated the visible “遠征開始!” button. The UI then showed fleet 3 on expedition 17 at 1% progress with about 44 minutes remaining. Poi's read-only snapshot at `2026-10-30T16:33:42.454Z` reported fleet 3 `api_mission=[1,17,1793380662749,0]`; the tuple was observed with state 1 and mission ID 17, and its timestamp slot corresponds to `2026-10-30T17:17:42.749Z`.
- **Return-to-port verification:** after the active mission tuple was verified, a visible top-left navigation click returned the UI to the home port. A later screenshot showed the normal home-port menu.
- **Safety and scope:** all game actions used the visible UI through local CDP mouse input; state checks used the read-only Poi MCP bridge. No direct game API or game-server request was made. This verifies a manual fleet-3 / expedition-17 dispatch and home-port navigation, not Bot-owned automated dispatch, timer wake-up, source-freshness correlation, or the complete closed loop. MVP remains `IN_PROGRESS`.

## 2026-10-31 — Fleet 3 expedition 17 read-only follow-up confirmation
- **User-reported outcome:** the user confirms the fleet-3 / Northern Sea expedition 17 dispatch succeeded. This is consistent with the previously recorded visible-UI dispatch evidence and active mission tuple.
- **Fresh read-only follow-up:** Poi snapshot captured at `2026-10-30T16:35:37.776Z` (2026-10-31 00:35:37.776 Beijing time) reported fleet 3 `api_mission=[1,17,1793380662749,0]`. An earlier read at `2026-10-30T16:34:30.935Z` reported the same tuple. The third slot is Unix milliseconds corresponding to `2026-10-30T17:17:42.749Z`; this is the recorded expedition return timestamp, not evidence that the return has occurred.
- **UI follow-up:** a fresh screenshot `diagnostics/captures/dc-current-before.png` showed the game at the normal home-port menu at approximately 00:35 Beijing time. The snapshot observer recorded `poiHealth=ok`, three fleets, and 92 ship instances. No game input, refresh, dispatch, or supply action was performed during this follow-up.
- **Acceptance boundary:** expedition 17 is confirmed active in the read-back state and the UI is at the home port. This follow-up does not independently re-prove the earlier supply action's before/after state, nor does it establish a fresh natural `api_port/port` response correlated to the snapshot. Do not re-dispatch fleet 3 while this mission is active. The Bot-owned automated loop and full return/reward/idle/re-dispatch acceptance remain pending; MVP remains `IN_PROGRESS`.

## 2026-10-31 — Fleet 2 expedition 1 replenishment and script-driven dispatch
- **Script-driven supply:** used local Node.js scripts with client-mediated CDP mouse input through the visible game UI (no direct game API call or game-server request). Selected fleet 2 in the supply screen and activated fleet-wide replenishment.
- **Supply before/after:** the pre-action read-only Poi snapshot showed fleet 2 idle with four ships' fuel/ammunition values `11/20`, `11/15`, `11/20`, and `11/20`. The post-supply snapshot at `2026-10-30T16:40:08.236Z` showed `15/20`, `15/15`, `15/20`, and `15/20`; fuel was replenished and ammunition remained full.
- **Dispatch:** selected expedition ID 01, `練習航海` (15 minutes), selected fleet 2, and activated the visible `遠征開始!` button using a Node.js CDP-input script. Poi read-back at `2026-10-30T16:45:45.875Z` reported fleet 2 `api_mission=[1,1,1793379572868,0]`, confirming active status 1 and expedition ID 1. The four fleet-2 ships remained at the replenished fuel/ammunition values.
- **Return to home port:** after the active tuple was confirmed, a script clicked the visible top-left home-port navigation control. A fresh screenshot `diagnostics/captures/dc-current-before.png` at approximately 00:47 Beijing time showed the normal home-port menu. A subsequent read-only Poi snapshot at `2026-10-30T16:47:12.845Z` still reported fleet 2 `[1,1,1793379572868,0]` and fleet 3 `[1,17,1793380662749,0]`.
- **Acceptance boundary:** this verifies the requested manual script-driven replenishment, dispatch of fleet 2 on expedition 1, active mission read-back, and return to the home-port UI. The scripts are local diagnostic/workflow files and were not added to the repository by this record. Bot-owned guarded automation, timer wake-up, natural source-freshness correlation, and the full return/reward/idle/re-dispatch loop remain pending; MVP remains `IN_PROGRESS`.

## 2026-10-31 — Runtime screenshot policy and performance requirement (implementation pending)
- **User requirement:** normal Bot execution must not save a screenshot to local disk for each UI action. Per-step PNG capture, base64 decoding, and file writes are not acceptable in the production workflow.
- **Required design:** screenshots are disabled by default in normal runs. The normal path uses lightweight state reads and explicit transition checks; it keeps the existing safety gates for fleet supply, target expedition, active mission tuple, and return to home port. CDP should be reused across a whole workflow, and fixed sleeps should be replaced where safe by bounded condition polling.
- **Diagnostics:** screenshot capture is opt-in for troubleshooting or failure-only diagnostics, with bounded file count/retention. No routine per-click image files. If visual evidence is not needed, do not call `Page.captureScreenshot` at all.
- **Timing requirement:** user authorizes cancelling an expedition through normal game UI if needed for testing; the 15-minute in-game duration is not a performance constraint for script optimization. Do not use direct game API calls or game-server requests.
- **Verification status:** this is a recorded acceptance requirement and implementation plan, **not** evidence that scripts have already been changed or benchmarked. Implementation, runtime verification that normal execution creates no screenshots, and before/after timing measurements remain pending. The MVP remains IN_PROGRESS.

## 2026-10-31 — Removed routine local screenshot writes from expedition UI step scripts
- **Implemented locally:** removed `Page.captureScreenshot`, PNG output-path declarations, `fs.writeFileSync`, and screenshot-path result fields from these nine local action scripts: `fleet2_expedition1_workflow.js`, `fleet2_supply_select.js`, `fleet2_supply_execute.js`, `fleet2_return_sortie_selection.js`, `fleet2_select_expedition_mode.js`, `dc_ui_click_mission.js`, `dc_ui_confirm_last.js`, `dc_ui_start_expedition17_fleet3.js`, and `dc_return_homeport_click.js`.
- **Static verification:** Node.js `--check` passed for all 9/9 modified scripts. A follow-up source scan found no screenshot capture, PNG path, screenshot result field, or local file-write code in these scripts.
- **Scope boundary:** these are local workflow/diagnostic files, not repository-tracked Bot source. No game UI actions were run during this code change. Existing screenshots were not deleted. The check establishes absence of routine screenshot writes in the nine named scripts only; it does not establish that every script in the diagnostics directory or the production Bot is screenshot-free.
- **Still pending:** consolidate the multi-step workflow into one reused CDP session, replace safe fixed sleeps with bounded condition polling, execute a controlled runtime test confirming no new screenshot files are created, and measure before/after timings. No speedup is claimed yet; MVP remains `IN_PROGRESS`.

## 2026-10-31 — User-defined end-to-end expedition loop
- **User-defined sequence:** home-port idle → read fleet state → dispatch → wait for timer → click the Sortie (出击) menu → click the top-left Home Port control to refresh game data → collect expedition reward → replenish supplies → return/verify Home Port → repeat from idle.
- **State gates:** at startup, read the actual target fleet mission tuple and supplies before acting. Never dispatch a fleet whose mission is already active or whose state is stale/ambiguous. At timer expiry, the timer is only a wake-up signal: navigate through the visible Sortie and Home Port UI, then require a fresh state observation before deciding whether the expedition has returned. Collect only when the target fleet is positively identified as completed/awaiting reward. After collection, verify the fleet returns to ready/idle; replenish only as needed and verify supplies before any next dispatch.
- **UI semantics:** “click Sortie” means the visible Sortie navigation/menu action in this workflow, not an unrequested combat launch. All game actions remain normal visible-UI input; no direct game API or game-server requests.
- **Runtime/performance:** no routine local screenshots. Reuse one CDP connection for a multi-step segment where practical; use bounded condition-based waits and explicit timeouts instead of fixed sleeps where safe. State reads and event logs are preferred over screenshots.
- **Acceptance path:** `home_port_idle → state_read → dispatch_verified → timer_wake → sortie_menu → home_port_refresh → fresh_state_verified → reward_collected → idle_verified → supplies_verified → home_port_verified`. Any ambiguous or stale state fails closed without collection or dispatch.
- **Verification status:** this records the user's intended workflow and its proposed guardrails; it is not evidence that the full Bot-owned loop has been implemented or runtime-tested. MVP remains `IN_PROGRESS`.

## 2026-10-31 — Expedition completion network-delay buffer
- **Required behavior:** the expedition timer is an earliest-wakeup signal, not proof of server-side completion. After timer expiry, apply a configurable grace delay before the first UI refresh/check to allow network/server settlement and client synchronization.
- **Bounded retry:** after the grace delay, follow the visible UI sequence (Sortie menu → top-left Home Port refresh) and read fresh fleet state. If the target fleet still appears active or the result is stale/ambiguous, wait for a configurable retry interval and repeat the refresh/state check, up to a configured maximum elapsed time or attempt count.
- **Safety:** do not repeatedly click dispatch/collect as a retry strategy. Collect only after the target fleet is positively confirmed complete and awaiting reward. If the deadline is exceeded, pause the loop and log a timeout; do not guess.
- **Tuning:** record timer-expiry, first-check, confirmation and retry timestamps in lightweight event logs (no routine screenshots). Start with a conservative configurable grace period and retry interval, then tune using observed delays; do not claim adaptive tuning until implemented and measured.
- **Status:** requirement/design archived only; implementation and network-delay runtime tests remain pending. MVP remains `IN_PROGRESS`.


## 2026-10-31 — Fleet 2 / expedition 3 full-loop preflight
- **Successful read-only live snapshot:** at `2026-10-30T17:07:23.901Z` (2026-10-31 01:07:23.901 Beijing time), Poi health was `ok`, the store was readable, and 3 fleets / 92 ship instances were present. Fleet 2 reported `api_mission=[2,1,1793379572868,0]`; this is a returned result for expedition 1, not an idle fleet and not the requested expedition 3. Its four ships' current fuel/ammunition values were `15/20`, `15/15`, `15/20`, and `15/20`. Fleet 3 remained on mission 17 and was not touched.
- **In-memory visual preflight:** a transient CDP screenshot was decoded and template-matched in memory only; no screenshot file was written. At the 1200x720 viewport, `home_sortie_menu.png` matched at score `0.99815` at `(227,342)`, positively identifying the current home-port menu. No game input was sent during this preflight.
- **UI state-gate finding:** the CDP accessibility tree returned 110 nodes but no expedition/result/home-port labels from the game canvas; it mostly exposed the surrounding DMM page. The current `tools/one_expedition_ui_loop.py` only accepts a pre-existing active fleet-2 / mission-3 tuple, so it is not applicable to this observed startup state and must not be run with a guessed timestamp.
- **Acceptance boundary:** the full loop has **not started**. No reward was collected, no supply or dispatch input was sent, and fleet 3 was left untouched. The live preflight successfully established the exact startup mismatch and a no-disk visual-state identification path. Next, the single-entry runner must safely resolve the already-returned mission-1 reward, dispatch fleet 2 on mission 3, wait with the configured grace delay, refresh/read state, collect the correct result, supply if needed, and verify the home-port idle state. Visual checks must remain in-memory; routine screenshot files are prohibited. MVP remains `IN_PROGRESS`.


## 2026-10-31 — Offline baseline verification for the live-loop worktree
- **Verified success:** on the dedicated detached worktree at `7f81e5e90c783f1cddf62e2ada0db52605318ec0`, `python -m unittest discover -s tests` passed all 180 tests; `node --test tests/static_decoder_extract.test.cjs tests/verify_cdp_snapshot.test.cjs` passed all 17 tests; and `python -m compileall -q src tests` passed.
- The Python test runner printed expected usage/error text from negative CLI-argument tests; the suite exit code was 0 and the final summary was `Ran 180 tests ... OK`.
- The installed environment does not have `pytest`; the repository's authoritative CI uses standard-library `unittest`, which ran successfully. Clipboard copy was unavailable in the remote PowerShell session; this does not affect test execution.
- **Live-action boundary unchanged:** tests are offline baseline evidence only. The current accessible-text gate cannot classify the game canvas, and the existing runner requires a pre-existing active mission-3 timestamp. No live click was sent; no local screenshot file was created. Full-loop acceptance remains pending and MVP remains `IN_PROGRESS`.


## 2026-10-31 — Home-port recovery and log-only runtime output
- **Bounded recovery:** if the current UI state is uncertain, the runner may click the visible top-left Home Port control up to 3 times. Log each attempt, wait, and post-click state observation. A click alone is not proof of recovery; continue only after the expected Home Port state is confirmed. Never retry indefinitely.
- **Fail-closed boundary:** if recovery cannot confirm Home Port, stop before reward collection, supply, or dispatch. Record the last known target-fleet mission summary and the stop reason.
- **Runtime file policy:** the only permitted local file output during a run is the designated append-only log. Normal execution must not call screenshot APIs or write PNGs, images, temporary files, debug dumps, duplicate snapshots, or separate reports.
- **Auditable actions:** log every action and state transition with UTC time, run ID, action, target label/coordinates where relevant, attempt number, precondition, outcome, wait duration, postcondition, and error/stop reason. Record only the minimal fleet/mission fields needed to explain decisions; never log credentials, cookies, tokens, or full unfiltered game state.
- **Implementation status (updated 2026-10-31):** `tools/one_expedition_ui_loop.py` now has an append-only JSONL audit log, UTC timestamps/run IDs, bounded Home Port recovery attempts, an idle-to-dispatch start, completion readback after the Sortie/Home Port refresh path, and a final idle-state gate. Static source checks and a remote Python AST parse passed; this is an **unverified implementation**, not live acceptance. The current accessibility gate may be unable to classify canvas-only screens; the reward/result screen sequence and coordinate mapping remain unverified, and automatic supply is not implemented. Do not run unattended until these blockers are resolved.


## 2026-10-31 — In-memory visual state gate prototype
- **Implemented in the dedicated local worktree:** added `src/kancollebot/adapters/poi/visual_state_gate.py` and `tests/test_visual_state_gate.py`. The classifier uses template matching from a CDP screenshot payload in memory, writes no image files, and returns `UNKNOWN` when matches are weak or ambiguous. Added an optional `vision` dependency extra (`opencv-python-headless` and `numpy`).
- **Offline verification:** all 186 Python unit tests passed; `compileall` passed; `git diff --check` passed; the two Node.js static suites passed 17/17.
- **Live read-only verification:** the existing controller's UI-state gate was exercised against the actual game WebView. The accessibility tree returned `UNKNOWN`; the in-memory template fallback uniquely classified `HOME_PORT`, and three consecutive captures 0.4 seconds apart also classified `HOME_PORT`. The best home Sortie-menu template score was approximately `0.998`. No game input was sent and no screenshot was written to disk.
- **Limitations:** this live check verifies only the current Home Port classification. Return-result/bonus classification has offline unit coverage but has not been validated on live screens. The single-entry controller still requires a pre-existing active fleet-2 / expedition-3 timestamp and has not been adapted to start from the currently observed idle state or to verify the supply UI; it was **not** run against the live game. The full closed loop remains NOT_STARTED / IN_PROGRESS.


## 2026-10-31 — Return/bonus label templates and refreshed live preflight
- **Implemented and pushed:** the in-memory visual classifier now also recognizes two existing local Japanese label crops by filename suffix: the expedition-return notice (`*202405.png`) and bonus title (`*202556.png`). Template bytes are decoded in memory, including Unicode paths; no screenshot is written. This improves classification coverage but does not by itself authorize any UI click.
- **Verification:** the full offline suite remains 186/186 Python tests passing, Python compile checks and `git diff --check` pass, and the Node.js static suites pass 17/17. A fresh live read-only check classified the current screen as `HOME_PORT` with a Sortie-menu match score of about `0.9982`; no input was sent and no screenshot file was created.
- **Fresh Poi state at `2026-10-30T17:28:07.647Z`:** fleet 2 was idle `[0,0,0,0]`; its four ships had fuel `11/15` each and ammunition at full values `20/20, 15/15, 20/20, 20/20`. Fleet 3 was also idle `[0,0,0,0]`. Poi health was `ok` and `storeReady=true`.
- **Remaining live acceptance gate:** supply-screen recognition and positive pre-dispatch identification of mission 3 are not yet verified by the current templates. The one-entry controller has not been run against the game; no supply, dispatch, refresh, or reward-collection action was sent in this preflight. Full-loop acceptance remains pending and MVP remains `IN_PROGRESS`.


## 2026-10-31 — Implemented configurable post-expiry grace delay
- **Implemented and pushed:** the one-entry controller now accepts `--grace-delay-seconds` (default 10 seconds, bounded 0–300). If the scheduled return time is reached while Poi still reports the same mission as active, it waits once for the grace interval, reads state again, and then hands control to the existing visible Sortie → Home Port refresh and bounded completion checks. It does not reset or repeat the grace delay on each poll.
- **Automated tests:** added three unit tests covering the one-time grace delay, an already-returned mission skipping the delay, and a mismatched mission failing closed. Full verification passed: 189 Python unit tests, Python compilation, `git diff --check`, and 17 Node.js static tests.
- **Remote archive:** controller and test source were pushed to `dev/expedition-closed-loop-mvp` at commit `9de1a446b063f5a34f5f482dc7acfc44eebbdcc2`; the GitHub file API readback confirms the controller exposes the grace option and emits the grace events.
- **Acceptance boundary:** this is offline behavior verification plus source-level archive verification. The full live loop has not been started; supply verification and positive mission-3 selection before the irreversible dispatch click remain open gates. MVP remains `IN_PROGRESS`.


## 2026-10-31 — Navigation-only calibration and safe return to Home Port
- **Successful UI navigation calibration:** used visible UI navigation only to inspect the sortie/expedition path, then returned via the visible Home Port icon at approximately `(50, 50)`. The final screen was reclassified as `HOME_PORT` after moving the pointer off the menu and dismissing hover overlays; the best `home_sortie_menu.png` score was `0.9982`.
- **Post-calibration live state at `2026-10-30T17:39:29.840Z`:** Poi health `ok`, store ready, fleet 2 and fleet 3 both idle `[0,0,0,0]`. This confirms no expedition was started and no fleet mission state changed during calibration.
- No supply, expedition dispatch, refresh, or reward-collection action was performed. No local screenshot file was written; image data used for visual inspection remained in memory only.
- **Controller correction needed:** the Home Port click coordinate should be updated from the stale `(52, 90)` to the live-verified `(50, 50)`, and visual state checks should move the pointer to a neutral area before capturing to avoid hover overlays. Mission-3 selection and supply-screen state recognition remain unverified; the full live loop is still pending.


## 2026-10-31 — Home Port state-gate hardening
- **Implemented and pushed** at `c4467dc83d0fd1e184258fedcf3ac70a0600445c`: the Home Port click target is now the live-verified visible icon coordinate `(50, 50)`; the expedition-mode target was aligned with the existing recorded UI action at `(1105, 179)`; visual fallback moves the pointer to a neutral area and waits briefly so hover tooltips do not obscure state markers.
- The visual classifier now requires both the Sortie-menu match and a second Home Port navigation marker, while rejecting strong expedition-list, dispatch-button, result, or bonus markers. Ambiguous screens fail closed.
- **Verification:** 191 Python unit tests passed, Python compile checks passed, `git diff --check` passed, Node.js static tests passed 17/17, and the live read-only Home Port gate passed at `2026-10-30T17:42:42.209Z` with Sortie-menu score `0.9982` and navigation-marker score `1.0`. GitHub readback verified the controller and test updates.
- **Still not full acceptance:** the UI-only calibration did not dispatch an expedition or collect rewards. Supply-screen recognition and positive identification of mission 3 before the irreversible start click remain unresolved. Last confirmed fleet snapshot remains fleet 2 and fleet 3 idle; the last supply values read for fleet 2 were fuel `11/15` for each of four ships with ammunition full. Full-loop acceptance remains pending.


## 2026-10-31 — Dynamic in-memory Home Port baseline
- **Implemented and pushed** at `9a64bd7d8fb0219d43103ffd02e3b2f596622c71`: because the older fixed Sortie template can match hover/overlay content rather than the live button, the controller now captures a transient Home Port reference in memory and compares the stable left-menu region after navigation. The reference is never written to disk. A neutral pointer move clears hover overlays before visual checks.
- Startup establishes the reference only when the top navigation marker is strong and expedition-list, dispatch, result, and bonus markers are not strong. Later Home Port checks require a high baseline similarity plus no competing state marker; otherwise the controller fails closed.
- **Verification:** 192 Python unit tests passed; Python compile checks, `git diff --check`, and Node.js static tests (17/17) passed. A live read-only test captured the reference and immediately rechecked Home Port successfully with `home_reference_similarity=0.9802`, navigation marker `1.0`, and strongest competing marker `0.6276`. No game click was sent during this test and no screenshot file was created. GitHub readback verified the visual gate, controller, and tests.
- **Full-loop status unchanged:** the one-shot runner still lacks a positively verified supply-screen gate and mission-3 pre-dispatch identity check. No expedition-3 dispatch, supply action, or reward collection has been performed. MVP remains `IN_PROGRESS`.


## 2026-10-31 — Explicit browser-level UI recovery capability
- **Implemented and pushed** at `265cf7cdbe0106c829123cf80a02c6ebb87142f5`: the CDP allowlist now permits only the explicit browser-level `Page.reload` recovery method in addition to its existing read-only inspection and mouse-input methods. Arbitrary `Page.navigate`, network enablement, and game-server methods remain blocked.
- **Verification:** 194 Python unit tests passed, Python compile checks and `git diff --check` passed; GitHub readback confirms the allowlist and its tests. No reload has been invoked by this change itself.
- **Use limitation:** reload is for restoring the client UI only. After reload, the controller must re-establish the live Home Port baseline and re-read fleet state before any game action. Full expedition-3 acceptance remains pending.


## 2026-10-31 — Fail-closed dispatch checkpoint contract

- **Implemented:** `tools/one_expedition_ui_loop.py::_dispatch` now requires a checkpoint-verifier callback before it can issue any of the six dispatch clicks. Each step declares an expected state before and after the click; a verifier exception stops the sequence immediately. If no verifier is supplied, the controller emits `dispatch_blocked` with `clicks_sent=0` and raises before reading or clicking.
- **Important limitation:** the current production `run()` does not supply a verifier. This is deliberate: the repository currently cannot reliably identify the mission-selected and fleet-2-selected canvas states without adding manually labeled templates or another recognition provider. The new state labels describe required checkpoints; they are not claims that those states have already been recognized on the live client.
- **Verification:** 197 Python unit tests passed, including tests for zero clicks when the verifier is absent, stopping after a failed post-click checkpoint, and the six-step callback contract with a mocked verifier. `compileall` and `git diff --check` passed. The mocked verifier tests validate control flow only; no live UI input was sent.
- **Acceptance status:** safety hardening is tested; live dispatch and the complete expedition loop remain unverified and blocked until a real per-step verifier is available.


## 2026-11-01 — Return-result and bonus-item template checkpoint implementation

- **Local template assets recovered:** `D:\AI_Project\Project_KancolleBot\kancolle_templates` contains success/failure result templates (521×473 / 482×414), a Japanese return-notice crop (220×49), and a Japanese bonus-title crop (221×69). These assets are local and are not tracked by this repository; callers must explicitly provide their directory.
- **Mismatched asset excluded:** `result_next_button.png` is an 81×495 vertical FRIEND FLEET AREA strip, not the reward-page next button. It is no longer loaded or accepted as a BONUS_ITEMS marker. The verified Japanese `bonus_title` crop is currently the only positive bonus-state marker.
- **Template-only state path fixed:** `_require_ui_state` previously could not evaluate RETURN_RESULT or BONUS_ITEMS because those states are absent from the Ooi fingerprint JSON and no screenshot payload was created for them. It now falls back directly to the configured template directory and requires two consecutive matching in-memory frames. Known conflicting states, missing directories, and unknown frames fail closed.
- **Configuration:** both `one_expedition_ui_loop.py` and `verify_ooi_ui_state_gate.py` accept `--template-dir`; the path is validated and passed through `KANCOLLEBOT_TEMPLATE_DIR`. No machine-specific absolute path is hard-coded in the repository.
- **Offline verification:** Python unittest **263 tests passed**; compileall and `git diff --check` passed. Synthetic in-memory positive controls classified success result, failure result, return-notice crop as RETURN_RESULT and bonus-title crop as BONUS_ITEMS (all exact-template scores 1.0). These controls validate template loading/matching only, not real game-screen accuracy.
- **Live negative verification:** the production checkpoint gate recognized the current Ooi HOME_PORT screen twice (fingerprint scores 0.9990 / 0.9990). When RETURN_RESULT and BONUS_ITEMS were expected against that same live HOME_PORT screen, the template-only path correctly classified HOME_PORT and returned FAIL_CLOSED. All screenshots remained in memory; no clicks or navigation were sent.
- **Current game state:** Poi reports all three fleets idle. Fleet 2 has four ships, each at fuel 11/15; ammunition is 20/20, 15/15, 20/20, and 20/20. No fleet is currently returning, so positive live RETURN_RESULT/BONUS_ITEMS verification cannot be obtained from the current screen.
- **Production remains blocked:** the runner's startup fail-closed gate is unchanged. No supply action, dispatch, reward collection, or resource-consuming action was performed. Full live return/reward transitions and end-to-end acceptance remain pending.
- **Local environment:** the ignored worktree `.venv` was provisioned with the declared optional vision extra (`opencv-python-headless 5.0.0.93`, `numpy 2.5.4`); this is local environment state, not a repository dependency change.

## Injected-port continuous dispatch controller

- The controller consumes one PoiExpeditionReader-compatible state reader through read_fleets(), one shared visible-UI driver/CDP session, one shared UiOperationArbiter, one ContinuousExpeditionScheduler, and one DurableDispatchCoordinator.
- The controller requires a durable event sink. SqliteDispatchEventLog provides append-only dispatch_events records and can share the same SQLite database file as fleet state and dispatch intents.
- Mission requirements are explicit and must cover every and only dispatchable fleet. The current configuration is Fleet 2 → mission 1 (minimum two ships, flagship level 1) and Fleet 3 → mission 3 (minimum three ships, flagship level 3).
- Poi normalization retains ordered ship levels and master ship types. Missing or incomplete composition metadata fails closed; the controller will not dispatch based only on fuel/ammunition readiness.
- Dispatch transaction: read a fresh source-verified idle baseline; check supply and mission composition; persist intent; perform visible UI dispatch; persist UI action completion; read a newer Poi observation; confirm exact mission ID, ordered ship IDs, reserved slot, and future return time; navigate to Home Port; require two consecutive HOME_PORT UI states; read a newer Poi observation and confirm the exact active tuple is unchanged.
- Ambiguous UI/observation outcomes block the fleet and forbid automatic retry. UI actions are disabled by default. The production composition root must inject one shared arbiter and a reviewed UI driver; no production driver is currently wired.
- This controller is a dispatch transaction primitive, not yet the unattended supervisor. Return-result settlement, BONUS_ITEMS progression, timer monitoring, and resupply remain separate pending integration work. The BONUS_ITEMS fail-closed guard remains unchanged.

## Read-only continuous fleet monitor

- ContinuousExpeditionMonitor reads one fresh, source-verified Poi Redux observation for all dispatchable fleets before changing any scheduler state. Missing or duplicate fleet records reject the whole tick before partial reconciliation.
- EXPEDITION_ACTIVE remains active while status 1 and the exact mission tuple match, even after the planned return timestamp. Only Poi mission status 2 with the same mission ID, final timestamp, reserved slot, and ordered ship IDs can confirm return.
- Return confirmation also requires the scheduler's post-dispatch Home Port proof. A returned fleet then remains in RETURNED_PENDING_UI / RETURN_RESULT / BONUS_ITEMS while the same returned mission identity remains present.
- IDLE and RESUPPLY_REQUIRED fleets become READY only after full supply and mission composition are observed. BLOCKED never auto-unblocks; DISPATCHING remains the responsibility of DurableDispatchCoordinator restart reconciliation.
- The monitor is read-only and emits durable events only for scheduler phase transitions, not every unchanged polling tick. It does not click, dispatch, collect rewards, or resupply.

## Offline continuous supervisor boundary

- Implemented `src/kancollebot/expeditions/continuous_supervisor.py` as a single-tick coordinator over the shared scheduler, monitor, and dispatch controller.
- The supervisor monitors first, pauses the shared dispatch queue for BLOCKED, unresolved DISPATCHING, pending settlement, or active fleets without Home Port proof, and dispatches READY fleets sequentially in configured order only when `allow_ui_actions` is explicitly enabled.
- Each dispatch must leave the scheduler in `EXPEDITION_ACTIVE` with Home Port verification before the next fleet can be dispatched. A failed post-dispatch proof stops the queue.
- This is offline orchestration logic, not a persistent process or production dispatch UI driver. It does not perform live game actions by itself. Natural freshness witness acquisition has since been verified and the refresh-only monitor integration is documented below; production dispatch/settlement driver integration, settlement/reward progression, resupply, and unattended operation remain pending.

## Visible UI port refresh adapter — offline implementation

- Added `src/kancollebot/adapters/poi/visible_ui_refresh.py` as a separately testable adapter for the already live-verified visible navigation sequence `HOME_PORT → SORTIE_MENU → EXPEDITION_LIST → HOME_PORT`.
- `VisibleUiPortRefreshDriver` requires two consecutive matching state observations before continuing after each click, shares the `UiOperationArbiter`, emits durable events, and stops without retry when a state is ambiguous. Attempted and completed clicks are recorded separately so a transport error cannot be mistaken for zero input.
- `FreshnessRefreshingStateReader` performs at most one navigation sequence per read, waits up to 45 seconds by default for a naturally captured, source-matched witness, and uses a 30-second refresh cooldown. Timeout returns the last unverified observation; it never promotes an unverified snapshot.
- Offline verification: adapter tests **8/8**, full Python suite **408/408**, `compileall`, and `git diff --check` passed.
- **Integration/live boundary:** the adapter is now optionally wired into `tools/run_continuous_expedition_monitor.py` behind `--enable-ui-refresh`; the default runtime still opens no game CDP input session. This wiring has only been tested with a fake input session and has not been run against Poi. The prior live navigation and 31-poll witness evidence remain separate. Dispatch, mission selection, RETURN_RESULT/BONUS_ITEMS settlement, and resupply remain separate acceptance gates.

## Supply UI checkpoint and current preflight boundary

- Added `UiState.SUPPLY_MENU` and enabled the fingerprint-capture utility to record it. On 2026-11-02, the live Ooi page was first confirmed as `HOME_PORT` by two consecutive in-memory fingerprints (scores 0.9984 / 0.9986). A normal UI click on the visible Home Port `補給` menu entry (observed center approximately x=120, y=350 at 1200×720) opened the ship-supply screen.
- The live supply screen was manually confirmed from an in-memory screenshot. A three-sample compact RGB fingerprint was captured using crop [150,105,1050,60] and grid [24,4] (288 channel values); no screenshot bytes were saved. The crop covers the stable screen-header row containing `艦船選択` and `燃料・弾薬の補給`, avoiding dynamic ship rows and resource values.
- Live verification passed: `verify_ooi_visual_fingerprint.py --expected SUPPLY_MENU` classified the page as `SUPPLY_MENU` with score 0.99967. The production checkpoint gate confirmed two consecutive frames (0.9996 / 0.9997). This parent page checkpoint is used before selecting a fleet.
- Added separate `SUPPLY_FLEET_2_SELECTED` and `SUPPLY_FLEET_3_SELECTED` visual states. Each uses a three-sample, 10×8 RGB fingerprint around its own selected fleet tab: Fleet 2 crop [249,155,30,40], Fleet 3 crop [295,155,30,40]. The classifier treats these as child states of `SUPPLY_MENU`, excluding only the shared parent score from the child state's margin check; sibling fleet states remain competitors.
- Live gate verification passed for both fleet tabs. Fleet 2 matched 1.0 on two consecutive frames; Fleet 3 matched 1.0 on two consecutive frames. Cross-state negative checks rejected Fleet 2 while Fleet 3 was selected (Fleet 2 score 0.8964) and vice versa. Normal UI tab transitions Fleet 2 → Fleet 3 and Fleet 3 → Fleet 2 were both confirmed by the two-frame production gate.
- Added regression tests for the bundled menu/fleet descriptors (crop, grid, sample count, vector length, and manual-label provenance) and for the hierarchical parent/child classification margin rule. No screenshot bytes were saved.
- A cached Poi Redux preflight previously showed Fleet 2 idle and composition-eligible for mission 1 (flagship Lv8), Fleet 3 idle and composition-eligible for mission 3 (flagship Lv5), and both below full supply. That cached observation is not a freshness witness and cannot authorize resupply or dispatch.
- The CDP accessibility tree exposed only `RootWebArea`; no semantic `補給` button was available. The menu and fleet-tab states therefore rely on manually confirmed visual fingerprints. The first Fleet 2 ship checkbox is now separately identified and gated by the bounded visual substate mechanism documented below; no supply-control state has been verified.
- The current one-shot UI script contains a recorded row target for mission 3 only (`MISSION_ROW = (350, 350)`); no mission-1 row target was found in the repository/template inventory. Do not infer a mission-1 coordinate. The `MISSION_SELECTED`, `FLEET_2_SELECTED`, and `FLEET_3_SELECTED` fingerprint entries still require independent live verification.
- Windows time service reported `Local CMOS Clock` with no successful synchronization. Actual skew is unknown; keep time-based unattended scheduling disabled until the time source is verified. No global time settings were changed.
- **Safety boundary:** only normal UI navigation into the supply page and between Fleet 2/Fleet 3 view tabs occurred. The first Fleet 2 ship checkbox was temporarily checked and then unchecked to observe the selection-only behavior; the screen returned to all rows unchecked. No individual supply or supply-all action, repair, dispatch, or reward action was performed; no game API request was sent.

## Optional visible refresh integration in the read-only monitor runtime

- `tools/run_continuous_expedition_monitor.py` now accepts `--enable-ui-refresh`. Without this flag, runtime composition remains unchanged: no game WebView CDP input session is opened and the raw read-only Poi reader is used.
- With the flag, the runtime opens one `connect_game_webview()` session, adapts the existing `_require_ui_state` two-frame Ooi checkpoint gate, and wires `VisibleUiPortRefreshDriver` through `FreshnessRefreshingStateReader`. The refresh driver shares a `UiOperationArbiter` and the existing SQLite durable event sink.
- The only permitted input remains the verified navigation path `HOME_PORT → SORTIE_MENU → EXPEDITION_LIST → HOME_PORT`. Runtime output explicitly reports `ui_navigation_enabled` separately from `game_actions_enabled: false`.
- The reader waits up to 45 seconds for a naturally captured matching witness and starts its 30-second cooldown after the complete wait. Unverified timeout results remain unverified; ambiguous UI outcomes stop without retry.
- Offline verification: refresh-adapter tests **9/9**, monitor-runtime tests **9/9**, full Python suite **414/414**, compileall, observer `node --check`, CLI `--help`, and `git diff --check` passed. Fake-session tests confirm composition only and send no clicks.
- **Not live-verified:** the `--enable-ui-refresh` CLI path has not been invoked against Poi. This is still a read-only monitor with optional refresh navigation, not a dispatch controller, settlement/reward driver, resupply driver, or unattended expedition loop.

## Fleet 2 first-ship checkbox visual substate (2026-11-02)

- Added `VisualTargetPoint` and `CdpInput.click_visual_target()`. The visual click path is separate from `RecordedPoint`: it requires a point produced by the approved bounded locator and verifies that the supplied screenshot payload has the exact SHA-256 digest used during localization. The point is restricted to the Fleet 2 first-ship checkbox region and label.
- Added `visual_target.py`. The locator scans only region [150,225,55,50], merges nested contours for the same square, and fails closed unless exactly one checkbox is found. The live detected bounds were [166,240,22,23], center [177,251.5] at 1200×720.
- Added a selection-only probe and a guarded restore tool. Each sent exactly one click to that checkbox; the restore tool required a visible check mark before clicking and verified that the mark disappeared afterward. The live checked interior contained 41 bright mark pixels; after restore it contained 0. No screenshot bytes were written.
- Added `SUPPLY_FLEET_2_SHIP_1_CHECKED` and `SUPPLY_FLEET_2_SHIP_1_UNCHECKED` as child substates. Their separate compact fingerprint file uses crop [164,238,26,26], grid [13,13], three samples per state, and 507 RGB channel values per descriptor. Checked-vs-unchecked similarity is 0.954001.
- Added a two-stage classifier: every frame must first match the parent `SUPPLY_FLEET_2_SELECTED` fingerprint, then the checkbox crop must match exactly one of the checked/unchecked descriptors (minimum score 0.97, minimum margin 0.04). The production gate requires two consecutive matching frames for the child state.
- Live production verification passed for both checked and unchecked states. The checked expectation was rejected while unchecked; the unchecked expectation was rejected while checked. Parent Fleet 2 state remained verified during both transitions. The page was restored to Fleet 2 with the first checkbox unchecked.
- Local verification: focused suites for visual fingerprint, UI state gate, CDP input, and visual target passed; full Python suite **430/430**, Node static suites **17/17**, Python compileall, Node syntax check, and `git diff --check` passed.
- Logs are retained locally at `logs/supply_checkbox_probe.jsonl` and the focused gate logs; the `logs/` directory is ignored by Git. Only compact fingerprints and metadata are tracked, not screenshots.
- **Scope boundary:** this verifies only Fleet 2's first ship checkbox. It does not verify other ship rows, Fleet 3 ship checkboxes, the supply button, supply-all, repair, dispatch, or reward controls. No resource action or game API request occurred; resupply remains disabled.


## Fleet 2 four-ship checkbox visual substates (2026-11-02)

- Extended the bounded visual-target allowlist to the four visible Fleet 2 ship rows. Independent regions are [150,225,55,50], [150,302,55,50], [150,379,55,50], and [150,456,55,50]. Live bounds were [166,240,22,23], [166,317,22,23], [166,394,22,23], and [166,471,22,23]. Each target retains a distinct label and exact source-frame SHA-256 binding; arbitrary coordinates and labels remain rejected.
- Added six explicit child states: SUPPLY_FLEET_2_SHIP_2_CHECKED/UNCHECKED, SHIP_3_CHECKED/UNCHECKED, and SHIP_4_CHECKED/UNCHECKED. The classifier now selects one requested ship index and compares only that ship's checked/unchecked descriptors after the parent SUPPLY_FLEET_2_SELECTED fingerprint matches. The production gate still requires two consecutive matching frames.
- Expanded supply_checkbox_fingerprints.json to eight descriptors (checked and unchecked for ships 1-4). Each uses three live samples, a 13x13 RGB grid, 507 channel values, and its own 26x26 crop: [164,238,26,26], [164,315,26,26], [164,392,26,26], [164,469,26,26]. Per-row checked/unchecked similarity is approximately 0.954.
- Added tools/calibrate_supply_checkbox.py with bounded capture-unchecked, probe, capture-checked, and guarded restore modes. Selection probes issue at most one click to the specified checkbox; restore requires the checked-state two-frame gate and verifies the unchecked-state two-frame gate. It does not expose any supply, repair, dispatch, or reward action.
- Live proof for Fleet 2 ships 2-4: each checkbox was independently located, temporarily selected with exactly one click, and visually confirmed with 41 mark pixels. Three checked samples were captured per row. Each was restored with exactly one click; the unchecked gate passed and the mark count returned to zero. Ship 1's earlier equivalent calibration remains valid.
- Final live verification confirmed all four checkboxes unchecked. For each row, the unchecked expectation passed its two-frame production gate and the checked expectation was rejected while the row remained unchecked. All four targets were found in the same in-memory frame with their expected bounds. No screenshot bytes were written.
- Regression coverage checks the four-row target allowlist, requested-row classifier isolation, all eight bundled descriptors, and explicit enum states. Full Python suite 433/433, Node static suites 17/17, Node syntax check, Python compileall, calibration CLI help, and git diff --check passed. GitHub Actions push run 41460331247 for commit ee58e67b3d789936dcc11389da957311854bb2ed completed successfully; the test job and all workflow steps passed.
- Safety boundary: this verifies only the four Fleet 2 ship-selection checkboxes. It does not authorize resupply. Fleet 3 ship checkboxes, individual supply, supply-all, repair, dispatch, and reward controls remain unverified and disabled. No game API request or resource-consuming action occurred.
- Local calibration and gate logs are retained under the ignored logs/ directory; only compact fingerprint values and the reusable calibration tool are tracked.


## Fleet 3 four-ship checkbox visual substates (2026-11-02)

- Added a bounded supply-tab visual target for Fleet 3: region [285,155,50,50], live outer contour [293,164,34,31], center [310,179.5]. The Fleet 2-to-Fleet 3 normal UI tab transition used one click bound to the exact source screenshot SHA-256 and passed the Fleet 3 parent-state two-frame gate.
- A Fleet 3 parent-gated in-memory scan found 14 contour candidates across the ship-list area. Exactly four candidates in the left checkbox column were independently localized with Fleet 3-specific labels: [166,240,22,23], [166,317,22,23], [166,394,22,23], and [166,471,22,23]. The other ten candidates were outside those bounded checkbox regions and were not clicked.
- Added explicit checked/unchecked child states for Fleet 3 ships 1-4. The production classifier now accepts fleet_index 2 or 3 and ship_index 1-4, first requiring the matching selected-fleet parent fingerprint, then comparing only the two descriptors for that fleet/ship. Each child state still requires two consecutive matching frames.
- Expanded supply_checkbox_fingerprints.json to 16 descriptors (checked and unchecked for ships 1-4 in Fleets 2 and 3). Each descriptor uses three samples, a 13x13 RGB grid (507 values), and a row-specific 26x26 crop. Fleet 3 checked/unchecked similarities: ship 1 0.954001, ship 2 0.954009, ship 3 0.954032, ship 4 0.954009.
- Generalized tools/calibrate_supply_checkbox.py with --fleet-index 2|3. It supports three-sample capture, one-click selection-only probe, and restore guarded by the exact checked and unchecked production gates. Added tools/navigate_supply_fleet_tab.py for bounded one-click navigation between supply Fleet 2 and Fleet 3 tabs.
- Live proof: all four Fleet 3 checkboxes started unchecked (0 mark pixels), were individually selected with exactly one click (41 mark pixels), had three checked samples captured, and were individually restored with exactly one click. The checked-state two-frame gate passed before restore; the unchecked-state two-frame gate passed afterward; every restored mark count returned to zero.
- Final Fleet 3 cross-row check: all four unchecked expectations passed their two-frame gates, all four checked expectations were rejected while unchecked, and all four targets were found at their expected bounds in one in-memory screenshot. No screenshot bytes were written.
- Local regression suite: Python 439/439, Node static tests 17/17, Node syntax, Python compileall, both utility CLI help checks, and git diff --check passed. GitHub Actions push run 41463396246 for commit a358afee9c0ffd7234f1d9cbeaabe0f5d07680bb completed successfully; the test job and all workflow steps passed.
- Safety boundary: this verifies only Fleet 3 ship-selection checkboxes and the Fleet tab navigation. It does not authorize resupply. Individual supply, supply-all, repair, dispatch, and reward controls remain unverified and disabled. No game API request or resource-consuming action occurred.


## 2026-11-02 - Live fleet-wide resupply across all unlocked fleets

- **Verified UI semantics:** the visible 艦隊全補給 button fills every ship in the currently selected fleet; it does not span all fleets. To meet the all-fleet requirement, the guarded workflow selects each under-supplied fleet tab and invokes this control once. No individual ship checkbox or single-ship supply path is used.
- **Freshness witness:** extended the passive /kcsapi/api_port/port observer to retain compact api_material records alongside fleet/ship state. The full-supply tool requires a natural successful response, a maximum 30-second witness age, exact matching of all material IDs/values against Poi info.resources, and a matching current fleet/ship snapshot. It initiates no game API request.
- **Preflight:** the unlocked fleet set was exactly [1,2,3], all three fleets were idle, and all 13 assigned ships had complete live/master fuel/ammunition values. Initial estimated cost was 32 fuel and 12 ammunition; inventory was sufficient. Fleet 1 was already fully supplied. Fleet 3 required 16 fuel/12 ammunition; Fleet 2 required 16 fuel/0 ammunition.
- **Live actions:** the source-frame-bound 艦隊全補給 button was independently localized at [1062,604,92,30]. Fleet 3 and Fleet 2 were selected through their own two-frame tab gates and each received exactly one full-fleet UI click. Each action's Poi state was read twice after the click; the target fleet became fully supplied and the resource delta exactly matched that fleet's preflight cost. No click was retried.
- **Independent final audit:** Fleet 1 5/5, Fleet 2 4/4, and Fleet 3 4/4 ships were all at their master maximum fuel and ammunition (13/13 total). All three fleets remained idle. Final inventory at the independent audit: 2484 fuel / 3453 ammunition. A later --dry-run acquired another matching fresh witness, calculated zero remaining supply cost, and exited through full_supply_not_needed without sending any resource click.
- **Reusable implementation:** added src/kancollebot/adapters/poi/full_supply.py with fail-closed freshness/resource/roster/mission/cost checks and per-fleet/all-fleet postconditions; added tools/full_supply_ui_action.py with --dry-run and explicit --authorize-full-supply; added a bounded, source-frame-SHA-256-protected visual locator for the fleet-wide supply button; extended the passive witness with material inventory.
- **Validation:** Python full suite 450/450 passed; Node static tests 17/17 passed; Node syntax, compileall (src/tests/tools), CLI help, post-supply no-op dry-run, independent 13-ship audit, and git diff --check passed.
- **Limits:** the independently verified supply-tab gates currently cover Fleet 2 and Fleet 3. Fleet 1 needed no click because it was already full; if Fleet 1 later becomes under-supplied, the tool fails closed until its tab-selection state is independently verified. The tool processes one target fleet per fresh-witness cycle. The one damaged ship remains damaged; repair is outside this operation.
- **Safety:** no game API request was initiated; the passive observer only captured naturally occurring port responses. No repair, dispatch, reward claim, or individual-ship supply action occurred. All screenshots remained in memory; no screenshot bytes were written to disk.

## 2026-11-03 - P0 production UI controller integration

- IMPLEMENTED: tools/one_expedition_ui_loop.py now composes the fresh Poi monitor, one-fleet scheduler, durable dispatch-intent coordinator, ContinuousDispatchController, single-tick ContinuousExpeditionSupervisor, and CdpContinuousExpeditionUiDriver. Production scope is fixed to Fleet 3 / mission 3; Fleets 1 and 2 are read-only and Fleet 4 remains deferred. A dedicated SQLite database isolates this one-fleet runtime.
- IMPLEMENTED: the driver uses visible CDP mouse input only. It has no game API request path or page-state mutation path, neutralizes the pointer before visual checks, requires two consecutive frames for every pre-dispatch state, and does not retry an ambiguous click. The production entry requires explicit --authorize-ui-actions.
- LIVE VERIFIED: Fleet 2 and Fleet 3 selected-tab fingerprints were recalibrated to small, disjoint tab-label crops. Both states pass their positive two-frame gate and reject the opposite state. Mission-3 row selection and the HOME_PORT -> SORTIE_MENU -> EXPEDITION_LIST -> MISSION_SELECTED -> FLEET_2_SELECTED -> FLEET_3_SELECTED -> HOME_PORT driver preparation path passed live. All captures remained in memory; no screenshot files were created.
- TESTED: offline integration verifies monitor -> controller -> supervisor composition, fresh Poi pre/post observations, exact Fleet 3 / mission 3 dispatch intent, Home Port postcondition, and fail-closed behavior when the monitor observation is not fresh. Full suite: 487 Python tests and 19 Node tests passed; compileall, CLI help, and git diff --check passed.
- ACCEPTANCE BOUNDARY: the live driver was exercised only through prepare_dispatch() and Home Port recovery. The irreversible expedition-start click was not sent; no expedition was dispatched and no resources were consumed. Post-dispatch natural freshness, return/result, reward collection, idle confirmation, resupply, and the full one-expedition loop remain NOT_VERIFIED.

## 2026-11-04 — Settlement state classifier conflict hardening

- **IMPLEMENTED:** `src/kancollebot/adapters/poi/visual_state_gate.py` now requires the RETURN_RESULT and BONUS_ITEMS markers to be free of conflicting `sortie_menu_title.png` evidence; RETURN_RESULT also continues to reject return-notice evidence, and BONUS_ITEMS rejects it explicitly. This reduces false-positive state labels when independent page markers overlap.
- **TESTED:** added two regression cases: bonus-title plus return-notice must classify as UNKNOWN; result marker plus sortie-menu title must classify as UNKNOWN. GitHub Actions run [41767679742](https://github.com/wosafety0/wosafety0clamp/actions/runs/41767679742) passed **571 Python tests, 20 skipped**, plus all 17 Node tests, observer syntax, CLI smoke checks, and Python compile.
- **LIMITS:** classification remains dependent on operator-supplied local templates and is only offline-tested in this change. The two-frame gate in `_require_ui_state()` is not yet connected to the continuous supervisor's returned-settlement lifecycle. No RETURN_RESULT/BONUS_ITEMS live captures, reward button localization, reward click, post-click confirmation, or supply integration were performed. The legacy helper's hard-coded `RETURN_CONFIRM` coordinate is not approved for production use.
- **NEXT:** implement a read-only settlement observer for the continuous supervisor, then calibrate the actual result/bonus action targets from live Poi captures. Keep reward input disabled until the target is uniquely localized from the exact current frame and the post-click state is independently verified.

## 2026-11-04 — Read-only settlement observer and supervisor transitions

- **IMPLEMENTED:** added `SettlementStateObserver`, which accepts only `RETURN_RESULT` or `BONUS_ITEMS` and requires structured evidence containing at least two consecutive frames for the exact expected state. It propagates capture/mismatch failures and sends no UI input.
- **IMPLEMENTED:** `ContinuousExpeditionSupervisor` optionally observes exactly one pending settlement fleet per tick. It advances `RETURNED_PENDING_UI → RETURN_RESULT` and `RETURN_RESULT → BONUS_ITEMS` only through the scheduler's guarded transition methods. If more than one fleet is pending settlement, it does not guess which fleet owns the shared Poi screen. `BONUS_ITEMS` remains paused; no reward-claim action or transition to resupply is enabled.
- **INTEGRATED:** the one-shot production entry wires the observer to the existing `verify_state` callback, which returns the two-frame visual/accessibility evidence. The integration is read-only with respect to settlement: no result/bonus click, no reward claim, no API request.
- **TESTED:** six observer unit tests and five supervisor transition/safety tests were added. GitHub Actions run [41768376887](https://github.com/wosafety0/wosafety0clamp/actions/runs/41768376887) passed **582 Python tests, 20 skipped**, plus the 17 Node static tests, observer syntax, CLI smoke checks, and Python compilation.
- **LIMITS:** the callback is wired but no live RETURN_RESULT or BONUS_ITEMS screen has been captured in this phase. Existing template recognition still depends on local operator-provided assets. This is not a live-accepted settlement loop and does not authorize the legacy hard-coded `RETURN_CONFIRM` coordinate.
- **NEXT:** obtain real Poi result/bonus screen evidence, calibrate distinct action targets, then implement click authorization and post-click verification as a separate stage. Keep the supervisor paused at BONUS_ITEMS until reward completion is independently proven.

## 2026-11-04 — Fail-closed reward claim authorization gate

- **IMPLEMENTED:** added `RewardClaimGate` and immutable target/authorization records. The only accepted asset name is `bonus_claim_button.png`; legacy `result_next_button.png` is rejected.
- **AUTHORIZATION CONDITIONS:** an explicit approved live-calibration mapping must match both template SHA-256 and calibration ID; target must be unique in the current frame; score must be at least 0.96 with at least 0.10 separation from the next candidate; bounds must fit the current viewport; and two consecutive state checks must both be `BONUS_ITEMS`.
- **FRESHNESS:** authorization is bound to a monotonically supplied frame ID and frame SHA-256. A changed frame invalidates it and requires recapture/re-authorization.
- **SAFETY LIMIT:** the default approved calibration allowlist is empty. No live Poi reward-button calibration currently exists, so the gate refuses reward authorization by default. The authorization object is deliberately non-executable and has no click method; no UI input was added or sent.
- **TESTED:** GitHub Actions [41768901495](https://github.com/wosafety0/wosafety0clamp/actions/runs/41768901495) passed **593 Python tests, 20 skipped**, plus all other workflow checks. Tests cover missing calibration, unique target, frame invalidation, wrong asset, duplicate candidates, weak confidence, out-of-bounds coordinates, wrong state sequence, and malformed calibration fingerprints.
- **NEXT:** acquire real Poi bonus-screen captures, identify and independently validate the actual claim button crop, then provision an approved calibration record. Only after that should a separate executor be designed to revalidate the exact frame immediately before input and verify the post-click result independently.

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

## 2026-11-04 — Production UI state evidence bound to exact captured frames

- **IMPLEMENTED:** the continuous production verifier now performs two independent state checks, each requiring two consecutive visual matches backed by exact CapturedFrame objects. The UiStateEvidence returned to the dispatch controller retains the final frame from each independent check.
- **FAIL-CLOSED:** accessibility-only evidence cannot satisfy the production two-check verifier. Missing frame objects, repeated image fingerprints, non-increasing frame IDs, or non-increasing monotonic capture timestamps are rejected.
- **TEST COVERAGE:** the classifier input is asserted to be the exact base64 encoding of each captured PNG frame; existing accessibility-only and visual fallback unit coverage remains separate.
- **LIMITS:** this establishes frame provenance for production state-check evidence, not cryptographic authenticity against a malicious in-process caller. Reward target localization still does not consume the retained second frame, and no reward click path or live Poi calibration is enabled.

## 2026-11-04 — Reward target retains captured frame object identity

- **IMPLEMENTED:** `RewardClaimTarget` now retains the exact two `CapturedFrame` objects used for the consecutive BONUS_ITEMS checks and a direct reference to the second object used for localization. Existing frame ID/hash metadata must match those objects, and the localization frame must be the identical second object, not a separately reconstructed equivalent.
- **TEST COVERAGE:** tests use valid PNG-backed `CapturedFrame` instances and reject metadata/object mismatches and equivalent-but-distinct target frame objects.
- **LIMITS:** this is an in-process object-identity consistency check, not cryptographic caller authentication. The production reward locator is not yet wired to construct targets directly from the `UiStateEvidence.frames` objects; no live calibration or click executor exists, so reward claiming remains disabled.

## 2026-11-04 — Reward target localization consumes retained BONUS_ITEMS frames

- **IMPLEMENTED:** added `locate_reward_claim_target`, accepting only the production `SettlementObservation` or `UiStateEvidence` types and requiring two distinct, increasing `CapturedFrame` objects from consecutive `BONUS_ITEMS` checks.
- **FRAME BINDING:** matching runs only against the exact second frame's PNG bytes. The returned `RewardClaimTarget.frame` is the identical second object; both state frame objects are retained. Template SHA-256 is computed from the exact local asset bytes.
- **FAIL-CLOSED:** requires the dedicated `bonus_claim_button.png` asset, explicit calibration ID and bounded search region, 1200x720 viewport, confidence >= 0.96, and uniqueness margin >= 0.10. Missing assets, invalid evidence, low confidence, duplicate matches, or invalid bounds raise a localization error. No coordinates are inferred and no click is sent.
- **TESTS:** added validation for wrong state, missing frames, fabricated lookalike evidence, wrong template, missing calibration inputs, exact second-frame object identity, and duplicate matches. CI now installs the optional vision dependencies so image-matching tests execute rather than being skipped.
- **LIMITS:** no real `bonus_claim_button.png` asset or live Poi calibration region has been reviewed in this environment. The locator is implemented but not connected to a production reward-click workflow. A Python evidence dataclass is not cryptographic caller authentication; the accepted concrete evidence types and object identity checks are consistency safeguards only. Reward clicking remains disabled.

- **CI VERIFIED:** GitHub Actions run [41772607310](https://github.com/wosafety0/wosafety0clamp/actions/runs/41772607310) passed all 612 tests with optional OpenCV/NumPy vision dependencies installed; zero tests were skipped. This includes synthetic target localization, duplicate-match rejection, exact second-frame object retention, and allowlist-gated authorization. It is offline synthetic evidence, not live Poi calibration.

- **HARDENING:** template matching now decodes the exact byte buffer whose SHA-256 is recorded in the target (no second file read or cache race); boolean/non-numeric thresholds are rejected. Regression coverage verifies the exact template digest.


## 2026-11-04 — Production reward authorization preparation wired (read-only)

- **IMPLEMENTED:** added `RewardClaimPreparer` and wired it into the production settlement-state verification callback. When two consecutive visual checks confirm `BONUS_ITEMS`, the callback can prepare a frame-bound authorization from the exact retained second `CapturedFrame`; the supervisor still receives the same state evidence.
- **EXPLICIT CONFIGURATION:** opt-in requires all four environment settings: `KANCOLLEBOT_BONUS_CLAIM_TEMPLATE_PATH`, `KANCOLLEBOT_BONUS_CLAIM_CALIBRATION_ID`, `KANCOLLEBOT_BONUS_CLAIM_APPROVED_TEMPLATE_SHA256`, and `KANCOLLEBOT_BONUS_CLAIM_REGION` (`left,top,right,bottom`). No settings means preparation stays disabled; partial settings fail closed. The template bytes must match the separately supplied approved SHA-256, and the region must fit the 1200x720 viewport.
- **RUNTIME BEHAVIOR:** with valid configuration, the entry logs authorization frame ID/hash, calibration ID, target center/bounds and template hash. Without configuration it logs that authorization is unavailable. Neither branch sends UI input; no click executor is present.
- **TESTS:** GitHub Actions run [41773389790](https://github.com/wosafety0/wosafety0clamp/actions/runs/41773389790) passed 620 tests with OpenCV/NumPy enabled and zero skips. The new end-to-end synthetic test verifies the production evidence → exact-frame localization → explicit-template allowlist → non-executable authorization path.
- **LIMITS:** this is synthetic/offline validation only. No real reward template, live calibration region, or approved template digest has been obtained. Do not configure the environment values from guessed coordinates or synthetic assets. Reward clicking remains disabled; no live Poi behavior is claimed.


## 2026-11-04 — Reward authorization freshness and expiry

- **IMPLEMENTED:** reward authorization now rejects a target whose captured frame is older than the configured authorization TTL (default 2 seconds), or whose monotonic capture timestamp is later than authorization time.
- **EXPIRY:** each authorization records monotonic issue and expiry timestamps. `assert_same_frame` rejects use after expiry as well as a changed frame ID/hash. The authorization remains non-executable; no click operation was added.
- **TESTS:** GitHub Actions run [41773910790](https://github.com/wosafety0/wosafety0clamp/actions/runs/41773910790) passed 623 tests, zero skipped. Coverage includes stale target rejection, future timestamps, and authorization expiry.
- **LIMITS:** the two-second TTL is a conservative freshness guard for a future action boundary, not proof of live screen continuity. A future executor must call `assert_same_frame` immediately before any authorized input and independently verify the post-action state. No executor exists or is enabled.


## 2026-11-04 — Reward locator counts spatial candidates (fail-closed)

- **IMPLEMENTED:** reward localization now performs non-maximum suppression over the complete template-match response and counts spatially distinct candidates meeting the authorization confidence threshold. It no longer hardcodes `candidate_count=1`.
- **FAIL-CLOSED:** zero qualifying candidates or more than one spatially distinct high-confidence candidate blocks authorization. The locator also retains the confidence-margin requirement. The gate independently requires `candidate_count == 1`.
- **TESTED:** GitHub Actions run [41774452317](https://github.com/wosafety0/wosafety0clamp/actions/runs/41774452317) passed 623 tests with zero skipped; the duplicate-placement regression test passed.
- **LIMITS / DESIGN REVIEW:** this corrects the prior mismatch between the claimed candidate count and the actual image-match search. It does not make a Python authorization object unforgeable or provide one-time consumption. There is still no click executor. Any future executor must own replay/consumption state, re-capture and re-localize immediately before input, and independently verify the post-click state. The current same-frame assertion is a consistency check over supplied frame identifiers, not proof that the live display remained unchanged.


## 2026-11-04 — Reward authorization validates both evidence-frame ages

- **IMPLEMENTED:** authorization now checks the age of both consecutive `BONUS_ITEMS` evidence frames against the configured TTL, not only the localization frame. A fresh second frame can no longer mask an excessively old first state check.
- **INPUT VALIDATION:** `assert_same_frame` now rejects non-integer frame IDs (including booleans and floats), non-positive IDs, and malformed SHA-256 values before comparing frame identity.
- **TESTED:** GitHub Actions run [41774962958](https://github.com/wosafety0/wosafety0clamp/actions/runs/41774962958) passed 625 Python tests. The regression cases cover a stale first frame with a fresh target frame and malformed frame identifiers.
- **LIMITS:** this remains an in-process, non-executable authorization record. It is not a one-time capability, does not authenticate a caller, and does not prove live display continuity. No reward click executor or live calibration is enabled.


## 2026-11-04 — Process-local one-time reward authorization consumption

- **IMPLEMENTED:** `RewardClaimGate` now issues each authorization with a random identifier and registers the exact returned object in a gate-owned `RewardClaimAuthorizationRegistry`. The registry atomically consumes an authorization once under a thread lock; replay, a copied/fabricated object, a foreign registry, and an expired authorization are rejected.
- **BOUNDS:** the registry is process-local and volatile. A process restart clears pending/consumed history; it does not provide durable exactly-once semantics across crashes or restarts. Pending entries are bounded (default 1,024); expired entries are pruned when a newer authorization is registered. Saturation fails closed.
- **SAFETY:** consumption only marks an authorization as used. It performs no CDP call, click, or game request. A future executor must consume immediately at its input boundary, re-capture and re-localize the current UI immediately beforehand, re-check consecutive BONUS_ITEMS evidence and TTL, and independently verify the post-click state. The registry is an in-process replay guard, not an unforgeable capability or protection against malicious code in the same process.
- **TESTS:** regression coverage verifies exactly-once consumption, replay rejection, fabricated/foreign object rejection, expiry, and two concurrent consumers racing for the same authorization.
- **LIMITS:** no live Poi calibration, reward-click executor, or post-click verification is enabled. Reward claiming remains disabled.


## 2026-11-04 — Reward claim input lifecycle protocol (offline, no executor)

- **IMPLEMENTED:** added `RewardClaimExecutionProtocol` in `src/kancollebot/expeditions/reward_claim_protocol.py`. It models the irreversible boundary without providing a production input adapter.
- **ORDERED PHASES:** `READY → CONSUMED_BEFORE_INPUT → INPUT_ATTEMPTED → POSTCONDITION_VERIFIED`. Authorization/frame/TTL rejection becomes `BLOCKED_BEFORE_INPUT`; any ambiguous input or failed/missing postcondition becomes `UNKNOWN_AFTER_INPUT`.
- **INPUT CONTRACT:** caller revalidates exact authorization frame identity and expiry, consumes the exact one-time authorization, then calls `begin_input_attempt()` immediately before exactly one ordinary visible-UI input attempt. The protocol itself never invokes CDP or any input callback. If the input call raises or its result is ambiguous, no retry is allowed.
- **POSTCONDITION CONTRACT:** two distinct, increasing `CapturedFrame` objects must be captured after the input-attempt timestamp. An independent classifier must explicitly assert `reward_claimed=True` and a state other than `BONUS_ITEMS`. The protocol validates evidence shape and time ordering; it does not authenticate the classifier or inspect pixels itself.
- **CRASH/RESTART:** protocol phase and registry are process-local. A restart loses both; old authorizations must be discarded. The new process must inspect the current visible state and create fresh evidence and authorization. It must never resume an input from a pre-crash authorization.
- **TESTS ADDED:** `tests/test_reward_claim_protocol.py` covers the one-shot transition sequence, changed frame, expiry, replay/second attempt, post-input frame timestamps, and unknown outcome. GitHub Actions [41775989058](https://github.com/wosafety0/wosafety0clamp/actions/runs/41775989058) passed 636 Python tests with zero skips, plus all other workflow checks.
- **LIMITS:** no production click executor, live template, calibration, post-click visual classifier, or supervisor integration was added. Reward collection remains disabled; this is not live-verified.


## 2026-11-04 — Oldest reward-state frame freshness at consumption

- **IMPLEMENTED:** `RewardClaimAuthorization` now carries the capture timestamp of the oldest frame in its two-frame BONUS_ITEMS evidence. `assert_same_frame()` rechecks that timestamp against the authorization TTL at the input boundary.
- **FAIL-CLOSED:** a first frame that ages beyond the TTL is rejected even if the authorization object has not reached its own expiry time. No input attempt may follow that rejection.
- **TESTS ADDED:** gate and protocol regressions cover the boundary where the first frame has expired but the authorization itself is still nominally valid. GitHub Actions [41776371723](https://github.com/wosafety0/wosafety0clamp/actions/runs/41776371723) passed 638 Python tests with zero skips, plus all other workflow checks.
- **LIMITS:** this is offline time-bound validation only; it does not provide live screen capture, input execution, or post-click acceptance.
