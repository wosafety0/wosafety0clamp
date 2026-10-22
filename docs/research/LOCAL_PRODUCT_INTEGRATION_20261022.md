# Local Product Files Integration — 2026-10-22

## Source review

Reviewed the uploaded files under `upload/local-product-files-20261022/` on the research branch:

- `state_parser.py` contains prose mixed with Python code and is not an importable module as uploaded.
- `scheduler.py` combines scheduling, a synchronous action queue, and an unverified CDP/WebSocket reader. It is not safe to connect directly to the live game.
- `guard.py` contains useful allow-list and failure-lock concepts. The existing package policy remains the canonical live-action gate; live actions stay disabled by default.

## Integrated now

- Added `src/kancollebot/core/fleet_parser.py` as a pure parser for observed fleet payloads.
- Added `src/kancollebot/core/expedition_planner.py` as a pure planner that recommends at most one action.
- Added offline tests for top-level JSON arrays, slot preservation, unknown mission state, malformed slots, collection priority, and dispatch recommendations.

## Safety and limitations

- No POI network connection is opened by these modules.
- No collection, dispatch, supply, battle, or other gameplay action is executed.
- Missing/invalid expedition state is treated as unknown, never as idle.
- The planner does not yet verify ship HP, repair status, supply levels, mission fleet requirements, or fleet rotation constraints. A dispatch recommendation is not authorization to execute.
- The uploaded files remain in the repository as source material and are not imported by runtime code.

## Next steps

1. Run the repository CI workflow for this branch and inspect its result.
2. Connect the parser to a separately verified read-only POI adapter.
3. Add safety preconditions for ship state, supply, fleet composition, and rotation.
4. Only after those gates and failure recovery are tested, design a live action adapter; live actions remain disabled by default.
