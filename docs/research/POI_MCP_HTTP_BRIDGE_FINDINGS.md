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
