from __future__ import annotations

import unittest
from unittest.mock import patch

from kancollebot.adapters.poi.cdp_input import CdpInputError
from kancollebot.adapters.poi.cdp_websocket import CdpWebSocketTransport


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


if __name__ == "__main__":
    unittest.main()
