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
- Added `core/expedition_safety.py` to apply the readiness gate to planner recommendations: dispatch recommendations become `wait` unless the planned fleet is present and every gate passes. Collection/wait recommendations remain unchanged. This is decision gating only, not live-action authorization.
- Added offline tests for default blocking, all-gates-passed behavior, absent planned fleet, critical HP, and preservation of non-dispatch decisions.
- CI passed for commit `6dc50b1d2bd806d9064f1a88864c29ecdb42dcc9` (run 39789699491): https://github.com/wosafety0/KancolleBot/actions/runs/39789699491
- CI success does not mean runtime verification against local POI.
- An uploaded snippet mentions `api_name`, `api_nowhp`, and `api_maxhp`, but no authoritative raw `/ships` fixture proves those keys. They remain unverified.
- Supply, repair/docking, mission composition, and rotation checks are not yet backed by real observations. The gate defaults to blocked.
- No real game actions have been executed.

### Next steps
1. Capture and redact one raw `/ships` record from local POI and verify actual field names.
2. Implement data-backed supply/repair/composition/rotation checks; never manually attest these flags without evidence.
3. Continue KCV (非提助手)-first call-chain research, using KCA only for labeled supplementary comparisons.
