# Passive Poi Port Response Observer

## Current status

- Current branch: `dev/expedition-closed-loop-mvp`; the passive observer is wired through `BackgroundPortSnapshotProvider` into the read-only continuous-monitor composition, not the production dispatch runner.
- **Natural response capture:** on 2026-11-02, the integrated `PoiExpeditionReader` matched a naturally captured, non-cache HTTP 200 `/kcsapi/api_port/port` witness against a contemporaneous Poi MCP read after 31 polls / 35.55 seconds. This verifies that bounded reader/provider path for that observed session; it does not establish long-term freshness or unattended dispatch readiness.
- **Child-only witness mode:** when `KCB_POI_FRESHNESS_WITNESS_STDOUT=1`, the observer returns the compact witness through stdout/in-memory handoff and does not create the Desktop snapshot directory or write a file. Standalone snapshot-export mode retains its explicit compact-file output.
- **Target discovery:** the CDP target-list response must parse as a JSON array. A valid JSON object/scalar now fails with `TARGET_DISCOVERY_FAILED` before any array filtering.
- **Production boundary:** the production freshness/dispatch gate remains fail-closed. The read-only monitor and optional refresh navigation do not authorize dispatch, reward settlement, repair, or resupply.

## Safety contract

- Reads only `http://127.0.0.1:9222/json/list` and a credential-free loopback CDP WebSocket.
- Uses `Network.enable`, `Network.responseReceived`, `Network.loadingFinished`, and `Network.getResponseBody`.
- Does not call `Runtime.evaluate`, page JavaScript, game functions, or any input method.
- Does not issue a game API request. It waits up to 120 seconds for a natural response already initiated by the game client.
- Rejects non-200 responses, cached/service-worker/prefetch responses, failed API results, duplicate IDs, and malformed fleet/ship records.
- Child-only witness mode writes no snapshot or Desktop file; explicit standalone snapshot-export mode saves only a compact subset to `Desktop/KancolleBot-Port-Snapshots`. Neither mode saves the full raw response body, and console output excludes ship/fleet payload values.
- The observer alone does not modify the production freshness gate. The optional in-memory provider can feed a witness to `PoiExpeditionReader`, which sets `source_freshness_verified` only after bounded-age and exact mutable-field matching; the default reader without a provider remains unverified.

## Output and limits

The JSON artifact contains a local response-receipt timestamp, a response-body SHA-256, compact fleet and ship records, and a safety summary. The timestamp is the local observer's receipt time, not a server timestamp.

A successful capture establishes that the current Poi page exposed a fresh, non-cache HTTP 200 port response with the expected fields at that moment. The 2026-11-02 integrated reader matched one natural response against a contemporaneous Poi MCP read after 31 polls / 35.55 seconds. This does **not** establish long-term freshness, atomic consistency across future reads, production dispatch integration, or full expedition-loop correctness. CDP target discovery rejects a valid JSON response whose root is not an array before filtering. If no natural port response occurs during the wait window, the observer exits without producing a witness; it must not trigger a refresh or any game action.

## Read-only runtime preflight — 2026-10-29

- CDP target-list read from `http://127.0.0.1:9222/json/list` returned exactly one matching KanColle webview target at `https://play.games.dmm.com/game/kancolle` with a WebSocket endpoint, plus the Poi shell page and a DMM service worker.
- This satisfies the observer's existing `safeGameTarget` URL predicate at preflight time; no target-rule change is indicated by this observation.
- The Nanodesu MCP health endpoint independently returned `status=ok` and `storeReady=true`.
- No observer was armed and no snapshot was created in this preflight. Natural `api_port/port` response capture, response-body validation, and snapshot hashing remain **unverified**.
- Safety: read-only CDP target discovery and MCP health read only; no game input, refresh, or game API request was initiated.


## Follow-up runtime capture validation — 2026-10-29

- Supersedes the earlier status wording above that successful response-body capture remained unverified: a later passive observation did capture a natural, non-cache HTTP 200 `/kcsapi/api_port/port` response after the observer was armed. A compact local snapshot was saved and its JSON syntax and structure were independently checked.
- Snapshot: `Desktop/KancolleBot-Port-Snapshots/poi-port-snapshot-20261029T140534.950Z.json`; 19,694 bytes; snapshot file SHA-256 `70a5c5b3d709061b1f5ca7062e09849aa981f3ba14ced643c5aed4e2d70d61f7`. Captured response-body SHA-256 `11946f7e1e112d05bce29f972d454f2d48484576a750d1e3e3435558f84412f6`.
- Runtime payload check passed: 3 fleet records; 92 ship records in an object keyed by ship instance ID; all keys matched `api_id`, IDs were unique, basic ship HP/ID checks passed, fleet record shapes passed, and all 14 positive fleet ship references resolved within the snapshot.
- This validates a real capture point and compact payload structure only. The local receipt timestamp is not a server timestamp; it does not prove synchronization with later MCP store reads, continuing freshness, or full expedition-loop correctness. No production gate was changed and no game action was performed. The earlier historical preflight sections are retained as a record of what was known at those times.


## In-memory witness handoff and read-only diagnostic — 2026-10-29

- The observer now supports an opt-in child-process output mode. Only when the child environment contains `KCB_POI_FRESHNESS_WITNESS_STDOUT=1` does it emit one `[PortObserver] WITNESS_JSON:` line after a successful capture. Normal Hand sandbox use keeps the original summary-only console output.
- `PassivePortResponseObserver` launches the reviewed script as a local Node child process, captures stdout privately, and parses exactly one witness in memory. It never reads the persistent snapshot file as freshness evidence. Timeout, no natural response, malformed/duplicate witness output, oversized output, missing script, or nonzero child exit returns no witness.
- `PoiExpeditionReader` accepts an optional `port_snapshot_provider` and a default 30-second freshness window. A supplied witness must match the current MCP fleet/ship record sets and captured mutable fields, and both response-receipt and body-capture timestamps must be within the window. Missing provider, stale capture, or any mismatch leaves the freshness flag false.
- Read-only diagnostic command: `python tools/verify_poi_freshness_bridge.py --help`. A full run may wait up to 120 seconds for a naturally occurring response; it never refreshes the page, sends game input, or initiates a game API request. The output omits ship/fleet mission details and always reports `game_actions_enabled: false`. Exit code 2 means the read completed but freshness evidence was not verified.
- Offline validation on Windows: 148/148 Python unit tests passed; observer JavaScript syntax check and diagnostic `--help` passed. A live read-only diagnostic with a deliberately short 2-second observer timeout read 3 fleets from MCP but correctly returned `FRESHNESS_UNVERIFIED` because no witness was received.
- This remains an opt-in integration. The default reader does not start an observer, and the diagnostic does not dispatch expeditions. A recent real response and matching current MCP state have not yet been verified through the new stdout handoff. Do not use a historical disk snapshot to set the freshness flag, and do not refresh or interact with the game to force a capture.
