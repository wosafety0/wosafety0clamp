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

This transport implementation is offline-tested only. It has not yet been verified against a running Poi 12.0.1 instance. State normalization is deliberately separate and must be based on captured real payloads, not guessed field names.

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

The verifier has offline unit tests. A local run against Poi 12.0.1 is still required to establish plugin compatibility and real payload shape.
