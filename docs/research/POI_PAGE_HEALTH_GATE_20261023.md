# POI Page Health Gate — Initial Offline Test Stage

## Scope

This stage adds a loopback-only Chromium DevTools target-discovery helper and a pure, fail-closed page-health policy. It does not connect to the DevTools WebSocket, evaluate the live document, read live Redux state, or perform gameplay actions.

## Health policy

A page is considered healthy only when all of these are explicitly supplied and valid:

- A unique, verified Poi renderer target (target discovery must reject ambiguous matches).
- A fresh probe timestamp.
- Document readiness equal to `complete`.
- No recognized Chromium connection-error marker in the title, URL, or supplied error text.
- A fresh, timezone-aware valid game-state observation.
- If a connection failure was recorded, the state observation timestamp must be strictly later than that failure.

Missing evidence is `UNKNOWN`, not healthy. A detected connection failure or missing target is `UNHEALTHY`. Both block the gate.

## Limits

The current page-health module is policy-only. It cannot detect a real `ERR_CONNECTION_CLOSED` until a CDP/runtime probe supplies evidence. Target discovery alone is not page-health verification. The next stage must implement the CDP probe with strict loopback validation and offline tests for protocol errors/timeouts before it can be used as a live health check.

## Test coverage

Offline unit tests cover connection-error markers, missing probes/targets, loading documents, stale/future observations, recovery requiring a post-failure state timestamp, and fail-closed gating. They do not constitute validation against a live Poi instance.

## Safety

No game commands, clicks, page reloads, or live requests are issued by these modules. Real gameplay remains disabled.
