# Poi State Observation Timestamp Source

## Purpose

`PoiStateObservationTracker` provides the timestamp callback expected by the page-health gate. It does not assign the local read time to cached data.

## Acceptance rules

An event advances the tracker only when all conditions pass:

- `stage` is exactly `response`.
- `path` is one of the explicitly allowlisted state endpoints.
- `response.api_result` is integer `1`.
- `timestamp` is a numeric Unix timestamp in seconds or milliseconds.
- The source timestamp is not more than five seconds in the future and is no older than the configured maximum age (30 seconds by default).
- The timestamp is newer than the stored timestamp; out-of-order events cannot move freshness backwards.

Unknown timestamp strings, request-stage events, unrelated paths, failed responses, stale timestamps, and malformed payloads are rejected.

## Integration boundary

The tracker must be fed events returned by `PoiWebhookReceiver.get_event()`. The receiver itself does not authenticate the sender; binding to `127.0.0.1` reduces network exposure but does not protect against other local processes. Treat this timestamp as event freshness evidence, not cryptographic proof of origin.

The tracker is not yet wired into the HTTP adapter automatically. A future orchestration entry point must probe the Poi page first, perform a read-only state read, then use `latest_valid_state_at` in `read_state_with_page_health`. The caller must record connection failures in UTC and pass `connection_failure_at`; otherwise post-failure recovery cannot be enforced.

## Validation

Unit tests use fixed timestamps and mocked event dictionaries. They do not connect to Poi, perform game actions, or prove the runtime Forwarder configuration.