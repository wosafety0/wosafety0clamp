import json
import unittest
from datetime import datetime, timezone
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

    def test_websocket_urls_with_control_or_space_characters_rejected_before_network(self):
        for url in (
            "ws://127.0.0.1:9222/devtools/page/x\\r\\nInjected: header",
            "ws://127.0.0.1:9222/devtools/page/x\r\nInjected: header",
            "ws://127.0.0.1:9222/devtools/page/has space",
        ):
            with self.subTest(url=url):
                with patch(
                    "kancollebot.adapters.poi.cdp_probe.socket.create_connection"
                ) as connect:
                    with self.assertRaises(CdpProbeError):
                        _connect_websocket(url, 0.2)
                    connect.assert_not_called()

    def test_websocket_userinfo_and_query_strings_rejected_before_network(self):
        for url in (
            "ws://user:secret@127.0.0.1:9222/devtools/page/x",
            "ws://@127.0.0.1:9222/devtools/page/x",
            "ws://127.0.0.1:9222/devtools/page/x?access_token=secret",
        ):
            with self.subTest(url=url):
                with patch("kancollebot.adapters.poi.cdp_probe.socket.create_connection") as connect:
                    with self.assertRaises(CdpProbeError):
                        _connect_websocket(url, 0.2)
                    connect.assert_not_called()

    def test_bad_scheme_rejected(self):
        with self.assertRaises(CdpProbeError):
            _connect_websocket("wss://127.0.0.1:9222/devtools/page/x", 0.2)

    def test_non_finite_or_non_positive_websocket_timeouts_rejected_before_network(self):
        for timeout in (0, -1, float("nan"), float("inf"), -float("inf"), 10**1000, 1e308, 3600.001):
            with self.subTest(timeout=timeout):
                with patch("kancollebot.adapters.poi.cdp_probe.socket.create_connection") as connect:
                    with self.assertRaisesRegex(ValueError, "finite and positive"):
                        _connect_websocket("ws://127.0.0.1:9222/devtools/page/x", timeout)
                    connect.assert_not_called()

    def test_invalid_probe_timeout_types_rejected_before_network(self):
        target = CdpTarget(
            "1", "Poi", "app://poi", "ws://127.0.0.1:9222/devtools/page/1"
        )
        for timeout in (True, False, None, "1", [], {}, complex(1, 0)):
            with self.subTest(timeout=timeout):
                with patch(
                    "kancollebot.adapters.poi.cdp_probe.socket.create_connection"
                ) as connect:
                    with self.assertRaisesRegex(ValueError, "finite and positive"):
                        _connect_websocket(target.websocket_url, timeout)
                    with self.assertRaisesRegex(ValueError, "finite and positive"):
                        _evaluate_read_only(target, timeout)
                    connect.assert_not_called()
                with patch(
                    "kancollebot.adapters.poi.cdp_probe.list_page_targets"
                ) as discover:
                    with self.assertRaisesRegex(ValueError, "finite and positive"):
                        probe_poi_page(timeout=timeout)
                    discover.assert_not_called()

    def test_invalid_message_size_limit_rejected(self):
        class FakeSocket:
            def recv(self, size):
                raise AssertionError("invalid size limits must fail before reading")

        for limit in (0, -1, True, 1.5):
            with self.subTest(limit=limit):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    _read_text_message(FakeSocket(), max_message_bytes=limit)

    def test_handshake_deadline_expires_during_slow_header_stream(self):
        class SlowSocket:
            def __init__(self):
                self.closed = False
                self.timeouts = []

            def settimeout(self, timeout):
                self.timeouts.append(timeout)

            def sendall(self, request):
                pass

            def recv(self, size):
                return b"H"

            def close(self):
                self.closed = True

        fake = SlowSocket()
        with patch(
            "kancollebot.adapters.poi.cdp_probe.socket.create_connection",
            return_value=fake,
        ), patch(
            "kancollebot.adapters.poi.cdp_probe.time.monotonic",
            side_effect=(1.0, 1.0, 1.1, 1.2),
        ):
            with self.assertRaisesRegex(CdpProbeError, "handshake deadline exceeded"):
                _connect_websocket("ws://127.0.0.1:9222/devtools/page/x", 0.15)
        self.assertTrue(fake.closed)
        self.assertTrue(fake.timeouts)

    def test_websocket_handshake_requires_upgrade_headers(self):
        import base64
        import hashlib

        class FakeSocket:
            def __init__(self, upgrade, connection, status_line="HTTP/1.1 101 Switching Protocols", extra_headers=""):
                self.response = bytearray()
                self.upgrade = upgrade
                self.connection = connection
                self.status_line = status_line
                self.extra_headers = extra_headers
                self.closed = False

            def settimeout(self, timeout):
                pass

            def sendall(self, request):
                request_text = request.decode("ascii")
                key_line = next(line for line in request_text.split("\r\n") if line.startswith("Sec-WebSocket-Key:"))
                key = key_line.split(":", 1)[1].strip()
                accept = base64.b64encode(
                    hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
                ).decode("ascii")
                response = (
                    f"{self.status_line}\r\n"
                    f"Upgrade: {self.upgrade}\r\n"
                    f"Connection: {self.connection}\r\n"
                    f"Sec-WebSocket-Accept: {accept}\r\n"
                    f"{self.extra_headers}"
                    "\r\n"
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
            with self.subTest(upgrade=upgrade, connection=connection):
                with patch("kancollebot.adapters.poi.cdp_probe.socket.create_connection", return_value=fake):
                    with self.assertRaisesRegex(CdpProbeError, "missing Upgrade headers"):
                        _connect_websocket("ws://127.0.0.1:9222/devtools/page/x", 0.2)
            self.assertTrue(fake.closed)

    def test_websocket_handshake_rejects_malformed_status_and_headers(self):
        cases = (
            ("HTTP/1.1 1010 Switching Protocols", "", "upgrade was rejected"),
            ("HTTP/1.1 101 Switching Protocols", "Upgrade: websocket\r\n", "duplicate or empty headers"),
            ("HTTP/1.1 101 Switching Protocols", "malformed-header\r\n", "malformed header"),
        )
        for status_line, extra_headers, expected_error in cases:
            fake = self._make_handshake_fake(status_line, extra_headers)
            with self.subTest(status_line=status_line, extra_headers=extra_headers):
                with patch("kancollebot.adapters.poi.cdp_probe.socket.create_connection", return_value=fake):
                    with self.assertRaisesRegex(CdpProbeError, expected_error):
                        _connect_websocket("ws://127.0.0.1:9222/devtools/page/x", 0.2)
            self.assertTrue(fake.closed)

    @staticmethod
    def _make_handshake_fake(status_line, extra_headers):
        import base64
        import hashlib

        class FakeSocket:
            def __init__(self):
                self.response = bytearray()
                self.closed = False

            def settimeout(self, timeout):
                pass

            def sendall(self, request):
                request_text = request.decode("ascii")
                key_line = next(line for line in request_text.split("\r\n") if line.startswith("Sec-WebSocket-Key:"))
                key = key_line.split(":", 1)[1].strip()
                accept = base64.b64encode(
                    hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
                ).decode("ascii")
                response = (
                    f"{status_line}\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    f"Sec-WebSocket-Accept: {accept}\r\n"
                    f"{extra_headers}"
                    "\r\n"
                )
                self.response.extend(response.encode("ascii"))

            def recv(self, size):
                chunk = self.response[:size]
                del self.response[:size]
                return bytes(chunk)

            def close(self):
                self.closed = True

        return FakeSocket()

    def test_websocket_handshake_rejects_invalid_header_names_values_and_unsolicited_negotiation(self):
        cases = (
            ("Bad Header: value\\r\\n", "invalid headers"),
            ("Bad\\x01Name: value\\r\\n", "invalid headers"),
            ("X-Test: bad\\x01value\\r\\n", "invalid header values"),
            ("Sec-WebSocket-Protocol: cdp\\r\\n", "unrequested extension or subprotocol"),
            ("Sec-WebSocket-Extensions: permessage-deflate\\r\\n", "unrequested extension or subprotocol"),
        )
        for extra_headers, expected_error in cases:
            extra_headers = extra_headers.encode("ascii").decode("unicode_escape")
            fake = self._make_handshake_fake("HTTP/1.1 101 Switching Protocols", extra_headers)
            with self.subTest(extra_headers=repr(extra_headers)):
                with patch(
                    "kancollebot.adapters.poi.cdp_probe.socket.create_connection",
                    return_value=fake,
                ):
                    with self.assertRaisesRegex(CdpProbeError, expected_error):
                        _connect_websocket("ws://127.0.0.1:9222/devtools/page/x", 0.2)
            self.assertTrue(fake.closed)

    def test_websocket_handshake_allows_horizontal_tab_in_header_value(self):
        fake = self._make_handshake_fake(
            "HTTP/1.1 101 Switching Protocols",
            "X-Test: left\tright\r\n",
        )
        with patch(
            "kancollebot.adapters.poi.cdp_probe.socket.create_connection",
            return_value=fake,
        ):
            sock = _connect_websocket("ws://127.0.0.1:9222/devtools/page/x", 0.2)
        self.assertIs(sock, fake)
        sock.close()

    def test_display_url_redacts_userinfo_query_and_fragment_without_changing_identity_url(self):
        from kancollebot.adapters.poi.cdp_probe import CdpPageProbe

        raw_url = "https://user:password@example.test/game?token=secret&x=1#private"
        probe = CdpPageProbe(
            checked_at=datetime.now(timezone.utc),
            target_found=True,
            title="Poi",
            url=raw_url,
            document_ready_state="complete",
            error_text="",
        )
        self.assertEqual(probe.url, "https://example.test/game")
        self.assertEqual(probe.display_url, "https://example.test/game")
        self.assertNotIn("password", probe.url)
        self.assertNotIn("token=secret", probe.url)
        self.assertNotIn("private", probe.url)
        self.assertEqual(len(probe.url_identity), 64)
        self.assertNotEqual(probe.url_identity, raw_url)

    def test_invalid_display_url_is_safely_redacted(self):
        from kancollebot.adapters.poi.cdp_probe import _sanitize_display_url

        self.assertEqual(_sanitize_display_url("https://[invalid"), "[invalid-url]")

    def test_url_identity_digest_detects_query_only_navigation_without_retaining_query(self):
        from kancollebot.adapters.poi.cdp_probe import CdpPageProbe

        base = dict(
            checked_at=datetime.now(timezone.utc),
            target_found=True,
            title="Poi",
            document_ready_state="complete",
            error_text="",
        )
        first = CdpPageProbe(url="https://example.test/game?token=first", **base)
        second = CdpPageProbe(url="https://example.test/game?token=second", **base)
        self.assertEqual(first.url, "https://example.test/game")
        self.assertEqual(second.url, "https://example.test/game")
        self.assertNotEqual(first.url_identity, second.url_identity)
        self.assertNotIn("first", first.url_identity)
        self.assertNotIn("second", second.url_identity)

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

    def test_read_exact_rejects_expired_overall_deadline(self):
        class FakeSocket:
            def settimeout(self, timeout):
                self.timeout = timeout

            def recv(self, size):
                raise AssertionError("recv must not be called after the deadline")

        from kancollebot.adapters.poi.cdp_probe import _read_exact

        with patch("kancollebot.adapters.poi.cdp_probe.time.monotonic", return_value=2.0):
            with self.assertRaisesRegex(CdpProbeError, "response deadline exceeded"):
                _read_exact(FakeSocket(), 1, deadline=1.0)

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

    def test_malformed_runtime_response_shapes_fail_closed(self):
        class FakeSocket:
            def __init__(self):
                self.closed = False

            def settimeout(self, timeout):
                pass

            def close(self):
                self.closed = True

        target = CdpTarget(
            "1", "Poi", "app://poi", "ws://127.0.0.1:9222/devtools/page/1"
        )
        for payload in (
            {"id": 1, "result": None},
            {"id": 1, "result": []},
            {"id": 1, "result": {"result": None}},
            {"id": 1, "result": {"result": []}},
        ):
            with self.subTest(payload=payload):
                fake = FakeSocket()
                with patch(
                    "kancollebot.adapters.poi.cdp_probe._connect_websocket",
                    return_value=fake,
                ), patch(
                    "kancollebot.adapters.poi.cdp_probe._send_frame"
                ), patch(
                    "kancollebot.adapters.poi.cdp_probe._read_text_message",
                    return_value=json.dumps(payload),
                ):
                    with self.assertRaises(CdpProbeError):
                        _evaluate_read_only(target, 1.0)
                self.assertTrue(fake.closed)

    def test_read_only_runtime_evaluation_with_mocked_socket(self):
        class FakeSocket:
            def __init__(self):
                self.closed = False

            def settimeout(self, timeout):
                self.timeout = timeout

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
