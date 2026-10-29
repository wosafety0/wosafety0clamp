# Personal-Windows-MCP Incident — 2026-10-29

## Summary

During read-only inspection of the recovered Poi Hand plugin, Personal-Windows-MCP returned an internal error on a file read. A later read attempt returned a specific MCP session initialization conflict. DC was used only for necessary read-only cross-validation. No game input, Poi refresh, process restart, tunnel configuration change, or Windows setting change occurred.

## Incident record

- **Failure time (timezone):** approximately `2026-10-29T23:17:40+08:00`; the long read failed after about 247 seconds, around 23:21:47+08:00. This is the observed call window, not a server-side timestamp.
- **Tool:** Personal-Windows-MCP `read_file`; initial parallel call also failed at the wrapper level.
- **Relative file path:** `Project_KancolleBot/dev/poi-plugin-hand/index.js` under `D:\AI_Project`.
- **offset:** `0`.
- **max_chars:** `18000`.
- **Elapsed time:** 247189 ms for the long `index.js` read; 9370 ms for a `package.json` read that returned an empty object; 14219 ms for a later `list_directory` call that succeeded.
- **Actual returned error:** `The tool failed internally.`; initial outer wrapper: `Script error: The tool failed internally.`.
- **content:** no usable file content from the failed read; the package read returned `{}` with no file body.
- **isError:** original MCP field not exposed; the wrapper raised an exception.
- **structuredContent:** not available.
- **Exception/timeout:** long read remained pending for about 247 seconds and then failed internally.
- **list_directory cross-check:** Personal-Windows-MCP successfully listed `Project_KancolleBot` and showed both `dev/poi-plugin-hand/index.js` and `package.json`; result was not truncated.
- **Same-file re-read:** DC successfully read the 42-line `index.js` and 13-line `package.json`. This is cross-validation only and does not erase the primary-tool failure.
- **Tunnel client:** `tunnel-client.exe`, PID 29960, process start `2026-10-29T22:25:39.119+08:00`; process existed during the check. Tunnel stdout/stderr and request ID were not obtained.
- **Python service:** PID 28256 runs `D:/AI_Project/Personal-Windows-MCP/personal_windows_mcp.py`, start `2026-10-29T22:25:39.236+08:00`, reported `Responding=True`. Process existence does not prove MCP session health.
- **Later repeat probe:** at approximately `2026-10-29T23:23:24+08:00`, reading `Project_KancolleBot/Cloud/KancolleBot-dev-poi-page-health-gate-20261023/README.md` with offset 0 and max_chars 1000 took 8289 ms and returned exactly: `CONFLICT: Error code: CONFLICT; Error: McpServerError: MCP server is not initialized; send initialize and notifications/initialized before operational requests`.
- **Source inspection:** `personal_windows_mcp.py` uses JSON-RPC over stdio and implements `initialize`, `notifications/initialized`, `tools/list`, and `tools/call`. Current evidence proves an MCP initialization-state error was returned, but does not locate whether the failure is in the remote session, tunnel bridge, or client lifecycle.
- **Other observations:** Poi remained running; CDP port 9222 and Poi API port 12450 were listening. No Hand runtime `[Hand]` log was confirmed, so disk installation and syntax validation must not be described as proof of runtime loading.
- **Secret handling:** no API key, token, credential value, or other secret is included.

## Follow-up

1. Keep Personal-Windows-MCP as the preferred read tool; use DC only when it fails or cannot perform the read.
2. Before retrying large reads, confirm MCP session initialization is restored and capture the original response fields if the connector exposes them.
3. Obtain tunnel stdout/stderr or request ID if the failure recurs; none was available in this incident.
4. Avoid restarting the tunnel while its runtime credential cannot be safely re-entered and the current session may depend on it.
5. Continue read-only verification of Hand loading without refreshing the Poi page or triggering game actions.
