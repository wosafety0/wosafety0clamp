# Passive Poi Port Response Observer

## Status

- Implementation: written on branch `dev/poi-safety-consolidation-20261029`; not yet run in the user's local Poi instance.
- Automated verification: static safety-contract test and CI are pending.
- Runtime verification: **not performed**.
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

A successful capture would establish that the current Poi page exposed a fresh, non-cache HTTP 200 port response with the expected fields at that moment. It would **not** by itself establish long-term freshness, atomic consistency with subsequent `poi_get` reads, or full expedition-loop correctness. Runtime compatibility, target discovery, and payload validation remain unverified until this script is run locally. If no natural port response occurs during the wait window, it exits without producing a snapshot; it must not trigger a refresh or any game action.
