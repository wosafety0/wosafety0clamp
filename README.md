# KancolleBot

A locally running KanColle automation framework hosted and tested through GitHub.

## Development principles

- The game client and POI remain on the user's Windows machine.
- GitHub Actions is used for automated checks, not as a persistent game runtime.
- Prefer documented and verified POI data interfaces over screen scraping or simulated clicks.
- Develop against mocked state first; real gameplay actions remain disabled until their adapter and safety checks are verified.
- Keep credentials, cookies, tokens, local game data, and third-party source trees out of this repository.
- Use Python and the standard library where practical. Add dependencies only when justified.

## Current development priorities

1. Complete production dispatch/supervisor composition only after freshness, UI-state gates, durable intent recovery, exact post-action verification, reward settlement, and fleet-wide resupply are integrated.
2. Keep Fleet 1 read-only, Fleet 2/Fleet 3 as the only dispatch candidates, and Fleet 4 deferred until it is unlocked.
3. Continue mocked/offline tests and static CI; missing, stale, ambiguous, or malformed observations must remain fail-closed.
4. Treat any future live operation as a separately authorized, operator-supervised activity. CI and this repository workflow do not authorize unattended game actions.

## Current status — 2026-11-03

The repository is beyond the initial bootstrap stage. The current MVP includes an offline-tested fleet lifecycle state machine, shared-UI arbiter, SQLite fleet-state and dispatch-intent stores/recovery, an injected-port dispatch controller, a read-only continuous monitor, and a single-tick supervisor.

The Poi integration includes a passive Network observer and an in-memory freshness-witness path. One natural successful port response was matched against a contemporaneous Poi MCP read on 2026-11-02; this is a bounded observation, not proof of long-term freshness or atomic consistency across future reads.

The recorded operator-supervised UI evidence covers return-result/bonus/home navigation and fleet-wide supply for the unlocked Fleets 1–3. A final audit recorded 13/13 ships at master fuel/ammunition levels. This historical evidence does not establish unattended scheduler readiness.

Production dispatch/supervisor composition, mission/fleet selection, reward progression, automatic resupply integration into the continuous scheduler, and unattended operation remain pending. The production freshness/dispatch gate remains fail-closed. Current safety candidate PR #27 is based directly on the current MVP branch and adds strict dispatch-intent, freshness, and passive-observer validation; its exact-head offline CI passed 460 Python tests, 17 Node static tests, and compile/syntax checks, with 17 optional vision tests skipped because those dependencies were unavailable.

## Local web console (early scaffold)

The minimal dark console uses only the Python standard library and binds to loopback by default:

```powershell
$env:PYTHONPATH = "src"
python -m kancollebot.web_ui
```

Open `http://127.0.0.1:8765/`. The first slice provides sanitized Poi MCP health diagnostics only. Start/pause/stop controls are intentionally disabled until they are connected to a verified runner lifecycle API; this console does not perform gameplay actions and does not duplicate fleet status, expedition target, or remaining time already displayed by Poi.
