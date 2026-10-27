"""Offline tests for read-only semantic target discovery."""
from __future__ import annotations

import unittest

from kancollebot.adapters.poi.cdp_input import CdpInputError
from kancollebot.adapters.poi.cdp_semantic_targets import (
    SemanticTargetError,
    locate_accessible_target,
)
from kancollebot.adapters.poi.cdp_websocket import CdpWebSocketTransport


class FakeTransport:
    def __init__(self, *, nodes=None, content=None, page_x=0, page_y=0):
        self.nodes = nodes if nodes is not None else [{
            "role": {"value": "button"},
            "name": {"value": "Expedition"},
            "backendDOMNodeId": 41,
            "ignored": False,
        }]
        self.content = content if content is not None else [100, 50, 200, 50, 200, 90, 100, 90]
        self.page_x = page_x
        self.page_y = page_y
        self.calls = []

    def send(self, method, params):
        self.calls.append((method, params))
        if method == "Accessibility.getFullAXTree":
            return {"nodes": self.nodes}
        if method == "DOM.pushNodesByBackendIdsToFrontend":
            return {"nodeIds": [12]}
        if method == "DOM.getBoxModel":
            return {"model": {"content": self.content}}
        if method == "Page.getLayoutMetrics":
            return {"cssVisualViewport": {
                "clientWidth": 800, "clientHeight": 600,
                "pageX": self.page_x, "pageY": self.page_y,
            }}
        raise AssertionError("Unexpected CDP method: " + method)


class SemanticTargetTests(unittest.TestCase):
    def test_locates_unique_exact_accessible_target(self):
        transport = FakeTransport(page_x=20, page_y=10)
        target = locate_accessible_target(transport, name="Expedition")
        self.assertEqual((target.backend_node_id, target.node_id), (41, 12))
        self.assertEqual((target.x, target.y), (130.0, 60.0))
        self.assertEqual(
            [call[0] for call in transport.calls],
            [
                "Accessibility.getFullAXTree",
                "DOM.pushNodesByBackendIdsToFrontend",
                "DOM.getBoxModel",
                "Page.getLayoutMetrics",
            ],
        )

    def test_ignores_hidden_accessibility_nodes(self):
        transport = FakeTransport(nodes=[
            {"role": {"value": "button"}, "name": {"value": "Expedition"},
             "backendDOMNodeId": 40, "ignored": True},
        ])
        with self.assertRaisesRegex(SemanticTargetError, "found 0"):
            locate_accessible_target(transport, name="Expedition")

    def test_refuses_ambiguous_accessible_names(self):
        node = {"role": {"value": "button"}, "name": {"value": "Expedition"},
                "backendDOMNodeId": 41, "ignored": False}
        transport = FakeTransport(nodes=[node, dict(node, backendDOMNodeId=42)])
        with self.assertRaisesRegex(SemanticTargetError, "found 2"):
            locate_accessible_target(transport, name="Expedition")
        self.assertEqual(len(transport.calls), 1)

    def test_refuses_missing_backend_id(self):
        transport = FakeTransport(nodes=[
            {"role": {"value": "button"}, "name": {"value": "Expedition"},
             "ignored": False},
        ])
        with self.assertRaisesRegex(SemanticTargetError, "backend DOM node ID"):
            locate_accessible_target(transport, name="Expedition")

    def test_refuses_target_outside_viewport(self):
        transport = FakeTransport(content=[900, 50, 950, 50, 950, 90, 900, 90])
        with self.assertRaisesRegex(SemanticTargetError, "outside"):
            locate_accessible_target(transport, name="Expedition")

    def test_refuses_runtime_evaluate_transport_command(self):
        transport = CdpWebSocketTransport.__new__(CdpWebSocketTransport)
        transport._closed = False
        with self.assertRaisesRegex(CdpInputError, "not allowlisted"):
            transport.send("Runtime.evaluate", {"expression": "document.body.click()"})


if __name__ == "__main__":
    unittest.main()
