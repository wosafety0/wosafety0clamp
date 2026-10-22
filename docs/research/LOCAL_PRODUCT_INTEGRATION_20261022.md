# Local Product Files Integration — 2026-10-22

## Source review
- `state_parser.py` mixes explanatory prose and Python snippets, so it is not directly importable.
- `scheduler.py` combines scheduling, a synchronous action queue, and an unverified CDP/WebSocket reader; it is not safe to connect directly to the live game.
- `guard.py` contains allow-list and failure-lock concepts. The existing package policy remains the canonical live-action gate; live actions stay disabled by default.

## Integrated
- `src/kancollebot/core/fleet_parser.py`: pure fleet parser preserving original ship slot positions; missing expedition state stays unknown.
- `src/kancollebot/core/expedition_planner.py`: pure planner recommending at most one collect, dispatch, or wait decision; it does not execute actions.
- `src/kancollebot/core/ship_parser.py`: strict schema-configurable parser for raw `/ships` records. Callers must supply `ShipFieldMap`; field names are not guessed.
- `src/kancollebot/adapters/poi/http_read_only.py`: loopback-only HTTP adapter restricted to GET `/fleets` and `/ships`, strict UTF-8/JSON, bounded response size, no redirects, and fleet/ship ID cross-checking. `get_ship_states(fields)` requires an explicit field map.
- `src/kancollebot/safety/fleet.py`: pure HP gate; empty fleets, missing ship states, ID mismatches, zero HP, and HP at/below the critical ratio block the check.
- `src/kancollebot/safety/dispatch.py`: fail-closed dispatch readiness gate combining HP safety with explicit supply, repair/docking, mission-composition, and fleet-rotation verification flags. All non-HP gates default to unverified.
- `src/kancollebot/core/expedition_safety.py` applies the readiness gate to planner recommendations and exposes `plan_safe_expedition()` as a combined planning-and-gating entry point. Dispatch becomes `wait` unless the planned fleet exists in current observations and all readiness checks pass; completed collection keeps priority. This is a pure decision layer, not live-action authorization.
- Offline tests cover parsing, adapter restrictions, Japanese name preservation, malformed payloads, HP safety, fail-closed readiness, and planner-level dispatch gating.

## Evidence and limitations
- GitHub Actions CI passed for commit `2fdf715fe697b08bd9609002f852631d554ccd46` (run 39788837921): https://github.com/wosafety0/KancolleBot/actions/runs/39788837921
- CI passed for parent commit `c24b46e3c0e28fe9c1c0a1aa131f189f1ea37851` (run 39790340470). The combined planner/gate entry point in the current commit requires its own CI verification.
- CI success is not runtime verification against the user's local POI instance.
- A previous user-provided local diagnostic confirmed `/fleets` and `/ships` returned HTTP 200, with a top-level fleet array and a ship object keyed by IDs. The repository does not yet contain an authoritative redacted raw `/ships` sample confirming name/current-HP/max-HP keys.
- An uploaded snippet mentions `api_name`, `api_nowhp`, and `api_maxhp`, but this is not proof of the current runtime schema. These names appear only in synthetic tests until verified.
- The new planner-level gate converts an unsafe or unverifiable dispatch recommendation to `wait`; callers must use this wrapper. It does not connect to an action adapter or authorize execution. Supply, repair/docking, mission composition, and fleet rotation checks are not yet backed by real observations; the gate safely blocks by default.

## Safety boundaries
- Cloud CI does not connect to local POI.
- No collection, dispatch, supply, battle, or other gameplay action has been executed.
- A dispatch recommendation is not authorization to execute. Live gameplay actions remain disabled by default.

## Next steps
1. Capture a minimal, redacted raw `/ships` record from the local read-only endpoint and verify actual field names.
2. Add an explicit reviewed field map and a fixture test using that evidence.
3. Implement real supply, repair/docking, composition, and rotation observations; do not manually mark these gates passed without evidence.
4. Keep live-action authorization as a separate mandatory gate.
5. Continue KCV (非提助手)-first call-chain research, using KCA only for labeled supplementary comparisons.
