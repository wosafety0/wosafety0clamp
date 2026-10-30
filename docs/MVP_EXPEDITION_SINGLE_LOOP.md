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
