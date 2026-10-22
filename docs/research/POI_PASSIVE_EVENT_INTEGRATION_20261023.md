# POI Passive Event Integration Research — 2026-10-23

## Decision

Keep the existing Poi Forwarder webhook receiver as the first integration path. Do not implement a Kuma-specific Electron preload patch and do not assume Kuma exposes its internal event emitter as a public network API.

## Verified KancolleBot baseline

- `src/kancollebot/adapters/poi/webhook_receiver.py` accepts POST events at `/webhook/kancolle`, binds only to IPv4 loopback, limits body size and queue size, filters KCSAPI/map resources, redacts credential-like keys, and queues sanitized events.
- `tests/test_poi_webhook_receiver.py` covers accepted events, redaction, route/body validation, queue saturation, and loopback binding.
- Existing fleet and ship parsers consume snapshot-like POI payloads. They are not yet a general event-to-state reducer.
- `docs/PROJECT_kancollebot_MASTER.md` says real gameplay actions are disabled by default and CI success is not evidence of live POI validation.

## Kuma evidence

Files reviewed in `zhangkanbbc-code/kuma` main:

- `assets/preload/webview-preload.js` exposes a narrow `kumaPreloadBridge` and forwards game request/response/error observations through Electron IPC channel `kuma:game-api`.
- `src/main/game-api-broadcaster.ts` validates the sender as a game webview, checks game-like paths and allowed HTTP methods, and emits internal `network.on.request`, `network.on.response`, and `network.error` events.
- The broadcaster normalizes `svdata=` response prefixes and only emits response events when the response body parses as JSON and the HTTP status is 200.
- These are internal Kuma/Electron events, not a demonstrated public HTTP webhook contract that KancolleBot can connect to directly.
- The reviewed source is adapted from Poi source under its stated MIT attribution. Reuse must preserve license notices and must not copy unrelated application code.

## KancolleSniffer research boundary

Treat KancolleSniffer as a targeted reference for API decoding and state interpretation, not as a replacement host or a second full application to reproduce. Inspect only the specific parsers and data models that address gaps discovered while validating the passive event pipeline. Its proxy/certificate setup is not required for the first Poi-hosted path.

## Integration plan

1. Confirm the actual installed Poi Forwarder payload contract against its source and a redacted local sample; do not infer field names from Kuma's internal IPC payload.
2. Add a pure event-normalization layer between the webhook queue and domain state. It should validate event stage, path, timestamp, response shape, and KCSAPI success marker without performing network I/O or game actions.
3. Build a state reducer that merges only recognized API responses, records per-field/per-resource observation times, and marks state incomplete when required events are missing.
4. Keep stale, incomplete, mixed-session, or contradictory observations non-dispatchable. A single event must never be treated as a complete current fleet snapshot.
5. Add offline fixtures and tests for ordering, missing responses, API failures, stale events, malformed payloads, and secret redaction.
6. Only after those checks pass, run a local read-only Poi integration diagnostic when the user's PC is available. Do not expose local ports to the network.
7. Research KancolleSniffer only for unresolved schema/state questions; stop when it adds no evidence needed by the current implementation.

## Non-goals

- No live game commands or operation adapter in this phase.
- No public binding of the webhook listener.
- No Kuma-specific runtime injection or direct dependency on Kuma's private internal event emitter.
- No claim that CI or source review proves the user's local Poi is connected.
