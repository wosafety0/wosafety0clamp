# Poi State Observation Timestamp Source

## Purpose

`PoiStateObservationTracker` provides the timestamp callback expected by the page-health gate. It does not assign the local read time to cached data.

## Acceptance rules

An event advances the tracker only when all conditions pass:

- `stage` is `response_received` as emitted by the inspected `poi-plugin-api-forwarder` source. The legacy `response` stage is also accepted for compatibility; request/map stages are rejected.
- `path` is one of the explicitly allowlisted state endpoints.
- `response.api_result` is integer `1`.
- `timestamp` is a numeric Unix timestamp in seconds or milliseconds.
- The source timestamp is not more than five seconds in the future and is no older than the configured maximum age (30 seconds by default).
- The timestamp is newer than the stored timestamp; out-of-order events cannot move freshness backwards.

The inspected Forwarder sends `{ path, request: postBody, response: body, stage: "response_received", timestamp: Date.now() }` for `/kcsapi/` events. Its timestamp is Unix milliseconds. Unknown timestamp strings, request/map stages, unrelated paths, failed responses, stale timestamps, and malformed payloads are rejected.

## Integration boundary

The tracker must be fed events returned by `PoiWebhookReceiver.get_event()`. The receiver itself does not authenticate the sender; binding to `127.0.0.1` reduces network exposure but does not protect against other local processes. Treat this timestamp as event freshness evidence, not cryptographic proof of origin.

`PoiHealthGatedHttpReader` composes the tracker with `PoiHttpReadOnlyAdapter` and `read_state_with_page_health`. Its public `get_observed_states`, `get_status`, `diagnose`, and `read_and_plan` methods all use the shared fail-closed gate. The wrapped adapter remains a low-level primitive and must not be exposed directly to application consumers; callers requiring the guarantee should depend on the wrapper only. The caller must record connection failures in UTC and pass `connection_failure_at`; otherwise post-failure recovery cannot be enforced.

## Validation

Unit tests use fixed timestamps and mocked event dictionaries. The event envelope and stage/timestamp format were checked against the public `pmsleepcheck/poi-plugin-api-forwarder` branch `feature/poi-interaction-server` (`index.js`). Unit tests still do not connect to the user's installed Poi process or prove that the installed plugin version/configuration matches that source.