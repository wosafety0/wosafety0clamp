# KancolleBot

A locally running KanColle automation framework hosted and tested through GitHub.

## Development principles

- The game client and POI remain on the user's Windows machine.
- GitHub Actions is used for automated checks, not as a persistent game runtime.
- Prefer documented and verified POI data interfaces over screen scraping or simulated clicks.
- Develop against mocked state first; real gameplay actions remain disabled until their adapter and safety checks are verified.
- Keep credentials, cookies, tokens, local game data, and third-party source trees out of this repository.
- Use Python and the standard library where practical. Add dependencies only when justified.

## Initial priorities

1. Establish the project structure and automated tests.
2. Research POI integration points and KCA expedition/battle behavior using source evidence.
3. Implement a read-only POI adapter and mocked state models.
4. Build the expedition module with explicit preconditions, cancellation, and audit logs.
5. Expand to battle automation only after the expedition and safety layers are validated.

## Status

The repository bootstrap is in progress. No real gameplay automation is enabled by the initial scaffold.


## Read-only POI health-gated preflight

The supported runtime composes the loopback Poi Forwarder receiver, CDP renderer
probe, read-only HTTP adapter, source-event freshness tracker, and
`PoiHealthGatedHttpReader`. The legacy command
`python -m kancollebot.adapters.poi.http_read_only` now delegates to this
same health-gated runtime rather than bypassing it.

From the repository root, start the preflight:

```powershell
$env:PYTHONPATH = "src"
python -m kancollebot.adapters.poi.runtime
```

Defaults:
- HTTP state endpoint: `http://127.0.0.1:17777`
- Chrome DevTools endpoint: `http://127.0.0.1:9222`
- Forwarder receiver: `http://127.0.0.1:9223/webhook/kancolle`
- Wait for a valid Forwarder state event: 60 seconds

Optional environment overrides: `KANCOLLE_POI_HTTP_URL`,
`KANCOLLE_POI_CDP_ENDPOINT`, `KANCOLLE_POI_TITLE_HINT`,
`KANCOLLE_POI_URL_HINT`, `KANCOLLE_POI_WEBHOOK_PORT`, and
`KANCOLLE_POI_EVENT_TIMEOUT_SECONDS`. The receiver binds only to IPv4
loopback. Start this command, configure Poi Forwarder manually to POST its
response events to the printed webhook URL, and then let a fresh successful
KCSAPI response arrive. This command does not edit Poi or Forwarder settings.

The runtime performs no HTTP state reads until it receives a recent successful
state-relevant event; it then probes exactly one matching Poi page before
reading `/fleets` and `/ships`. Any missing, stale, ambiguous, or unhealthy
evidence fails closed. Its output is a privacy-preserving schema diagnostic.
The HTTP adapter remains a low-level primitive; application callers should use
`PoiHealthGatedHttpReader` and must not expose the adapter directly.

## Preflight report interpretation

The JSON report is a **single-run diagnostic**, not permission to automate gameplay.
`status=PASS_READ_ONLY_HEALTH_GATED` means only that all read-only gates
passed during this invocation. A nonzero exit code or `status=FAILED` must
never be treated as a usable state snapshot.

- `gates.forwarder_event_accepted` becomes true once a validated Forwarder
  event is received; it remains true if a later CDP or HTTP check fails.
- `gates.source_event_fresh` means the validated source timestamp still meets
  the 15-second page-health freshness window at report time. The tracker can
  initially accept an event up to 30 seconds old; acceptance alone does not
  authorize an HTTP read.
- `gates.cdp_target_identity_validated` and `gates.cdp_document_ready`
  indicate the renderer identity and document-readiness checks.
- `gates.fleets_endpoint_read`, `gates.ships_endpoint_read`, and
  `gates.fleet_ship_references_valid` summarize the schema-only HTTP
  diagnostic, without returning raw fleet or ship records.
- `gates.gameplay_actions_disabled` is always true by design; it is **not**
  evidence that expedition or battle execution has been tested.
- `http_diagnostic_completed` is true only when the diagnostic returns and
  its post-read gate report is evaluated. Failure paths can have some earlier
  gates true while this flag remains false.

The receiver is loopback-only but does not authenticate the local sender.
These gates establish bounded read-only health evidence, not cryptographic
provenance or authorization to issue game commands.

## Real-environment acceptance checklist

Before treating a real Poi connection as validated, manually confirm all of the
following and record the versions/configuration used:

1. Poi is running and the loopback CDP endpoint is enabled intentionally.
2. CDP discovery selects exactly one intended Poi renderer; no ambiguous page
   match is accepted.
3. Poi Forwarder is installed/enabled and posts the actual `response_received`
   envelope to the printed loopback webhook URL.
4. The receiver accepts a real successful `/kcsapi/` response and the tracker
   advances from the source timestamp; failed, request-stage, stale, future,
   and unrelated-resource events are rejected.
5. The CDP probe reports the intended page with `document.readyState=complete`
   and no browser connection-error markers.
6. The real HTTP endpoint responds to loopback GET requests for `/fleets` and
   `/ships`; redirects, schema errors, and broken fleet-to-ship references
   are rejected.
7. A deliberately missing/stale webhook event and an unavailable/ambiguous CDP
   target both block the diagnostic before state is returned.
8. The report contains no ship names, IDs, cookies, tokens, or raw state records.
9. No game action is enabled. Collection, dispatch, and battle execution remain
   disabled until their own observed-state preconditions and action adapters are
   separately reviewed and tested.

Current CI uses mocked CDP metadata and local fixture HTTP/Forwarder traffic.
It validates wiring and fail-closed behavior, **not** the user's live Poi
installation. The runtime does not configure Forwarder automatically and does
not authenticate local webhook senders; a loopback-only endpoint is not proof
of sender identity.

## POI observations to expedition planner

- `get_observed_states(fields)` reads both endpoints, parses fleet and ship
  state, validates fleet-to-ship references, and returns timestamped observations
  with a common source label.
- `read_and_plan(fields, mission_by_fleet=...)` feeds observations through the
  health-gated facade and existing expedition safety layers. It only plans;
  no game action is sent.
