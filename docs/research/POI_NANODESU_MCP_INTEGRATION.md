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


## Independent fact-check of supplemental claims (2026-10-22)

This section separates source-confirmed facts from claims that still require a target-machine experiment. The original supplemental summary is not treated as an authoritative source.

### Claim: "Poi always stops rendering when minimized"

**Verdict: plausible risk, but the absolute wording is not verified.**

- Electron's official BrowserWindow documentation says the Page Visibility API reports a window as hidden when minimized, and `backgroundThrottling` defaults to true; this option affects animation/timer throttling and frame drawing. Source: https://www.electronjs.org/docs/latest/api/browser-window
- This supports treating minimized rendering as a risk, but it does not establish that Poi 12.0.1 always freezes every renderer or that CDP screenshots must be stale.
- Design decision: require the initial local test to compare repeated CDP captures before/during/after minimization and correlate them with an observable game/UI state change. Until then, do not promise reliable minimized operation. A separate Windows virtual desktop is only a possible workaround, not a verified requirement or solution.

### Claim: "Always use CDP screenshots; never use desktop-wide capture"

**Verdict: good privacy and targeting policy for this project; the claimed 'official Poi Agent Skill' provenance is unverified.**

- The search performed for this review did not find an official Poi-owned Agent Skill containing the quoted rule. Do not cite that attribution unless a precise official URL/repository path is supplied and checked.
- CDP's `Page.captureScreenshot` is a renderer-target capture mechanism, but success alone proves only that a screenshot response was returned. It does not prove that the frame is fresh, that the game is logged in, or that the correct Poi target was selected.
- Project policy remains: use CDP renderer capture for visual evidence and avoid `CopyFromScreen` / full-desktop screenshots to reduce unrelated-window privacy exposure. Validate target selection against the actual Poi 12.0.1 `/json/list` output.

### Claim: "Node 22+ has built-in WebSocket"

**Verdict: verified with version nuance.**

Node's official global API history says WebSocket was added in Node 21.0.0 and 20.10.0, ceased to be experimental in 22.4.0, and is stable in 22.4.0 onward. Source: https://nodejs.org/api/globals.html
Therefore "Node 22+" is a safe practical baseline, but it is not the earliest version with the API.

### Claim: Poi Redux state may be cached and may be nearly empty before login

**Verdict: verified by official Poi developer documentation.**

Poi's Redux documentation explicitly states that store data is synced in localStorage, distinguishes master data from player-specific data, and warns that the store may be nearly empty when Poi is fresh or not logged in. It documents `store.info.fleets` as a zero-based array of length four and `store.info.ships`, `store.info.resources`, and `store.info.quests`. Source: https://dev.poi.moe/docs/api/redux
This supports a hard safety rule: a non-empty cached fleet/ship payload alone is not proof of an active game session. The exact live-session signal still needs to be selected and tested.

### Claim: `minagi/poi-plugin-mcp` offers HTTP read endpoints and port discovery

**Verdict: verified in that repository's README; Poi 12.0.1 compatibility is unverified.**

The public README currently documents fork tag `v0.2.30-minagi.1`, loopback listener default `127.0.0.1:17777`, `GET /health`, a port file at `%USERPROFILE%\\.poi-mcp\\port`, and read endpoints including `/fleets`, `/ships`, `/resources`, and `/quests`. Source: https://github.com/minagi/poi-plugin-mcp/blob/main/README.md
The README also documents optional WebView input and debug-evaluation features; those are outside KancolleBot's read-only prototype and must not be enabled or called. The verifier only probes documented read endpoints, but actual installation/runtime compatibility is not established. The repo is a fork of npm `poi-plugin-mcp` 0.2.29 and has a different install procedure from the upstream npm package; do not treat those as interchangeable.

### Additional candidate discovered during review

The Poi organization has a public repository, `poooi/plugin-server`, describing a versioned, authenticated, read-only JSON/WebSocket/MCP projection with default loopback port 8765. Source: https://github.com/poooi/plugin-server
This repository is a separate candidate—not the same project as `minagi/poi-plugin-mcp` or `momocow/poi-plugin-nanodesu-mcp`. It should be evaluated from its own README, security model, package requirements, and Poi-version compatibility before any selection. It is not being adopted automatically.

### Claim: "Randomized human-like actions reduce automation detection"

**Verdict: unsupported as a safety guarantee.**

No evidence has been established here that timing jitter or curved cursor paths make this game automation safe or undetectable. They must not be presented as anti-detection guarantees. Engineering priority remains correct state, bounded actions, idempotency/duplicate prevention, stop conditions, and verifiable post-action outcomes.

### Fact-check outcome

- Confirmed from primary documentation: Poi Redux data paths and localStorage persistence; Electron minimized/visibility and background-throttling behavior; Node built-in WebSocket version history; alternative bridge README's documented endpoints and port file.
- Not established: exact Poi 12.0.1 minimization freeze; stale/fresh CDP frame behavior in this app; the claimed official Poi visual-verification skill; successful runtime compatibility of either bridge; active-login signal; any anti-detection benefit from randomized input.
- No plugin installation or live game action was performed as part of this review.

## Session-signal source audit (2026-10-22)

### Nanodesu MCP health contract: confirmed insufficient

Inspected the current public source in `momocow/poi-plugin-nanodesu-mcp`:
- `src/server.ts` implements `storeReady()` by calling the configured store accessor and checking that the returned object contains an `info` property.
- `GET /health` returns `status`, server name/version, port, and `storeReady`. It does **not** return `sessionReady`, a login flag, a current-game-connection flag, or a state freshness timestamp.
- `index.ts` obtains Poi's Redux store through `resolveGetStore` and passes it to the MCP server. This establishes store access, not active game login.

Sources:
- https://github.com/momocow/poi-plugin-nanodesu-mcp/blob/main/src/server.ts
- https://github.com/momocow/poi-plugin-nanodesu-mcp/blob/main/index.ts
- https://github.com/momocow/poi-plugin-nanodesu-mcp/blob/main/src/poi.ts

### Official Poi Redux state audit: player data is not a session proof

Inspected Poi's current public Redux source:
- `views/redux/info/index.ts` includes `info.basic`, `info.server`, `info.fleets`, and other player state.
- `views/redux/info/server.ts` initializes the server fields (`ip`, `id`, `name`) to null and updates them on a server-ready action.
- `info.basic.api_member_id` is used when handling the `api_get_member/require_info` response to detect an account change and clear stale player state. It is a useful identity/state-consistency field, but because Redux is persisted, a non-null member ID alone cannot prove that a current session is active.
- A populated fleet list, ship list, member ID, or server selection is therefore not accepted as live-session evidence.

Sources:
- https://github.com/poooi/poi/blob/master/views/redux/info/index.ts
- https://github.com/poooi/poi/blob/master/views/redux/info/server.ts
- https://dev.poi.moe/docs/api/redux

### Decision

Do not synthesize `sessionReady` from the existing `/health` response or infer it from cached Redux fields. The current adapter's `sessionReady is True` requirement is intentionally fail-closed and will block the unmodified Nanodesu bridge.

The next viable options are:
1. Validate a fresh, current-session signal from the actual Poi 12.0.1 renderer/network lifecycle and expose only a minimal boolean plus evidence age through a separately reviewed read-only bridge; or
2. Keep state reads non-actionable until a user-confirmed session check has been performed, clearly labelled as operator-confirmed rather than machine-verified.

Neither option is implemented or runtime-verified by this source audit. Do not install a plugin, inspect or print authentication tokens, call game endpoints directly, or perform live game actions as part of this investigation.

### Evidence status after source audit

- Nanodesu `/health` schema inspected: confirmed no session-validity field.
- Poi Redux account/server fields inspected: confirmed they describe stored application/player state, not sufficient proof of an active session.
- Reliable active-session signal in Poi 12.0.1: not yet established.
- Existing session gate: retained; no code relaxation.

## Read-only projection bridge comparison (2026-10-22)

Inspected the public `poooi/plugin-server` source and README:
- It exposes authenticated read-only snapshots and dataset endpoints, with WebSocket revisioned updates and an MCP endpoint.
- The server subscribes to Poi's Redux store and emits a new projection when the serialized projected datasets change.
- Its revision/update mechanism is a useful signal that the projected store changed after the bridge initialized, but it does not identify which game API response caused the change, prove that the game session remains active, or distinguish every legitimate live update from a store reset/replay.
- The README states that validation uses a Poi-shaped host harness and does not claim a graphical Poi runtime smoke test. Compatibility with the target Poi 12.0.1 installation remains unverified.
- The bridge requires a bearer token. Any future client must read it from a protected local configuration path, never print it, never place it in a URL, and never relax the loopback restriction. No token was read or created during this source review.

Sources:
- https://github.com/poooi/plugin-server
- https://github.com/poooi/plugin-server/blob/main/src/server.ts
- https://github.com/poooi/plugin-server/blob/main/src/projection.ts
- https://github.com/poooi/plugin-server/blob/main/tests/server.test.ts

### Decision after comparison

Do not switch bridges or install anything automatically. Neither the Nanodesu health contract nor the inspected plugin-server projection contract supplies a definitive active-login signal. Revisioned updates may support a future freshness monitor, but must not be renamed or interpreted as `sessionReady`.

A stronger signal should be tied to an explicitly observed, recent game response or a verified UI/session transition, with bounded age and invalidation on disconnect, account change, or bridge restart. The exact event and lifecycle handling must be proven against the target Poi 12.0.1 runtime before it can unblock actionable observations.

### Evidence status update

- Nanodesu bridge: store readiness only; no session signal.
- Poi Redux account/server fields: persisted application state; insufficient as active-session proof.
- Poi plugin-server: authenticated read-only projection and revisioned store-change notifications; useful for change detection, not session proof.
- Runtime compatibility and reliable current-session signal: still unverified.
- No bridge was installed; no credentials were accessed; no game action was executed.

