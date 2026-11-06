# CDP Target Discovery

`src/kancollebot/adapters/poi/cdp_targets.py` discovers Chrome DevTools page targets from a loopback endpoint. It does not open the target WebSocket or send input.

## Local read-only check

Start Poi with its existing DevTools endpoint enabled, then from the repository root run:

```powershell
$env:PYTHONPATH = "src"
python -m kancollebot.adapters.poi.cdp_targets --endpoint http://127.0.0.1:9222 --title-hint poi
```

If the title is not unique, the command fails closed. Inspect the reported page titles and URLs and rerun with a more specific `--title-hint` or `--url-hint`. Do not use guessed click coordinates.

A `PASS_DISCOVERY_ONLY` result proves only that one matching page target was discovered. It does not prove the game is logged in, the state is fresh, or dispatch is possible. The output includes a local WebSocket endpoint; treat it as a local control endpoint and do not share it publicly. No game action is performed.
