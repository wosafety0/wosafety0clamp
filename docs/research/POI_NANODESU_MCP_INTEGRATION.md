# POI Nanodesu MCP Integration Research

Date: 2026-10-22
Status: PUBLIC_SOURCE_INSPECTED; WINDOWS_RUNTIME_PENDING
Target: Poi 12.0.1 on Windows; Python local client

## Findings

### Official Poi Redux documentation

Poi documents a global `getStore(path?)` accessor and identifies `info.fleets`, `info.ships`, and `const.$missions` as useful state paths. The documentation says direct global store reads are convenient for debugging and recommends selectors for production plugin code.

Source: https://dev.poi.moe/docs/api/redux

### Existing bridge plugin

Repository: https://github.com/momocow/poi-plugin-nanodesu-mcp

Public source inspected:
- `index.ts`: plugin lifecycle calls `resolveGetStore(window)`, starts MCP only in Poi's main window, and reports startup failures in its status panel.
- `src/poi.ts`: reads `window.getStore`; fails explicitly when it is unavailable.
- `src/server.ts`: uses the Model Context Protocol SDK's Streamable HTTP transport. The default host is `127.0.0.1`, default port is `12450`, MCP endpoint is `/mcp`, and `GET /health` reports whether the store is ready.
- Read tools include `poi_describe`, `poi_get`, and `poi_lookup`.
- `poi_hensei_save` can be registered if optional dependencies are present. It writes Poi plugin state rather than sending game requests; the KancolleBot Python client must nevertheless use an explicit read-only tool allowlist and never invoke it.

Source files:
- https://github.com/momocow/poi-plugin-nanodesu-mcp/blob/main/index.ts
- https://github.com/momocow/poi-plugin-nanodesu-mcp/blob/main/src/poi.ts
- https://github.com/momocow/poi-plugin-nanodesu-mcp/blob/main/src/server.ts

## Integration decision

Reuse this bridge instead of implementing a duplicate Redux observer in our own plugin. Implement the Python side with a protocol-correct MCP Streamable HTTP client and keep the transport separate from fleet-state normalization.

The Python adapter should:
1. Default to loopback endpoint `http://127.0.0.1:12450/mcp`.
2. Use short connection/read timeouts and never connect to non-loopback addresses by default.
3. Initialize MCP and negotiate the session before calling tools.
4. Call only read-only tools (`poi_describe`, `poi_get`, optionally `poi_lookup`).
5. Parse MCP text content as JSON only when the tool response actually contains JSON; preserve explicit tool errors.
6. Treat connection errors, missing store, empty/uninitialized store, truncated results, and unknown field layouts as unavailable state.
7. Normalize only from actual captured payload fixtures; do not guess mission or return-time fields.
8. Keep all game actions disabled until a separately validated UI interaction path and post-action state confirmation exist.

## Validation plan

Offline:
- MCP initialize/notification/call request construction tests.
- Handle JSON-RPC errors, HTTP errors, missing session headers, malformed JSON/SSE, tool-level errors, and timeouts.
- Test state normalization using synthetic fixtures explicitly labelled as fixtures.

Local runtime, not yet performed:
1. Install/load the plugin in the user's existing Poi 12.0.1 environment using its supported plugin process.
2. Check `GET /health` and confirm `storeReady: true`.
3. Call `poi_describe` and inspect `info.fleets` without dispatching any action.
4. Capture a minimal sanitized response fixture and normalize it.
5. Repeat after a game-state refresh to establish that responses reflect live store changes.

## Evidence status

- Public documentation/source inspected: confirmed.
- Windows Poi 12.0.1 compatibility: needs runtime validation.
- Python MCP client implemented: pending.
- Real fleet payload normalized: pending.
- Live dispatch: not implemented and remains disabled.
- End-to-end expedition cycle: not verified.
