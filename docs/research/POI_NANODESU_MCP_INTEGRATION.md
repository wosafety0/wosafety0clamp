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


## Additional runtime constraints and alternative bridge (2026-10-22)

### Window/rendering and visual verification

Treat minimized-window rendering behavior as a high-priority runtime assumption to test, not as a universal fact until reproduced on the target Windows/Poi build. The bot must not depend on screenshots changing while Poi is minimized.

Visual verification should use the Poi renderer's Chrome DevTools Protocol (CDP), not whole-desktop capture:
1. Launch the existing Poi instance with remote debugging enabled only when needed; do not launch a second instance blindly because Poi enforces a single-instance lock.
2. Discover the intended renderer target from `http://127.0.0.1:9222/json/list`; select the Poi main renderer deliberately and reject unrelated targets.
3. Capture using `Page.captureScreenshot`.
4. Verify freshness across repeated captures (image hash or decoded image comparison) and correlate with a state change; a successful screenshot response alone does not prove the game is rendering or that the frame is current.
5. Never use desktop-wide `CopyFromScreen` for bot verification.

CDP is a visual evidence channel, not a replacement for structured state. Prefer the data bridge for fleet/ship/resource state and use CDP for session/UI evidence and post-action visual confirmation.

### Alternative: minagi/poi-plugin-mcp

Repository: https://github.com/minagi/poi-plugin-mcp

Public README inspected on 2026-10-22. This is a Minagi-maintained fork based on upstream `poi-plugin-mcp` 0.2.29; its README documents version `v0.2.30-minagi.1`, a loopback HTTP service defaulting to `127.0.0.1:17777`, a `GET /health` endpoint, `%USERPROFILE%\\.poi-mcp\\port` port file, REST-style read endpoints such as `/fleets`, `/ships`, `/resources`, `/quests`, and MCP at `/mcp`.

Sources:
- https://github.com/minagi/poi-plugin-mcp
- https://github.com/minagi/poi-plugin-mcp/blob/main/README.md

Advantages for an initial prototype:
- Plain HTTP GET endpoints can be queried with Python standard-library `urllib`; no `requests` or MCP SDK installation is required.
- The README documents a fleet endpoint and other structured endpoints, reducing initial tool-discovery friction.
- Port discovery can use the documented port file, with the default port only as fallback.

Security and compatibility caveats:
- This is a fork and not proof of compatibility with the user's Poi 12.0.1 installation; verify the exact release and plugin loading before relying on it.
- The README also documents optional WebView input and debug JavaScript evaluation. KancolleBot must not enable or call those features in the read-only prototype.
- Use only loopback addresses; reject unexpected hosts and ports read from the port file. Treat all returned data as untrusted input and cap response sizes/timeouts.
- The bridge's `/health` response proves service health only, not active game login or fresh game state.
- Do not install it automatically. Compare it with nanodesu-mcp and choose after local compatibility and data-shape checks.

### Session validity and stale cache

Poi can restore persisted Redux data at startup. A non-empty fleet/ship payload is therefore insufficient evidence that the game is currently logged in or at the home port. The adapter must represent these as separate signals:
- bridge/store readiness;
- game-session or home-port evidence, if exposed by a documented field or UI;
- observation timestamp/freshness;
- fleet/ship payload shape and completeness.

If session evidence is absent or contradictory, return an unavailable/unknown state and prohibit dispatch. Never infer successful login from cached ship counts alone.

### Window lifecycle and single-instance behavior

Before attempting any debug launch, inspect whether Poi is already running. Avoid starting a second instance with a new CDP port and assuming it became the active instance. The runtime verifier should report the existing process and whether CDP is actually reachable, without killing processes or changing global settings.

### Human-like timing

Timing variation may be used as conservative interaction pacing and to avoid brittle fixed delays, but it is not a guarantee against detection and must not be framed as such. Safety should come from state checks, bounded actions, explicit stop conditions, and post-action verification—not from randomized cursor paths or click points. Keep live actions disabled until the complete read/plan/act/confirm chain has been tested.

## Revised implementation order

1. Finish the standard-library read-only bridge verifier and offline tests.
2. Add a small read-only HTTP probe for `poi-plugin-mcp` (`/health`, `/fleets`, `/ships`, `/resources`) with loopback/port validation; do not install the plugin automatically.
3. Add a CDP screenshot freshness probe using Node's built-in WebSocket where supported; target discovery and repeated-frame comparison must be explicit.
4. Run local compatibility checks in the user's existing Poi 12.0.1 environment and capture sanitized fixture data.
5. Implement state normalization only after the fixture and session-validity evidence are available.
6. Keep live dispatch disabled until UI interaction and post-action confirmation are validated.

## Evidence status update

- GitHub Actions run for MCP client fix `e5ba78e26ec3f26f5ce23e5c9df0a037437c1e2c`: passed (run 39632888334).
- Alternative bridge public README: inspected; runtime compatibility not verified.
- CDP screenshot behavior when Poi is minimized: not verified on target machine.
- Cached-state/login distinction: accepted as a safety requirement; exact runtime signal remains to be identified.
- Windows Poi 12.0.1 runtime, actual fleet payload, CDP screenshot freshness, and end-to-end expedition cycle: still pending local validation.
