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


## Targeted KCV (非提助手) expedition call-chain findings — 2026-10-22

Repository examined: user-designated `wosafety0/KCV`, branch `upload/kcv-reference-20261022-174555`, commit `5f67fd8c134665d64919445edea061b3ec5eaa06`. These are observations from the exact files below; they do not establish a POI API contract.

- `KCA_Decompiled/KanColleViewer/Grabacr07.KanColleViewer.Actions/AutoExpedition.cs`, blob `5515e3a8d781c775d0071c177bc3f5c887704b38`: lines 437–445 subscribe to successful `api_req_mission_result` observations; lines 504 onward show a stop path that clears running/queued state; lines 538 onward log an expedition error and run a reset action; lines 1790–1835 process a finished-expedition queue and consult the fleet's remaining expedition time; lines 2073 onward validate configured targets and construct an `ActExpedition` action.
- `KCA_Decompiled/KanColleViewer/Grabacr07.KanColleViewer.Actions/ActFinishExpedition.cs`, blob `e0686f2642fd5443271b211ce7a12dd734b0fdfd`: lines 30–59 build a coordinate-click sequence for Supply/Homeport and wait for `api_req_mission_result` with a 30-second queue timeout. Lines 64 onward start that queue and map its completion/error events to the action state. This is a UI-driven action sequence, not a reusable POI command API.
- `KCA_Decompiled/KanColleViewer/Grabacr07.KanColleViewer.Actions/ActExpedition.cs`, blob `d497d4b8582db9ed5666e3f91c9adba516ae3408`: lines 420–465 build a queue including sortie/expedition navigation, an `api_get_member_mission`-observed click step, fleet selection, expedition selection, availability checking, and confirmation. Queue completion advances the next fleet; queue errors set the action state to Error.

### Consequences for our Python implementation

1. Keep action issuance separate from verification. A UI queue reaching its own Finished state is not enough to prove the desired fleet state transition.
2. Preserve the independent pre/post observation verifier: collect requires completed -> idle; dispatch requires idle -> underway for the requested mission. A timeout, stale/mismatched observation, or unconfirmed transition stops the cycle and never triggers a blind retry.
3. Before any live adapter is designed, establish which of these UI actions can be driven safely through the actual local POI environment. The inspected code uses configured screen coordinates and event observations; it does not prove that POI exposes equivalent action endpoints.
4. This is intentionally a narrow expedition-path review, not a broad class inventory or battle-state-machine reverse engineering. KCA remains a supplementary reference only when a specific unresolved question needs comparison.
