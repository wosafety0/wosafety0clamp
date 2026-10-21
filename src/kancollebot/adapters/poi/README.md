# POI Adapter Contract for Expedition Automation

Status: MCP_READ_PATH_IDENTIFIED; WINDOWS_RUNTIME_VALIDATION_PENDING

## Verified integration facts

The official Poi developer documentation states that plugins can access Poi's Redux store and that player state is exposed under `store.info`. Relevant documented data includes:
- `store.info.fleets`: four fleet entries, indexed from zero.
- `store.info.ships`: player-owned ship data.
- `store.const.$missions`: expedition master data.
- Redux request/response actions use `@@Request/kcsapi/<api>` and `@@Response/kcsapi/<api>`.
- Plugins should unsubscribe observers when disabled.

Sources:
- Poi API introduction: https://dev.poi.moe/docs/api/introduction
- Poi Redux utilities and state structure: https://dev.poi.moe/docs/api/redux

A public third-party plugin, `momocow/poi-plugin-nanodesu-mcp`, exposes live Poi state over MCP Streamable HTTP:
- Repository: https://github.com/momocow/poi-plugin-nanodesu-mcp
- Default endpoint: `http://127.0.0.1:12450/mcp`
- Health endpoint: `GET http://127.0.0.1:12450/health`
- Read tools include `poi_describe` and `poi_get`; `poi_get` accepts paths such as `info.fleets`, `info.ships`, and `info.resources`.
- The plugin reads the live Redux store and documents a loopback-only default host.

The MCP read path is therefore identified from public source and documentation. This is not yet proof that the plugin loads successfully in the user's Windows Poi 12.0.1 installation or that the returned state shape matches our normalizer.

## Integration approach

Prefer the existing MCP plugin over implementing another Poi Redux observer:
1. Python connects only to the loopback MCP endpoint; never bind or expose a service to the LAN.
2. Initialize the MCP Streamable HTTP session and call `tools/list` / `tools/call` using the protocol implementation, rather than scraping Poi cache files.
3. Allowlist read-only tools needed for state inspection (`poi_describe`, `poi_get`, and optionally `poi_lookup`). Do not call `poi_hensei_save`; although it does not send game requests, it writes to Poi plugin state.
4. Query only the minimum state required for fleet normalization. Treat empty, partial, malformed, or uninitialized state as unavailable rather than an empty fleet.
5. Normalize the result into the existing Python domain types and attach a local observation timestamp plus source metadata.
6. On connection failure, protocol errors, or missing fields, fail closed and prohibit dispatch.

## State normalization requirements

The Python domain layer must receive normalized snapshots, never raw Redux objects. The adapter must:
1. Detect an initialized and logged-in store; empty or partial state is unavailable, not a valid empty fleet.
2. Normalize fleet IDs, ship IDs, mission IDs, return-time values, damage, fuel, and ammunition based on observed sample payloads—not guessed field names.
3. Attach an observation timestamp and source/version metadata.
4. Emit an unavailable/stale result when required fields are missing or inconsistent.
5. Never interpret a timer reaching zero as proof that the game has refreshed.
6. Keep read and action capabilities separate.

## Action boundary

No live dispatch adapter is implemented or approved by this document. The KCV (非提助手) official site says its automation uses simulated mouse operations and supports a Poi mode; this is evidence for the reference product's approach, not proof that our plugin can reuse its private implementation.

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
- Public MCP read path: identified; Python transport/client and normalization remain to be implemented.
- Windows Poi 12.0.1 plugin installation and runtime read: not verified.
- Real UI dispatch adapter: pending.
- Local runtime and end-to-end expedition cycle: not verified.
