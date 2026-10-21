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
