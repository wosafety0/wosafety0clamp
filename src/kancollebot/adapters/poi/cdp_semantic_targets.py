"""Read-only semantic target discovery through the CDP accessibility tree."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Protocol


class SemanticTargetError(RuntimeError):
    """An accessible target cannot be resolved safely."""


class CdpReadTransport(Protocol):
    def send(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send an allowlisted CDP command."""
        ...


@dataclass(frozen=True, slots=True)
class SemanticTarget:
    """A unique accessible target projected into the current viewport."""

    name: str
    role: str
    backend_node_id: int
    node_id: int
    x: float
    y: float


def _positive_id(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SemanticTargetError(f"{label} is missing or invalid")
    return value


def _viewport(transport: CdpReadTransport) -> tuple[float, float, float, float]:
    metrics = transport.send("Page.getLayoutMetrics", {})
    viewport = metrics.get("cssVisualViewport") or metrics.get("visualViewport")
    if not isinstance(viewport, dict):
        raise SemanticTargetError("CDP did not provide visual viewport metrics")
    width, height = viewport.get("clientWidth"), viewport.get("clientHeight")
    page_x, page_y = viewport.get("pageX", 0), viewport.get("pageY", 0)
    values = (width, height, page_x, page_y)
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        or not math.isfinite(value)
        for value in values
    ):
        raise SemanticTargetError("CDP viewport metrics contain invalid numbers")
    if width <= 0 or height <= 0 or page_x < 0 or page_y < 0:
        raise SemanticTargetError("CDP viewport metrics are out of range")
    return float(width), float(height), float(page_x), float(page_y)


def locate_accessible_target(
    transport: CdpReadTransport,
    *,
    name: str,
    role: str = "button",
) -> SemanticTarget:
    """Locate exactly one accessible node by exact name and role, without input."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-empty string")
    if not isinstance(role, str) or not role.strip():
        raise ValueError("role must be a non-empty string")

    result = transport.send("Accessibility.getFullAXTree", {})
    nodes = result.get("nodes") if isinstance(result, dict) else None
    if not isinstance(nodes, list):
        raise SemanticTargetError("CDP accessibility tree is missing its nodes array")

    matches = []
    for node in nodes:
        if not isinstance(node, dict) or node.get("ignored") is True:
            continue
        node_role = node.get("role")
        node_name = node.get("name")
        actual_role = node_role.get("value") if isinstance(node_role, dict) else None
        actual_name = node_name.get("value") if isinstance(node_name, dict) else None
        if actual_role == role and actual_name == name:
            matches.append(node)
    if len(matches) != 1:
        raise SemanticTargetError(
            f"Expected one accessible {role} named {name!r}; found {len(matches)}"
        )

    backend_id = _positive_id(matches[0].get("backendDOMNodeId"), "backend DOM node ID")
    pushed = transport.send(
        "DOM.pushNodesByBackendIdsToFrontend", {"backendNodeIds": [backend_id]}
    )
    node_ids = pushed.get("nodeIds") if isinstance(pushed, dict) else None
    if not isinstance(node_ids, list) or len(node_ids) != 1:
        raise SemanticTargetError("CDP did not resolve the accessible target to one DOM node")
    node_id = _positive_id(node_ids[0], "frontend DOM node ID")

    box_result = transport.send("DOM.getBoxModel", {"nodeId": node_id})
    model = box_result.get("model") if isinstance(box_result, dict) else None
    content = model.get("content") if isinstance(model, dict) else None
    if not isinstance(content, list) or len(content) != 8:
        raise SemanticTargetError("CDP target has no valid content box")
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        or not math.isfinite(value)
        for value in content
    ):
        raise SemanticTargetError("CDP target box contains invalid coordinates")

    width, height, page_x, page_y = _viewport(transport)
    x = sum(float(content[index]) for index in (0, 2, 4, 6)) / 4 - page_x
    y = sum(float(content[index]) for index in (1, 3, 5, 7)) / 4 - page_y
    if not (0 <= x < width and 0 <= y < height):
        raise SemanticTargetError("Accessible target center is outside the current viewport")

    return SemanticTarget(name, role, backend_id, node_id, x, y)
