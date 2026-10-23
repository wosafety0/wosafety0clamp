# POI Page Health Gate — CDP Probe Stage

## Scope

Adds a standard-library-only, loopback-restricted CDP WebSocket probe. It selects exactly one Poi renderer target and evaluates read-only document metadata: title, URL, document readiness, and a bounded body-text sample. It does not click, reload, call game APIs, or read/write Redux state.

## Security and failure behavior

- Accepts only credential-free `ws://` loopback endpoints from validated CDP target discovery.
- Applies socket timeouts and a 2 MiB frame limit.
- Uses only `Runtime.evaluate` with a fixed metadata expression.
- Ambiguous targets, protocol errors, timeouts, invalid messages, and socket closure return no healthy evidence.
- Error-page markers can be passed to the page-health policy; target discovery and document readiness alone do not establish state freshness.

## Test limits

Tests mock the CDP socket and verify the command shape and loopback restriction. They are offline tests, not a live Poi validation. The policy still requires a separately timestamped valid state observation, and a post-failure fresh observation is required after a connection error.

## Safety

This probe is read-only. No real game actions are enabled.
