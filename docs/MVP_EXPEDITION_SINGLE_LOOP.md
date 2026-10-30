# Single Expedition MVP — Execution Contract

Status: IN_PROGRESS (2026-10-30; user-authorized UI-mediated dispatch for fleet 2 mission 3 verified; same-mission return/reward/re-dispatch and Bot integration remain pending)
Branch: dev/expedition-closed-loop-mvp

## One acceptance criterion
One fixed fleet performs one fixed expedition, with observed transitions:
ready -> dispatched -> completed -> reward collected -> ready.

## Strict scope
1. Read the fixed fleet's expedition state from Poi.
2. Identify readiness and expedition-in-progress/completed states using observed fields.
3. Identify the shortest feasible action mechanism for one dispatch and one reward collection.
4. Implement guarded single-dispatch and reward collection only after separate user authorization for live game actions.
5. Observe return to ready; stop. No continuous re-dispatch is required.

## Stop conditions
Unknown or stale state, mismatched fleet/expedition, ambiguous completion, unavailable action interface, or any unexpected response -> STOP without game actions.

## Current evidence and blockers
- Historical Poi read-only evidence: info.fleets (3 fleets) and info.ships (91 ships), positive fleet-ship references matched.
- Live local Poi MCP bridge was reachable and reported a ready Redux store. `poi_describe(info.fleets)` confirmed `api_mission` is part of the fleet records; one read-only `poi_get(info.fleets)` returned three cached values `[0,0,0,0]`. This is a current cache observation only: no source-freshness witness or before/after expedition transition was established.
- Existing Python MCP client is read-only; poi_get is allowlisted; poi_hensei_save is explicitly excluded.
- Fleet/ship reference consistency is not expedition-state recognition.
- CDP direct WebSocket connection to the embedded KanColle `webview` target and `Page.captureScreenshot`: VERIFIED 2026-10-30; three consecutive read-only captures produced valid PNGs at 1200x720, matching the CSS visual viewport (1200x720, scale 1, page origin 0,0). One captured frame was visually inspected and showed the rendered top-level menu with sortie/practice/expedition choices; no loading/black screen/modal was apparent in that frame. This is a single-frame manual observation, not an automated visual-state gate. Coordinate mapping remains NOT_VERIFIED.
- Real dispatch and reward-collection action interfaces: NOT_VERIFIED.
- End-to-end live expedition loop: NOT_VERIFIED.
- Current work-round tests: 167 Python unit tests passed; live screenshot was read-only.
- No game actions authorized or executed.

## Immediate next implementation task
The original stop-loss was revised only for the user's explicit authorization of normal game-UI input while prohibiting direct packet sending. A single client-mediated CDP `Input.dispatchMouseEvent` click sequence against the visible game UI has now been verified for a real dispatch. Do not call game API methods, construct/send game-server requests, or use page/PIXI injection to mutate game state. Next, track the same fleet and mission to completion, collect its result through the normal UI, verify the idle transition, and then implement/verify Bot-owned guarded re-dispatch with state readback. Fail closed on stale/unknown state, target ambiguity, or viewport mismatch.

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
