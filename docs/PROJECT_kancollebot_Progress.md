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
- Added pure fleet HP safety checks. Missing/invalid state, empty fleet, zero HP, and HP at/below the critical threshold block the check.
- CI passed for commit `2fdf715fe697b08bd9609002f852631d554ccd46` (run 39788837921): https://github.com/wosafety0/KancolleBot/actions/runs/39788837921
- CI success does not mean runtime verification against local POI.
- An uploaded snippet mentions `api_name`, `api_nowhp`, and `api_maxhp`, but no authoritative raw `/ships` fixture currently proves those keys. The HP gate is not yet connected to dispatch planning.
- Supply, repair/docking, mission composition, and fleet rotation checks remain unresolved. No real game actions have been executed.

### Next steps
1. Capture and redact one raw `/ships` record from local POI.
2. Verify the field map and add a fixture test using that evidence.
3. Integrate HP readiness into planning while blocking on unverified supply/repair/composition/rotation gates.
4. Continue KCV-first automation call-chain research; label KCA-only evidence clearly.
