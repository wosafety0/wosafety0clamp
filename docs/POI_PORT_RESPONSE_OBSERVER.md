# Passive Poi Port Response Observer

## Status

- Implementation: committed on branch `dev/poi-safety-consolidation-20261029` and run on the user's local Poi instance as a bounded, passive observer.
- Automated verification: static safety-contract test passed; GitHub Actions run [40858395913](https://github.com/wosafety0/wosafety0clamp/actions/runs/40858395913) passed, including Python compilation and 130 Python unit tests.
- Runtime preflight: verified exactly one matching DMM KanColle webview target and successful CDP `Network.enable`; the observer remained armed for 120 seconds and exited cleanly when no qualifying natural port response arrived. Follow-up read-only `Page.getFrameTree` identified a nested `htmlWrap` frame at `https://w14h.kancolle-server.com/kcs2/index.php`. Its default execution context reported one canvas and one `/kcsapi/api_port/port` resource-timing entry (`startTime≈9313 ms`, `duration≈453 ms`). The timing entry reflects page-load activity before the observer was armed; transfer-size fields were zero, and the timing entry provides no response body or source-freshness proof. The top-level DMM wrapper itself has zero canvas elements. The active game frame is now identified, but successful response-body capture and payload validation remain **unverified**.
- Intended use: observe a naturally occurring successful `/kcsapi/api_port/port` network response through loopback Chrome DevTools Protocol (CDP), then save a compact local snapshot for manual review.
- This is a diagnostic artifact, not production integration and not permission to dispatch expeditions.

## Safety contract

- Reads only `http://127.0.0.1:9222/json/list` and a credential-free loopback CDP WebSocket.
- Uses `Network.enable`, `Network.responseReceived`, `Network.loadingFinished`, and `Network.getResponseBody`.
- Does not call `Runtime.evaluate`, page JavaScript, game functions, or any input method.
- Does not issue a game API request. It waits up to 120 seconds for a natural response already initiated by the game client.
- Rejects non-200 responses, cached/service-worker/prefetch responses, failed API results, duplicate IDs, and malformed fleet/ship records.
- Saves only a compact subset of the response data to `Desktop/KancolleBot-Port-Snapshots`; it does not save the full raw response body. Console output excludes ship/fleet payload values.
- It does not modify the production freshness gate or set `source_freshness_verified`.

## Output and limits

The JSON artifact contains a local response-receipt timestamp, a response-body SHA-256, compact fleet and ship records, and a safety summary. The timestamp is the local observer's receipt time, not a server timestamp.

A successful capture would establish that the current Poi page exposed a fresh, non-cache HTTP 200 port response with the expected fields at that moment. It would **not** by itself establish long-term freshness, atomic consistency with subsequent `poi_get` reads, or full expedition-loop correctness. CDP startup, DMM wrapper discovery, and identification of the nested active game frame were verified on 2026-10-29. Successful response-body capture and payload validation remain unverified until a qualifying natural response is observed after the observer is armed. If no natural port response occurs during the wait window, it exits without producing a snapshot; it must not trigger a refresh or any game action.

## Read-only runtime preflight — 2026-10-29

- CDP target-list read from `http://127.0.0.1:9222/json/list` returned exactly one matching KanColle webview target at `https://play.games.dmm.com/game/kancolle` with a WebSocket endpoint, plus the Poi shell page and a DMM service worker.
- This satisfies the observer's existing `safeGameTarget` URL predicate at preflight time; no target-rule change is indicated by this observation.
- The Nanodesu MCP health endpoint independently returned `status=ok` and `storeReady=true`.
- No observer was armed and no snapshot was created in this preflight. Natural `api_port/port` response capture, response-body validation, and snapshot hashing remain **unverified**.
- Safety: read-only CDP target discovery and MCP health read only; no game input, refresh, or game API request was initiated.
