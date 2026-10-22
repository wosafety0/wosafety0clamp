# KancolleBot Development Progress

## 2026-10-21
- Confirmed private repository `wosafety0/KancolleBot`, default branch `main`.
- Established Python package structure, domain models, safety policy, read-only POI adapter contract, tests, and GitHub Actions CI.
- Added timezone-aware `ObservedState` freshness validation.
- Research direction: KCV (非提助手) first, KCA (`kcauto_custom`) supplementary, Python implementation, local POI runtime.
- Do not confuse KCV with `Grabacr07/KanColleViewer`. Do not automatically scan, read, or modify legacy `D:\KancolleBot\Source`. Review licensing before uploading third-party source.
- Prior user-provided local diagnostics confirmed `http://127.0.0.1:17777/fleets` and `/ships` returned HTTP 200. Cloud CI does not access local POI.
- KCV automation call chain remains under research. Automated battle is not implemented. Live gameplay actions remain disabled.

## 2026-10-22 — Local product integration
- Added pure fleet parsing, expedition planning, schema-configurable ship parsing, and a loopback-only read-only POI HTTP adapter.
- Adapter only issues GET requests to `/fleets` and `/ships`, disables redirects, bounds response size, uses strict UTF-8, and cross-checks fleet ship IDs.
- Added `get_ship_states(fields)`; field mapping must be explicit and is not guessed.
- Added pure fleet HP safety checks and a fail-closed dispatch readiness gate. The gate blocks missing/invalid ship state, empty fleets, zero HP, critical HP, and all unchecked supply, repair/docking, mission-composition, and fleet-rotation gates.
- Hardened the dispatch decision gate: invalid mission IDs and fleets that are not explicitly observed idle are blocked, including unknown and underway mission states.
- Added `plan_fresh_safe_expedition()`: it requires fleet and ship observations to be timezone-aware, within a caller-specified maximum age, and labeled with the same source. Stale, future-dated, or mixed-source snapshots return `wait` instead of planning from inconsistent data.
- Added offline tests for dispatch safety, invalid mission IDs, non-idle/unknown mission state, fresh observations, stale/future timestamps, and mismatched observation sources.
- CI passed for commit `6dc50b1d2bd806d9064f1a88864c29ecdb42dcc9` (run 39789699491): https://github.com/wosafety0/KancolleBot/actions/runs/39789699491
- CI passed for orchestration commit `728c3d6dff244e71ff8f1ba841766906cfcd51ba` (run 39791133890): https://github.com/wosafety0/KancolleBot/actions/runs/39791133890
- CI passed for dispatch-gate hardening commit `5fca0b28c7da438067140210aab995d3787f9291` (run 39791838719): https://github.com/wosafety0/KancolleBot/actions/runs/39791838719
- CI success does not mean runtime verification against local POI.
- An uploaded snippet mentions `api_name`, `api_nowhp`, and `api_maxhp`, but no authoritative raw `/ships` fixture proves those keys. They remain unverified.
- Supply, repair/docking, mission composition, and rotation checks are not yet backed by real observations. The gate defaults to blocked.
- No real game actions have been executed.

### Next steps
1. Capture and redact one raw `/ships` record from local POI and verify actual field names.
2. Implement data-backed supply/repair/composition/rotation checks; never manually attest these flags without evidence.
3. Continue KCV (非提助手)-first call-chain research, using KCA only for labeled supplementary comparisons.


## 2026-10-22 — Reference repository identity audit

- Audited the uploaded private repository `wosafety0/KCV` at commit `5f67fd8c134665d64919445edea061b3ec5eaa06`.
- User-confirmed research identity: KCV = 非提助手; `wosafety0/KCV` is the designated decompiled reference repository. README wording (“KCA Reverse Engineering”), directory names, and namespaces are metadata/code-lineage inconsistencies to analyze, not grounds to reject that identity.
- Existing call-chain observations in `docs/research/REFERENCE_REPO_IDENTITY_AUDIT_20261022.md` must be attributed to their exact subdirectory/version after tracing context; they are not a POI API contract.
- No third-party source was copied into the implementation repository. No local POI connection or real game action was performed.
- Current commit `f12485f1d9fb2efffb8b764d9a0a9ccb10e536ff` CI passed (run 39792656870): https://github.com/wosafety0/KancolleBot/actions/runs/39792656870

### Next steps

1. Confirm and upload/locate the actual 非提助手 source material using repository metadata and README evidence; do not read the prohibited legacy source path.
2. Continue Python-side state-machine design from verified event contracts only; keep live actions disabled.


## 2026-10-22 — Official KCV (非提助手) website cross-check

- Checked the official product page: https://kancolleassistant.mechland.cn/.
- The page identifies the product as “(非)提助手”, lists automatic expeditions, automatic sorties/ship rotation, and POI mode, and states that operation uses simulated mouse clicks with randomized coordinate offsets rather than direct game-server packet requests.
- The inspected page provides release-package links (v2.6.2, dated 2026-08-19) but no source-code repository link. Product identity is confirmed; internal implementation call chain remains unverified.
- Treat `wosafety0/KCV` as the user-designated 非提助手 decompilation reference. Continue validating component/version lineage before attributing individual implementation details. No binary package was downloaded or inspected in this cloud task.
- The preceding commit's CI job `test` completed successfully (compile + unit tests). Run: https://github.com/wosafety0/KancolleBot/actions/runs/39793352039


## Development focus update — expedition closure first (2026-10-22)

The immediate implementation priority is a minimal automatic-expedition closed loop, not broad KCV reverse engineering or a full battle state machine.

Target sequence:
1. Read fleet and ship observations and validate freshness/completeness.
2. Detect completed expeditions and recommend collection before dispatch.
3. Verify the result of collection before making another decision.
4. Check fleet readiness using evidence-backed HP, supply, repair/docking, mission-composition, and rotation data.
5. Select a configured mission and recommend dispatch only when every gate passes.
6. Verify the post-dispatch state before treating the operation as successful.
7. On timeout, connection failure, malformed/unknown state, or inconsistent observations, stop and require fresh observations rather than retrying blindly.

Current code implements read-only observations, parsing, planning, and fail-closed dispatch recommendations. It does **not** yet implement a live collect/dispatch action adapter or verify action outcomes. Live gameplay actions remain disabled by default. The three broader research questions (battle state machine, full POI capability inventory, and generalized stop/recovery design) should only be investigated as needed to close this expedition loop.


## 2026-10-22 — Action outcome verification foundation

- Added a pure expedition action verifier for collection and dispatch outcomes; it performs no I/O and cannot trigger gameplay actions.
- Requires same-source, fresh, timezone-aware, strictly time-ordered pre/post observations. Missing/duplicate target fleet, invalid preconditions, stale snapshots, or unexpected states produce `unknown` and require stopping.
- Collection is confirmed only by completed -> idle. Dispatch is confirmed only by idle -> underway with the requested mission ID.
- A valid unchanged state is `not_confirmed`; it still requires stopping and explicitly does not authorize an automatic retry.
- Added offline unit tests for successful transitions, unchanged states, mismatched mission, unknown preconditions, stale snapshots, source mismatch, and invalid mission IDs.
- This is verification logic only. There is still no live collect/dispatch adapter, and real gameplay actions remain disabled. CI status must be checked for this commit before treating tests as passed.


## 2026-10-22 — Targeted KCV expedition review

- Inspected the designated `wosafety0/KCV` repository at commit `5f67fd8c134665d64919445edea061b3ec5eaa06`, limited to the expedition action path.
- `AutoExpedition.cs` observes successful `api_req_mission_result` events, queues completed-expedition handling based on remaining time, and has explicit stop/reset error paths. `ActFinishExpedition.cs` performs configured coordinate clicks and waits for a mission-result event with a 30-second queue timeout. `ActExpedition.cs` sequences navigation, mission-data observation, fleet/mission selection, availability check, and confirmation.
- These are reference implementation details, not evidence that POI offers the same action API. A completed UI action queue is not sufficient proof of a fleet state transition.
- The Python action verifier was added in commit `cfb2d11260f7d98815519c7fa910455c7d3744e5`; it is pure/offline and requires independent post-action observations. No live action adapter was added or executed.
- Next: finish CI verification for the verifier commit; continue the expedition implementation using observed evidence, and only inspect further KCV code where it resolves a concrete blocker.


## 2026-10-22 — Pure expedition cycle state transitions

- Added `src/kancollebot/core/expedition_cycle.py`: a pure, fail-closed lifecycle for staging a single expedition intent and resolving it from fresh pre/post fleet observations.
- States are `ready`, `awaiting_observation`, and `stopped`. Unconfirmed results, missing post-observation, action errors, and attempting to stage another action before resolution lock the cycle instead of retrying.
- Added 12 offline cycle tests covering collect/dispatch confirmation, mismatch, no-op wait, duplicate resolution, and failure paths.
- This module stages **intent only**. It does not click, connect to POI, dispatch, collect, or authorize gameplay. Existing safety gates remain mandatory before any future action adapter.
- CI for this change must be checked separately. Cloud success does not establish local runtime integration.

## 2026-10-22 — Privacy-preserving ship schema inspection

- Added `src/kancollebot/core/ship_schema.py`, a pure bounded shape inspector for `/ships`-style objects. It emits only record counts and field-name/type frequencies, never ship IDs, ship names, or raw field values.
- Added six offline unit tests for redaction, sampling, malformed input, field limits, invalid limits, and mixed field types.
- This is a preparatory tool, not proof of actual POI field names. No local POI connection, upload of raw user data, or gameplay action occurred. CI pending verification.

## 2026-10-22 — Bind expedition intent to its planning observation

- Hardened `ExpeditionCycle`: non-wait intents now require a baseline observation, and the cycle stores that exact immutable observation for resolution. The resolver no longer accepts a caller-supplied `before` snapshot that could be unrelated to the decision.
- Staging checks target fleet uniqueness and action preconditions (completed for collect; idle for dispatch). Missing/invalid baseline fails closed.
- Added tests for missing baseline, post-snapshot timestamp ordering, and source mismatch; updated cycle tests to the new API.
- Pure offline logic only. No gameplay adapter, POI runtime access, or live action was introduced. CI pending.

## 2026-10-23 — Reference synthesis and passive-ingestion implementation boundary

- Consolidated Poi Forwarder, Kuma, KCV (非提助手), KCA, KanCollePlayerSimulator, and KancolleSniffer findings in `docs/research/REFERENCE_SYNTHESIS_AND_IMPLEMENTATION_PLAN_20261023.md`.
- Confirmed from the current source that the webhook receiver already sanitizes and queues events, but it does not yet validate KCSAPI success semantics or reduce a sequence of events into a coherent current state.
- Prioritized the next implementation task: verify the real Poi Forwarder payload contract, then implement a pure normalizer with offline fixtures. Do not invent event fields or infer a complete fleet state from one event.
- Extracted independent design ideas from KanCollePlayerSimulator: resource-utility scoring, anti-thrashing/hysteresis, fleet-composition filters, and POI battle-log-derived offline scenarios. Simulator estimates and TODOs are not authoritative game rules.
- CI passed for synthesis commit `7f0d84bcd018081ce4efab4e0d22bee5f815288c` (run 39834190517). This validates repository checks only; local Poi integration remains unverified.
- No local files were changed by this cloud task, no local Poi runtime was accessed, and no real game actions were executed.


## 2026-10-23 — Conservative Forwarder event normalization

- Added `src/kancollebot/adapters/poi/event_normalizer.py` as a pure, side-effect-free validation layer for sanitized receiver events.
- The normalizer fails closed for invalid paths, non-response stages, missing/non-mapping responses, unconfirmed `api_result == 1`, and missing/invalid timestamps. It does not reduce an event into a full fleet snapshot and cannot authorize a gameplay action.
- Added `tests/test_poi_event_normalizer.py` with 10 offline tests for accepted envelopes and fail-closed cases.
- GitHub Actions passed for normalizer commit `6b97f98517f3e53a7894888250c0509e3cbcbbaf` (run 39835227594) and test commit `f417c1a6516f59995a4eb78930ff8678158d72d2` (run 39835240442).
- The exact installed Poi Forwarder payload contract remains unverified. This implementation is deliberately conservative and must be checked against real, redacted local events before being treated as integration-ready.
- Local Poi startup is not yet requested: the next useful local session should happen after a minimal read-only diagnostic entry point is prepared, so the session can verify delivery, payload shape, timestamp semantics, and state mapping rather than only confirm that Poi launches.
- No local files were changed or local Poi runtime accessed. No real game actions were executed.
