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


## Local web console (early scaffold)

The minimal dark console uses only the Python standard library and binds to loopback by default:

```powershell
$env:PYTHONPATH = "src"
python -m kancollebot.web_ui
```

Open `http://127.0.0.1:8765/`. The first slice provides sanitized Poi MCP health diagnostics only. Start/pause/stop controls are intentionally disabled until they are connected to a verified runner lifecycle API; this console does not perform gameplay actions and does not duplicate fleet status, expedition target, or remaining time already displayed by Poi.
