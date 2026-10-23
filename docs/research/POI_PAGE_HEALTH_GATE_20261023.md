# POI Page Health Gate — CDP Probe and Read Orchestration

## Scope

Adds a standard-library-only, loopback-restricted CDP WebSocket probe. It selects exactly one Poi renderer target and evaluates read-only document metadata: title, URL, document readiness, and a bounded body-text sample. It does not click, reload, call game APIs, or read/write Redux state.

The `health_gated_reader.py` orchestration helper enforces this order:
1. Probe the page before requesting state.
2. Block immediately when the target is missing, the probe is stale, the document is incomplete, or a browser connection-error marker is present.
3. Read state only after the page-surface check passes.
4. Require an independently supplied timestamp for the latest validated game-state observation; reject stale state or state not observed after a recorded connection failure.
5. Return the state only after the final health decision is explicitly HEALTHY.

The timestamp callback must report a real validated state observation (for example, a state-eligible post-response event), not a local timestamp assigned merely because a cached payload was fetched.

## Security and failure behavior

- Accepts only credential-free `ws://` loopback endpoints from validated CDP target discovery.
- Applies socket timeouts and a 2 MiB message limit.
- Uses only `Runtime.evaluate` with a fixed metadata expression.
- Ambiguous targets, protocol errors, timeouts, invalid messages, and socket closure provide no healthy evidence.
- Page discovery and `document.readyState` alone do not establish game-state freshness.
- The Poi webhook receiver removes URL userinfo, query strings, and fragments from HTTP(S) URLs found in KCSAPI event strings and map-resource response URLs, so URL-embedded credentials are not retained in queued events.
- The supported facade is `PoiHealthGatedHttpReader`. Its `get_observed_states`, `get_status`, `diagnose`, and `read_and_plan` methods all route through the same page and source-event gate. The wrapped `PoiHttpReadOnlyAdapter` remains a low-level primitive: do not expose it to application consumers or call its methods directly when health gating is required.

## Validation

GitHub Actions validation is tracked on the pull request. The URL-userinfo sanitization change and its KCSAPI/nested-URL/map-resource regression tests passed the CI and Python Tests workflows for commit `827a012c2c12e4872f339e84c95b40b2c775b22f`:
- [CI](https://github.com/wosafety0/KancolleBot/actions/runs/39963311856)
- [Python Tests](https://github.com/wosafety0/KancolleBot/actions/runs/39963311923)

Unit tests are offline/mocked and do not constitute a live Poi validation.

## Safety

This code is read-only. No real game actions are enabled.
