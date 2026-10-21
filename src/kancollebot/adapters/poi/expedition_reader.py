"""Read-only adapter for Poi expedition observations.

The adapter only reads live Redux state. It has no dispatch method and cannot
perform gameplay actions.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

from kancollebot.adapters.poi.expedition_state import (
    PoiStateError,
    normalize_poi_fleets,
)
from kancollebot.adapters.poi.mcp_client import PoiMcpClient, PoiMcpError
from kancollebot.core.observed_state import ObservedState
from kancollebot.expeditions.planner import ExpeditionFleetSnapshot


def _tool_value(result: dict[str, Any], tool_name: str) -> Any:
    """Decode a tool's JSON text result; refuse missing or ambiguous content."""
    if result.get("isError") is True:
        raise PoiMcpError(f"{tool_name} returned a tool-level error")
    structured = result.get("structuredContent")
    if structured is not None:
        return structured
    content = result.get("content")
    if not isinstance(content, list):
        raise PoiMcpError(f"{tool_name} returned no content array")
    texts = [
        item.get("text")
        for item in content
        if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)
    ]
    if len(texts) != 1:
        raise PoiMcpError(f"{tool_name} returned an ambiguous text payload")
    try:
        return json.loads(texts[0])
    except json.JSONDecodeError as exc:
        raise PoiMcpError(f"{tool_name} returned non-JSON text") from exc


class PoiExpeditionReader:
    """Read-only source of normalized fleet observations."""

    def __init__(
        self,
        client: PoiMcpClient | None = None,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._client = client or PoiMcpClient()
        self._clock = clock
        self._initialized = False

    def _ensure_ready(self) -> None:
        health = self._client.health()
        if health.get("state") != "listening":
            raise PoiMcpError("Poi bridge health state is not listening")
        if not self._initialized:
            self._client.initialize()
            tools = {tool.name for tool in self._client.list_tools()}
            if not {"poi_describe", "poi_get"}.issubset(tools):
                raise PoiMcpError("Poi bridge is missing required read-only tools")
            self._initialized = True

    def read_fleets(self) -> ObservedState[tuple[ExpeditionFleetSnapshot, ...]]:
        """Read two state branches and normalize them with a local timestamp."""
        self._ensure_ready()
        describe = _tool_value(
            self._client.call_read_tool("poi_describe", {"path": "info.fleets"}),
            "poi_describe",
        )
        if not isinstance(describe, dict):
            raise PoiMcpError("Poi did not describe info.fleets as an object")
        if describe.get("kind") not in {"array", "collection", "object-map"}:
            raise PoiMcpError("Poi info.fleets has an unknown collection kind")

        fleets = _tool_value(
            self._client.call_read_tool("poi_get", {"path": "info.fleets"}),
            "poi_get(info.fleets)",
        )
        ships = _tool_value(
            self._client.call_read_tool("poi_get", {"path": "info.ships"}),
            "poi_get(info.ships)",
        )
        observed_at = self._clock()
        try:
            return normalize_poi_fleets(fleets, ships, observed_at=observed_at)
        except (PoiStateError, ValueError, TypeError, OverflowError) as exc:
            raise PoiMcpError(f"Poi state normalization failed closed: {exc}") from exc
