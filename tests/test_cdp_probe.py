import json
import unittest
from unittest.mock import patch

from kancollebot.adapters.poi.cdp_probe import (
    CdpProbeError,
    _connect_websocket,
    _evaluate_read_only,
)
from kancollebot.adapters.poi.cdp_targets import CdpTarget


class CdpProbeTests(unittest.TestCase):
    def test_non_loopback_websocket_rejected_before_network(self):
        with self.assertRaises(CdpProbeError):
            _connect_websocket("ws://192.0.2.1:9222/devtools/page/x", 0.2)

    def test_bad_scheme_rejected(self):
        with self.assertRaises(CdpProbeError):
            _connect_websocket("wss://127.0.0.1:9222/devtools/page/x", 0.2)

    def test_read_only_runtime_evaluation_with_mocked_socket(self):
        class FakeSocket:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        fake = FakeSocket()
        payload = {
            "id": 1,
            "result": {
                "result": {
                    "type": "string",
                    "value": json.dumps({
                        "title": "Poi - KanColle",
                        "url": "app://poi",
                        "readyState": "complete",
                        "bodyText": "KanColle",
                    })
                }
            }
        }
        target = CdpTarget("1", "Poi", "app://poi", "ws://127.0.0.1:9222/devtools/page/1")
        with patch("kancollebot.adapters.poi.cdp_probe._connect_websocket", return_value=fake), \
             patch("kancollebot.adapters.poi.cdp_probe._send_frame") as send, \
             patch("kancollebot.adapters.poi.cdp_probe._read_text_message", return_value=json.dumps(payload)):
            data = _evaluate_read_only(target, 1.0)
        self.assertEqual(data["readyState"], "complete")
        sent_command = json.loads(send.call_args.args[1].decode())
        self.assertEqual(sent_command["method"], "Runtime.evaluate")
        self.assertTrue(sent_command["params"]["returnByValue"])
        self.assertNotIn("Input.dispatchMouseEvent", sent_command["method"])
        self.assertTrue(fake.closed)


if __name__ == "__main__":
    unittest.main()
