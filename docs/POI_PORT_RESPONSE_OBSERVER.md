# Passive Poi Port Response Observer

## Current status

- Implementation: committed on branch `dev/poi-safety-consolidation-20261029`; the observer has been run on the user's local Poi instance as a bounded, passive observer.
- **Natural response capture:** a successful, non-cache HTTP 200 `/kcsapi/api_port/port` response was captured on 2026-10-29 and its compact payload structure was checked. This verifies the capture path and payload shape for that historical sample only.
- **Current in-memory witness gate:** the stdout handoff and reader integration have offline tests, but a successful natural-response capture through the current witness handoff followed by a matching current MCP read has not been verified end to end. The historical snapshot must not be reused as freshness evidence.
- **Automated verification recorded by the project:** the diagnostic bridge was previously validated with 148/148 Python unit tests, a JavaScript syntax check, and diagnostic `--help`. Those are prior results, not tests rerun during the latest static reviews.
- Intended use: passively observe a naturally occurring successful `/kcsapi/api_port/port` response through loopback Chrome DevTools Protocol (CDP), then compare an in-memory compact witness against current Poi MCP state.
- This is a diagnostic artifact, not a production integration and not permission to dispatch expeditions. Live dispatch remains disabled.

## Safety contract

- Reads only `http://127.0.0.1:9222/json/list` and a credential-free loopback CDP WebSocket.
- Uses `Network.enable`, `Network.responseReceived`, `Network.loadingFinished`, and `Network.getResponseBody`.
- Does not call `Runtime.evaluate`, page JavaScript, game functions, or any input method.
- Does not issue a game API request. It waits up to 120 seconds for a natural response already initiated by the game client.
- Rejects non-200 responses, cached/service-worker/prefetch responses, failed API results, duplicate IDs, and malformed fleet/ship records.
- In normal Hand mode, saves only a compact subset of the response data to `Desktop/KancolleBot-Port-Snapshots`; it never saves the full raw response body. In child-only witness mode (`KCB_POI_FRESHNESS_WITNESS_STDOUT=1`), it emits the compact witness in memory and does not create the output directory or write a snapshot file. Console output excludes ship/fleet payload values.
- The observer alone does not modify the production freshness gate. The optional in-memory provider can feed a witness to `PoiExpeditionReader`, which sets `source_freshness_verified` only after bounded-age and exact mutable-field matching; the default reader without a provider remains unverified.
- The child-only output mode reports `persisted: false` and skips `mkdirSync` / `writeFileSync`; normal Hand mode retains the original compact-file and hash-summary behavior. This is an output-mode guarantee only, not source authentication or live freshness proof.

## Output and limits

The JSON artifact contains a local response-receipt timestamp, a response-body SHA-256, compact fleet and ship records, and a safety summary. The timestamp is the local observer's receipt time, not a server timestamp.

A successful capture would establish that the current Poi page exposed a fresh, non-cache HTTP 200 port response with the expected fields at that moment. It would **not** by itself establish long-term freshness, atomic consistency with subsequent `poi_get` reads, or full expedition-loop correctness. CDP startup, DMM wrapper discovery, and identification of the nested active game frame were verified on 2026-10-29. Successful response-body capture and payload validation remain unverified until a qualifying natural response is observed after the observer is armed. If no natural port response occurs during the wait window, it exits without producing a snapshot; it must not trigger a refresh or any game action.

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
