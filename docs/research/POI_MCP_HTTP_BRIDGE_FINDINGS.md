# POI MCP HTTP Bridge Findings

**Date:** 2026-10-24  
**Repository branch:** `dev/kcv-research-python-direction`  
**Scope:** Read-only inspection of the locally installed `poi-plugin-mcp` source. No local plugin files were modified; no network requests or game actions were performed by the local inspection script, according to its report.

## Confirmed from the supplied local inspection output

- Plugin directory: `%APPDATA%/poi/plugins/node_modules/poi-plugin-mcp`.
- Package metadata shown in the inspection output identifies `poi-plugin-mcp` version `0.2.29`, with `index.js` as its main entry.
- `index.js` wires `createBridgeController()` to telemetry getters and calls `controller.load()` from `pluginDidLoad()`. It registers `telemetry.handleGameResponse` on the `game.response` event.
- `lib/settings.js` line 6 declares the default port as `17777`; the nearby settings output shows `enabled: true` and `inputEnabled: false`.
- `mcp-server.js` lines 110–146 construct a DevTools-injected HTTP server script. The generated script binds to `127.0.0.1:17777`, exposes `/health`, `/fleets`, `/ships`, `/equipment`, `/resources`, `/quests`, `/airbase`, `/basic`, and `/all`, and reads values from the POI store via `window.getStore()`. The file also contains an older commented version around lines 21–54.
- The generated script's `/fleets` handler serializes `st.info.fleets`; `/ships` serializes `st.info.ships`. This is direct source evidence for the intended data mapping in that injected-server path.
- `lib/poi-http-bridge.js` contains fleet/ship processing code. The scan output shows:
  - around lines 1587–1591, `findHeavilyDamaged(fleets)` iterates `main` and `escort` fleet entries;
  - around lines 1624–1627, ship output includes `instanceId` from `raw.api_id` and `masterId` from `raw.api_ship_id`;
  - around lines 1707–1710, extracted master data includes `constants.$ships`, equipment and ship types;
  - around lines 1769–1778, event data is built from `store.info.ships`, with `api_id` and `api_ship_id` kept as distinct identifiers.
- `lib/poi-interaction-recorder.js` around lines 1168–1183 reads `info.ships` and `info.fleets`, captures each ship's `api_slot`, and preserves each fleet's `api_ship` order.
- The targeted keyword scan returned no `flagship` matches in the scanned plugin source files. This means the scan did not find an explicit `flagship` identifier; it does **not** prove there is no equivalent logic under another name.

## Interpretation and limits

1. The evidence supports `127.0.0.1:17777` as the plugin's configured local bridge port and `/fleets` / `/ships` as routes in the DevTools-injection script. The active plugin also has a separate controller/bridge implementation; do not assume every route is served by the standalone `mcp-server.js` process without checking the controller startup path and runtime process ownership.
2. The API payloads in the observed responses are consistent with direct serialization of POI `store.info.fleets` and `store.info.ships` on the injection path. Any additional normalization performed by `lib/poi-http-bridge.js` should be documented separately from those raw endpoints.
3. `api_id` is the ship-instance identifier in the observed mapping, while `api_ship_id` is the master ship ID. Do not use `api_ship_id` to look up fleet slot references; the fleet's `api_ship` entries correspond to ship instance IDs.
4. The supplied response data included `api_flagship: "0"`, but the source scan did not find explicit `flagship` handling. Treat that field as unverified/possibly legacy. Do not infer flagship status solely from the field value or assume the first `api_ship` element is the flagship without additional source/runtime evidence.
5. This is source inspection evidence, not proof of a live API request in this documentation step, not proof that data has been ingested by the Python bot, and not validation of real gameplay automation.

## Next read-only verification

- Trace `lib/bridge-controller.js` from `controller.load()` to the active HTTP bridge start function.
- Inspect `lib/settings.js` and the route-registration section of `lib/poi-http-bridge.js` to distinguish the active plugin bridge from the standalone `mcp-server.js` injection/fallback path.
- If flagship selection is needed, trace the POI fleet state/model and the meaning of the relevant fields before implementing a rule. Keep game actions disabled.

## 2026-10-24 targeted active-bridge trace

The next local read-only report supplied the following additional source and runtime evidence:

- `lib/bridge-controller.js` imports `createPoiDataBridge` from `./poi-http-bridge` (reported line 2).
- `createBridgeController()` uses the configured settings path and initializes settings (reported lines 11–16). Its `createCurrentBridge()` passes `settings.port` and `settings.inputEnabled` into the bridge factory (reported lines 28–53).
- `ensureStarted()` creates the bridge if absent and calls `await bridge.start()` (reported lines 81–85). `stopCurrentBridge()` calls `runningBridge.stop()` (reported lines 98–104).
- `lib/poi-http-bridge.js` declares `DEFAULT_PORT = 17777` (reported line 28). Its request handler maps `/fleets` to `info.fleets || []` and `/ships` to `info.ships || {}` (reported lines 871–875).
- The bridge implementation creates the HTTP server with `http.createServer(handleRequest)` and calls `server.listen(configuredPort, '127.0.0.1', ...)`; it records the actual bound port in a port file (reported lines 941–955).
- The local listener snapshot reported both `127.0.0.1:17777` and `127.0.0.1:12450` owned by POI renderer PID `21168`, executable `D:\\Games\\poi\\poi.exe`, with the Electron renderer command line. This correlates the configured bridge port with a listener inside the POI renderer process.
- The targeted script reported `ERROR_COUNT: 0`, no HTTP/network requests, no game actions, no process termination, and no file modifications. Clipboard verification was deliberately not performed by that script.

### Updated interpretation

The active bridge implementation is no longer supported only by the standalone `mcp-server.js` injection-script evidence: the controller imports the `createPoiDataBridge` factory, passes its configured port, and has a start path that invokes `bridge.start()`; the bridge source contains the actual HTTP server and route handlers. The listener snapshot is consistent with this bridge running inside the POI renderer.

The supplied output does not include the controller's complete `load()` implementation or the full `index.js` lifecycle context in this particular report, so the final conditional path from `pluginDidLoad()` through settings enablement to `ensureStarted()` has not been shown end-to-end in one captured excerpt. PID ownership alone cannot distinguish multiple servers in the same renderer, and it does not identify which component owns port `12450`. Therefore, classify the active `17777` bridge attribution as **strongly correlated, not end-to-end runtime-proven**; `12450` remains unexplained.

### Next read-only check

If stronger attribution is needed, capture the complete `load()` method and adjacent lifecycle methods from `lib/bridge-controller.js`, plus `pluginDidLoad()` from `index.js`, with line numbers. Do not send HTTP requests or perform game actions.

