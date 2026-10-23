import json
import unittest
from unittest.mock import patch

from kancollebot.adapters.poi.cdp_probe import (
    CdpProbeError,
    _connect_websocket,
    _evaluate_read_only,
    _read_text_message,
    probe_poi_page,
)
from kancollebot.adapters.poi.cdp_targets import CdpTarget


class CdpProbeTests(unittest.TestCase):
    def test_non_loopback_websocket_rejected_before_network(self):
        with self.assertRaises(CdpProbeError):
            _connect_websocket("ws://192.0.2.1:9222/devtools/page/x", 0.2)

    def test_bad_scheme_rejected(self):
        with self.assertRaises(CdpProbeError):
            _connect_websocket("wss://127.0.0.1:9222/devtools/page/x", 0.2)

    def test_websocket_handshake_requires_upgrade_headers(self):
        import base64
        import hashlib

        class FakeSocket:
            def __init__(self, upgrade, connection):
                self.response = bytearray()
                self.upgrade = upgrade
                self.connection = connection
                self.closed = False

            def settimeout(self, timeout):
                pass

            def sendall(self, request):
                request_text = request.decode("ascii")
                key_line = next(line for line in request_text.split("\\r\\n") if line.startswith("Sec-WebSocket-Key:"))
                key = key_line.split(":", 1)[1].strip()
                accept = base64.b64encode(
                    hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
                ).decode("ascii")
                response = (
                    "HTTP/1.1 101 Switching Protocols\\r\\n"
                    f"Upgrade: {self.upgrade}\\r\\n"
                    f"Connection: {self.connection}\\r\\n"
                    f"Sec-WebSocket-Accept: {accept}\\r\\n\\r\\n"
                )
                self.response.extend(response.encode("ascii"))

            def recv(self, size):
                chunk = self.response[:size]
                del self.response[:size]
                return bytes(chunk)

            def close(self):
                self.closed = True

        for upgrade, connection in (("h2c", "Upgrade"), ("websocket", "keep-alive")):
            fake = FakeSocket(upgrade, connection)
            with self.subTest(upgrade=upgrade, connection=connection), \\
                 patch("kancollebot.adapters.poi.cdp_probe.socket.create_connection", return_value=fake):
                with self.assertRaisesRegex(CdpProbeError, "missing Upgrade headers"):
                    _connect_websocket("ws://127.0.0.1:9222/devtools/page/x", 0.2)
            self.assertTrue(fake.closed)

    def test_probe_rejects_url_change_after_target_discovery(self):
        target = CdpTarget("1", "Poi", "app://poi", "ws://127.0.0.1:9222/devtools/page/1")
        with patch("kancollebot.adapters.poi.cdp_probe.list_page_targets", return_value=(target,)), \
             patch("kancollebot.adapters.poi.cdp_probe._evaluate_read_only", return_value={
                 "title": "Poi", "url": "app://other", "readyState": "complete", "bodyText": ""
             }):
            probe = probe_poi_page()
        self.assertIsNone(probe.target_found)
        self.assertEqual(probe.probe_error, "CdpProbeError")
        self.assertIsNone(probe.document_ready_state)

    def test_probe_rejects_live_title_that_no_longer_matches_hint(self):
        target = CdpTarget("1", "Poi", "app://poi", "ws://127.0.0.1:9222/devtools/page/1")
        with patch("kancollebot.adapters.poi.cdp_probe.list_page_targets", return_value=(target,)), \
             patch("kancollebot.adapters.poi.cdp_probe._evaluate_read_only", return_value={
                 "title": "Unrelated page", "url": "app://poi", "readyState": "complete", "bodyText": ""
             }):
            probe = probe_poi_page(title_hint="poi")
        self.assertIsNone(probe.target_found)
        self.assertEqual(probe.probe_error, "CdpProbeError")

    def test_fragmented_text_message_is_reassembled(self):
        class FakeSocket:
            def __init__(self, data):
                self.data = bytearray(data)

            def recv(self, size):
                chunk = self.data[:size]
                del self.data[:size]
                return bytes(chunk)

        fake = FakeSocket(b"\x01\x01a\x80\x01b")
        self.assertEqual(_read_text_message(fake), "ab")

    def test_unexpected_continuation_frame_is_rejected(self):
        class FakeSocket:
            def __init__(self, data):
                self.data = bytearray(data)

            def recv(self, size):
                chunk = self.data[:size]
                del self.data[:size]
                return bytes(chunk)

        fake = FakeSocket(b"\x80\x01x")
        with self.assertRaises(CdpProbeError):
            _read_text_message(fake)

    def test_masked_server_frame_is_rejected(self):
        class FakeSocket:
            def __init__(self, data):
                self.data = bytearray(data)

            def recv(self, size):
                chunk = self.data[:size]
                del self.data[:size]
                return bytes(chunk)

        fake = FakeSocket(b"\x81\x81\x00\x00\x00\x00x")
        with self.assertRaisesRegex(CdpProbeError, "must not be masked"):
            _read_text_message(fake)

    def test_reserved_websocket_frame_bits_are_rejected(self):
        class FakeSocket:
            def __init__(self, data):
                self.data = bytearray(data)

            def recv(self, size):
                chunk = self.data[:size]
                del self.data[:size]
                return bytes(chunk)

        fake = FakeSocket(b"\xC1\x01x")
        with self.assertRaises(CdpProbeError):
            _read_text_message(fake)

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
