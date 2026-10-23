import unittest
from unittest.mock import patch

from kancollebot.adapters.poi.cdp_probe import _connect_websocket


class CdpProbeLoopbackTests(unittest.TestCase):
    def test_localhost_is_connected_via_ipv4_loopback_literal(self):
        with patch(
            "kancollebot.adapters.poi.cdp_probe.socket.create_connection",
            side_effect=OSError("synthetic stop before socket creation"),
        ) as create_connection:
            with self.assertRaisesRegex(OSError, "synthetic stop"):
                _connect_websocket(
                    "ws://localhost:9222/devtools/page/test", timeout=0.2
                )
        create_connection.assert_called_once_with(
            ("127.0.0.1", 9222), timeout=0.2
        )


if __name__ == "__main__":
    unittest.main()
