from __future__ import annotations

import unittest
from unittest.mock import patch

from kancollebot.adapters.poi.cdp_input import CdpInputError
from kancollebot.adapters.poi.cdp_targets import CdpTarget
from kancollebot.adapters.poi.cdp_websocket import (
    CdpPoiSession,
    CdpWebSocketTransport,
    connect_poi_session,
)


class CdpWebSocketTransportTests(unittest.TestCase):
    def test_rejects_non_loopback_before_opening_socket(self):
        with patch("kancollebot.adapters.poi.cdp_websocket.socket.create_connection") as connect:
            with self.assertRaisesRegex(CdpInputError, "loopback"):
                CdpWebSocketTransport("ws://192.0.2.1:9222/devtools/page/1")
            connect.assert_not_called()

    def test_rejects_runtime_evaluate_without_socket_activity(self):
        transport = CdpWebSocketTransport.__new__(CdpWebSocketTransport)
        transport._closed = False
        with self.assertRaisesRegex(CdpInputError, "not allowlisted"):
            transport.send("Runtime.evaluate", {"expression": "1+1"})

    def test_rejects_non_positive_timeout(self):
        with self.assertRaisesRegex(ValueError, "timeout"):
            CdpWebSocketTransport("ws://127.0.0.1:9222/devtools/page/1", timeout=0)

    def test_connect_selects_target_and_opens_transport(self):
        target = CdpTarget(
            "page-1", "Poi - KanColle", "app://poi",
            "ws://127.0.0.1:9222/devtools/page/page-1",
        )
        transport = object()
        with (
            patch("kancollebot.adapters.poi.cdp_targets.list_page_targets", return_value=(target,)),
            patch("kancollebot.adapters.poi.cdp_targets.select_poi_renderer", return_value=target),
            patch("kancollebot.adapters.poi.cdp_websocket.CdpWebSocketTransport", return_value=transport) as factory,
        ):
            session = connect_poi_session()
        self.assertIsInstance(session, CdpPoiSession)
        self.assertIs(session.target, target)
        self.assertIs(session.transport, transport)
        factory.assert_called_once_with(target.websocket_url, timeout=3.0)

    def test_connect_fails_closed_when_target_selection_is_ambiguous(self):
        with patch(
            "kancollebot.adapters.poi.cdp_targets.list_page_targets",
            return_value=(),
        ), patch("kancollebot.adapters.poi.cdp_websocket.CdpWebSocketTransport") as factory:
            with self.assertRaises(Exception):
                connect_poi_session()
            factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
