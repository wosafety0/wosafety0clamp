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
- Found an identity mismatch: its README identifies the material as “KCA Reverse Engineering”; its tree contains `KCA_Decompiled/` and `feitizhushou_Deobfuscation/` with `Grabacr07.KanColleViewer.*` namespaces. This does not verify it as the primary reference KCV = 非提助手.
- Recorded comparison-only call-chain evidence in `docs/research/REFERENCE_REPO_IDENTITY_AUDIT_20261022.md`. The inspected comparison code sequences UI-coordinate actions with API-session event waits; this is not a POI API contract and must not be attributed to 非提助手.
- No third-party source was copied into the implementation repository. No local POI connection or real game action was performed.
- Current commit `f12485f1d9fb2efffb8b764d9a0a9ccb10e536ff` CI passed (run 39792656870): https://github.com/wosafety0/KancolleBot/actions/runs/39792656870

### Next steps

1. Confirm and upload/locate the actual 非提助手 source material using repository metadata and README evidence; do not read the prohibited legacy source path.
2. Continue Python-side state-machine design from verified event contracts only; keep live actions disabled.


## 2026-10-22 — Official KCV (非提助手) website cross-check

- Checked the official product page: https://kancolleassistant.mechland.cn/.
- The page identifies the product as “(非)提助手”, lists automatic expeditions, automatic sorties/ship rotation, and POI mode, and states that operation uses simulated mouse clicks with randomized coordinate offsets rather than direct game-server packet requests.
- The inspected page provides release-package links (v2.6.2, dated 2026-08-19) but no source-code repository link. Product identity is confirmed; internal implementation call chain remains unverified.
- Do not treat the uploaded `wosafety0/KCV` source tree as verified 非提助手 source; retain the identity-audit conclusion. No binary package was downloaded or inspected in this cloud task.
- The preceding commit's CI job `test` completed successfully (compile + unit tests). Run: https://github.com/wosafety0/KancolleBot/actions/runs/39793352039
