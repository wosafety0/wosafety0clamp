# POI Adapter Contract for Expedition Automation

Status: RESEARCHED_READ_PATHS; CONCRETE_ADAPTER_NOT_IMPLEMENTED

## Verified integration facts

The official Poi developer documentation states that plugins can access Poi's Redux store and that the player state is exposed under `store.info`. Relevant documented data includes:
- `store.info.fleets`: four fleet entries, indexed from zero.
- `store.info.ships`: player-owned ship data.
- `store.const.$missions`: expedition master data.
- Redux request/response actions use `@@Request/kcsapi/<api>` and `@@Response/kcsapi/<api>`.
- Plugins should unsubscribe observers when disabled.

Sources:
- Poi API introduction: https://dev.poi.moe/docs/api/introduction
- Poi Redux utilities and state structure: https://dev.poi.moe/docs/api/redux

These facts establish a plausible **read-only state adapter path**, not a verified action interface. Store shape and event details must still be checked against the installed Poi version before implementation.

## Adapter boundary

The Python domain layer must receive normalized snapshots, never raw Redux objects. The adapter must:
1. Detect an initialized and logged-in store; empty or partial state is unavailable, not a valid empty fleet.
2. Normalize fleet IDs, ship IDs, mission IDs, return-time values, damage, fuel, and ammunition.
3. Attach an observation timestamp and source/version metadata.
4. Emit an unavailable/stale result when required fields are missing or inconsistent.
5. Never interpret a timer reaching zero as proof that the game has refreshed.
6. Unsubscribe listeners and stop emitting updates when the plugin unloads.

## Action boundary

No dispatch action implementation is approved by this document. The KCV (非提助手) official site says its automation uses simulated mouse operations and explicitly supports a Poi mode; this is evidence for the reference product's approach, not proof that our plugin can reuse its private implementation.

Reference: https://kancolleassistant.mechland.cn/

Before a concrete action adapter is implemented, verify:
- the installed Poi version and plugin lifecycle;
- a supported UI interaction path that does not fabricate or directly send game-server requests;
- how to detect that the UI action was accepted;
- a fresh state read after every action;
- a safe stop path and recovery from ambiguous timeouts.

Do not send game API requests directly from Python or the plugin. Do not infer a supported command interface from Redux's observed request/response events.

## Current implementation status

- Offline planner: implemented and CI-tested.
- Fleet rotation: implemented and CI-tested.
- One-cycle coordinator and fail-closed safety gates: implemented and CI-tested with a fake adapter.
- Read-only Poi adapter: pending version-specific validation.
- Real UI dispatch adapter: pending.
- Local runtime and end-to-end expedition cycle: not verified.
