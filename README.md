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


## Read-only POI connection diagnostic

The initial integration path is the local HTTP state endpoint, because it can
read fleet and ship state without issuing gameplay commands. The adapter only
uses loopback HTTP GET requests for `/fleets` and `/ships`, rejects redirects,
and never prints raw ship records.

From the repository root, run:

```powershell
$env:PYTHONPATH = "src"
python -m kancollebot.adapters.poi.http_read_only
```

The default endpoint is `http://127.0.0.1:17777`. To override it for a
different local loopback port, set `KANCOLLE_POI_HTTP_URL` before running.
The JSON report shows endpoint/read validation, fleet and ship counts, observed
field names/types, and whether the known `api_name`/`api_nowhp`/`api_maxhp`
mapping is consistent across every live ship record. It does not auto-enable
that mapping or authorize actions. A successful report proves read-only state
retrieval only; collection, dispatch, and battle actions remain disabled.


## POI observations to expedition planner

- `get_observed_states(fields)` reads both endpoints, parses fleet and ship
  state, validates fleet-to-ship references, and returns timestamped observations
  with a common source label. The timestamp is captured before network I/O so
  endpoint latency makes the observations conservatively older.
- `read_and_plan(fields, mission_by_fleet=...)` feeds those observations into
  the existing freshness and expedition safety layers. A completed expedition
  can be recommended for collection. Dispatch remains blocked until supply,
  repair/docking, mission-composition, and fleet-rotation checks are backed by
  actual observations.
- These methods only read and plan. They do not click, collect, dispatch, or
  execute battles.
