# POI Adapter

The adapter package keeps Poi transport isolated from domain logic.

## Read-only MCP transport

`mcp_client.py` implements a small synchronous MCP Streamable HTTP client using only the Python standard library. Its default endpoint is `http://127.0.0.1:12450/mcp`; non-loopback hosts are rejected unless explicitly overridden. The client allowlists only `poi_describe`, `poi_get`, and `poi_lookup`. It never calls game actions or the plugin's optional `poi_hensei_save` write tool.

Minimal usage:

```python
from kancollebot.adapters.poi.mcp_client import PoiMcpClient

client = PoiMcpClient()
print(client.health())
print(client.initialize())
print([tool.name for tool in client.list_tools()])
print(client.call_read_tool("poi_describe", {}))
print(client.call_read_tool("poi_get", {"path": "info.fleets"}))
```

The user has confirmed that the local Nanodesu endpoint at port 12450 responds to health checks, initializes, lists tools, and describes `info.fleets`; the separate Minagi endpoint at port 17777 also returned a read-only fleet-status result. No gameplay actions were issued. This confirms the installed plugin bridges are reachable, but the repository's Python `PoiMcpClient` and `PoiExpeditionReader` have not yet been run directly against the live local endpoints. Their compatibility remains pending an explicit local Python integration test.

State normalization is deliberately separate and must be based on captured real payloads, not guessed field names.

Public bridge: https://github.com/momocow/poi-plugin-nanodesu-mcp
Official Poi Redux docs: https://dev.poi.moe/docs/api/redux


## Read-only bridge verifier

From the repository root, run the verifier without installing any packages:

```powershell
$env:PYTHONPATH = "src"
python tools/verify_poi_bridge.py --bridge nanodesu
```

For the alternative `minagi/poi-plugin-mcp` bridge (default port 17777; if present, its documented `%USERPROFILE%\\.poi-mcp\\port` file is used for port discovery):

```powershell
python tools/verify_poi_bridge.py --bridge poi-plugin-mcp
```

Both modes restrict HTTP traffic to loopback and query only read-only endpoints/tools. The report prints response shapes, not raw fleet or ship values. A successful transport report deliberately leaves session validity marked `NOT_VERIFIED`; it does not prove the game is logged in or that cached data is fresh. The alternative plugin is not installed automatically, and its optional WebView input/debug-eval features must remain disabled.

The verifier has offline unit tests. A local run against Poi 12.0.1 is still required to establish Python-client compatibility and live payload shape.

## Session evidence prototype

`response_action_evidence.py` extracts a minimal record from two allowlisted Poi Redux response actions and drops all response/request payload fields. In Poi's current source, the response pipeline checks the outer envelope's numeric `api_result == 1` before dispatch, then normally sets `payload.body` to `api_data`; therefore the Redux action body usually has no `api_result`. The prototype reflects that transformed shape. The source call chain is documented in `docs/research/POI_RESPONSE_TIMESTAMP_SOURCE_AUDIT.md`.

This remains a Python contract prototype, not a running Poi plugin. Current public source does not prove compatibility with the installed Poi 12.0.1 binary, and the reader remains fail-closed until the bridge lifecycle, timestamp behavior, and invalidation are validated in the target runtime.
