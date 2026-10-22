# KancolleBot Reference Synthesis — 2026-10-23

## Purpose

Consolidate the currently reviewed external-project clues into implementation decisions for KancolleBot. This is a source-review synthesis, not evidence of a live local Poi connection. Follow `docs/PROJECT_kancollebot_MASTER.md`: Python is the implementation language; keep evidence, inference, and unknowns separate; do not copy third-party source trees; do not treat CI as live-game validation.

## Executive decision

Build one passive-data path first:

`Poi Forwarder webhook -> existing bounded receiver -> event normalizer -> validated event ledger -> state reducer -> freshness/completeness gate -> read-only planner`

Do not add a second transport until the Forwarder contract is verified. Keep live game mutations disabled while the read path and recovery behavior remain unvalidated.

## Evidence matrix

| Reference | Verified useful evidence | Apply to KancolleBot | Do not infer |
| --- | --- | --- | --- |
| Poi Forwarder + existing receiver | Current receiver accepts POST `/webhook/kancolle`, binds only to `127.0.0.1`, limits payload/queue, filters KCSAPI/map resources, redacts sensitive key names and URL query/fragment | Preserve it as the first ingestion adapter; add normalization and state reduction after the queue | The user's installed plugin is configured, payload contract is fully verified, or local Poi is connected |
| Kuma | Game request/response/error observations flow through internal Electron IPC; response path normalizes `svdata=` and only broadcasts HTTP 200 JSON responses | Use as a conceptual example of passive observation, response validation, and event-ledger design | Kuma's internal events are a public webhook/API; Kuma injection is needed for the Poi-hosted path |
| KCV / 非提助手 | Primary behavioral reference selected by project rules | Study task loop, fleet readiness, expedition/sortie decision, stop/error recovery when those implementation questions arise | Its transport/protocol is implemented by Poi or is already available locally |
| KCA / kcauto_custom | Supplementary implementation reference; its docs mention Forwarder 1.1.0+ and webhook receiver port 9223 | Cross-check actual Forwarder behavior and response correlation only where current implementation lacks evidence | Its optional control service is required for passive read-only ingestion; its control port should be enabled |
| KanCollePlayerSimulator | Expedition resource utility scheduling; anti-thrashing when resource levels are close; ship/fleet filters; POI battle-log clipboard parser can produce offline scenarios | Independently implement expedition scoring, hysteresis, composition constraints, and offline fixtures | Its estimates/TODOs are authoritative game rules; clipboard battle logs are a general live API |
| KancolleSniffer | Potentially useful for decoding specific KCSAPI response semantics | Consult targeted parsers only when a field/schema question blocks the reducer | Need its proxy/certificate setup or to recreate its whole app for the first integration milestone |

## Existing KancolleBot baseline confirmed by source

- `src/kancollebot/adapters/poi/webhook_receiver.py` is a bounded read-only receiver.
- `tests/test_poi_webhook_receiver.py` currently covers accepted KCSAPI events, secret redaction, map resource path normalization, unknown-resource filtering, malformed/non-object JSON, wrong route, oversized body, queue saturation, and loopback binding.
- The receiver currently carries `path`, `request`, `response`, `stage`, and `timestamp` into a sanitized event. It does not yet establish whether an event is a successful KCSAPI response or whether a set of events forms a coherent current state.
- The existing fleet/ship parsers are snapshot-oriented, not a general event-to-state reducer.
- The MCP client must be treated as a separate optional path; prior research found the referenced server is stateless Streamable HTTP and has a specific tool list. No successful local session or Poi connection has been established.

## Implementation plan (ordered)

### P0 — Verify the event contract

1. Inspect the actual `poi-plugin-forwarder` source corresponding to the supported installed version, especially event construction and webhook POST serialization.
2. Record required fields and actual types for `path`, `stage`, `timestamp`, `request`, and `response`; determine whether the event is request, response, or error and how failures are represented.
3. Add only redacted synthetic fixtures until an actual local sample is available. Never commit cookies, tokens, account/session data, or full unnecessary request bodies.

### P1 — Pure normalization

- Accept only documented event stages and valid path types.
- Normalize KCSAPI path/query separately; never use query values as identifiers or log them.
- Parse timestamp conservatively and label missing/unparseable timestamps as unknown rather than fresh.
- Recognize successful API responses only when the response shape and `api_result` semantics are supported by evidence.
- Preserve failure/missing-response observations as diagnostics, not successful state updates.
- No network I/O, Poi commands, or game actions in this layer.

### P2 — State reducer and trust gate

- Update only fields explicitly mapped from recognized response schemas.
- Store observation time and source event for each state domain; do not treat one event as a whole-port snapshot.
- Track missing required domains, stale observations, response failures, and conflicting observations.
- Mark the resulting state as non-actionable if required data is missing, stale, mixed-session, or contradictory.
- Keep the first milestone read-only; planners can emit proposed decisions but must not dispatch them.

### P3 — Offline tests

Cover at least: valid response; request-only event; explicit API failure; missing response; out-of-order events; duplicate event; malformed JSON/body shape; missing or invalid timestamp; stale data; conflicting updates; unknown endpoint; secret redaction. Test that incomplete/stale states fail closed.

### P4 — Separate planning logic from transport

- Expedition scoring: expected resource gain per unit time, target deficits, resource caps, and hysteresis to avoid oscillation.
- Fleet composition: explicit level/lock/equipment/upgrade constraints, with unknown compatibility treated as unknown rather than silently accepted.
- Use independently written logic and tests; treat simulator estimates as tunable assumptions, not canonical values.
- Use POI battle-log-derived scenarios only as offline fixtures after their clipboard format is documented and sanitized.

### P5 — Local read-only validation when PC is available

Confirm installed Forwarder version/configuration, receiver connectivity, redacted event samples, and end-to-end state freshness. Keep ports loopback-only. Do not claim success before the actual local test output exists.

## Explicitly out of scope for the current pass

- Kuma-specific preload injection or a dependency on Kuma's private Electron event bus.
- Enabling KCA's control port as a prerequisite for passive observation.
- Broadly reproducing KancolleSniffer, the simulator UI, or any reference project's entire codebase.
- Live expedition dispatch, fleet changes, sortie start, reward claims, or any other state-mutating game command before the project safety gates are met.
- Claiming local Poi integration from source review or GitHub Actions results.

## Next concrete coding task

Verify the Forwarder event contract, then add a pure `normalize_forwarder_event` function and a small typed normalized-event model, with offline fixtures and fail-closed tests. Do not implement guessed field aliases or a broad reducer until the Forwarder source confirms the payload semantics.
