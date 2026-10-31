from __future__ import annotations

import json
import threading
import unittest
from collections import deque
from unittest.mock import patch

from kancollebot.adapters.poi.cdp_input import CdpInputError
from kancollebot.adapters.poi.cdp_targets import CdpTarget
from kancollebot.adapters.poi.cdp_websocket import (
    CdpPoiSession,
    CdpWebSocketTransport,
    connect_game_webview,
    connect_poi_session,
    READ_ONLY_FLEET_SNAPSHOT_EXPRESSION,
)


class CdpWebSocketTransportTests(unittest.TestCase):
    def test_event_is_demultiplexed_before_matching_command_response(self):
        transport = CdpWebSocketTransport.__new__(CdpWebSocketTransport)
        transport._closed = False
        transport._next_id = 1
        transport._timeout = 1.0
        transport._event_queue = deque(maxlen=100)
        transport._dropped_event_count = 0
        transport._condition = threading.Condition()
        transport._command_lock = threading.Lock()
        transport._active_request_id = None
        transport._command_response = None
        transport._reader_error = None
        def fake_send(opcode, payload):
            request = json.loads(payload.decode('utf-8'))
            transport._route_message({"method": "Network.requestWillBeSent", "params": {"request": {"url": "https://example.invalid/api?token=abc", "method": "POST"}}})
            transport._route_message({"id": request["id"], "result": {"ok": True}})
        transport._send_frame = fake_send
        self.assertEqual(transport.send("Network.enable", {}), {"ok": True})
        events = transport.drain_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["method"], "Network.requestWillBeSent")
        self.assertIn("token=[REDACTED]", events[0]["params"]["request"]["url"])

    def test_event_queue_is_bounded_and_counts_dropped_oldest_events(self):
        transport = CdpWebSocketTransport.__new__(CdpWebSocketTransport)
        transport._event_queue = deque(maxlen=100)
        transport._dropped_event_count = 0
        transport._condition = threading.Condition()
        for index in range(1000):
            transport._dispatch_event({"method": "Network.requestWillBeSent", "params": {"requestId": str(index)}})
        events = transport.drain_events()
        self.assertEqual(len(events), 100)
        self.assertEqual(transport.dropped_event_count, 900)
        self.assertEqual(events[0]["params"]["requestId"], "900")
        self.assertEqual(events[-1]["params"]["requestId"], "999")

    def test_event_redacts_sensitive_headers_and_post_data_before_queueing(self):
        transport = CdpWebSocketTransport.__new__(CdpWebSocketTransport)
        transport._event_queue = deque(maxlen=100)
        transport._dropped_event_count = 0
        transport._condition = threading.Condition()
        transport._dispatch_event({"method": "Network.requestWillBeSent", "params": {"request": {
            "headers": {"Cookie": "password=secret", "Authorization": "Bearer abc", "Accept": "application/json"},
            "postData": json.dumps({"api_token": "sensitive", "fleet": 2}),
            "url": "https://example.invalid/api?api_token=secret&fleet=2",
        }}})
        request = transport.drain_events()[0]["params"]["request"]
        self.assertEqual(request["headers"]["Cookie"], "[REDACTED]")
        self.assertEqual(request["headers"]["Authorization"], "[REDACTED]")
        self.assertEqual(request["headers"]["Accept"], "application/json")
        self.assertNotIn("sensitive", request["postData"])
        self.assertIn("[REDACTED]", request["postData"])
        self.assertIn("api_token=[REDACTED]", request["url"])

    def test_reader_routes_events_while_no_command_is_pending(self):
        transport = CdpWebSocketTransport.__new__(CdpWebSocketTransport)
        transport._closed = False
        transport._event_queue = deque(maxlen=100)
        transport._dropped_event_count = 0
        transport._condition = threading.Condition()
        transport._active_request_id = None
        transport._command_response = None
        transport._reader_error = None
        messages = iter([
            {"method": "Network.responseReceived", "params": {"requestId": "idle-1", "response": {"status": 200}}},
        ])
        def receive_then_stop():
            try:
                return next(messages)
            except StopIteration:
                raise RuntimeError("mock reader stop")
        transport._receive_text = receive_then_stop
        transport._reader_loop()
        events = transport.drain_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["method"], "Network.responseReceived")
        self.assertEqual(events[0]["params"]["response"]["status"], 200)
        self.assertIsInstance(transport._reader_error, RuntimeError)

    def test_reader_does_not_route_unmatched_response_as_event(self):
        transport = CdpWebSocketTransport.__new__(CdpWebSocketTransport)
        transport._event_queue = deque(maxlen=100)
        transport._dropped_event_count = 0
        transport._condition = threading.Condition()
        transport._active_request_id = None
        transport._route_message({"id": 99, "result": {"ignored": True}})
        self.assertEqual(transport.drain_events(), [])

    def test_rejects_non_loopback_before_opening_socket(self):
        with patch("kancollebot.adapters.poi.cdp_websocket.socket.create_connection") as connect:
            with self.assertRaisesRegex(CdpInputError, "loopback"):
                CdpWebSocketTransport("ws://192.0.2.1:9222/devtools/page/1")
            connect.assert_not_called()

    def test_rejects_arbitrary_runtime_evaluate_without_socket_activity(self):
        transport = CdpWebSocketTransport.__new__(CdpWebSocketTransport)
        transport._closed = False
        with self.assertRaisesRegex(CdpInputError, "fixed read-only fleet snapshot"):
            transport.send("Runtime.evaluate", {"expression": "1+1", "returnByValue": True})

    def test_rejects_extra_runtime_evaluate_parameters_without_socket_activity(self):
        transport = CdpWebSocketTransport.__new__(CdpWebSocketTransport)
        transport._closed = False
        with self.assertRaisesRegex(CdpInputError, "fixed read-only fleet snapshot"):
            transport.send("Runtime.evaluate", {
                "expression": READ_ONLY_FLEET_SNAPSHOT_EXPRESSION,
                "returnByValue": True,
                "userGesture": True,
            })

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

    def test_connect_game_webview_uses_exact_embedded_game_target(self):
        target = CdpTarget(
            "game-view", "KanColle - DMM", "https://play.games.dmm.com/game/kancolle",
            "ws://127.0.0.1:9222/devtools/page/game-view",
        )
        transport = object()
        with (
            patch("kancollebot.adapters.poi.cdp_targets.list_webview_targets", return_value=(target,)),
            patch("kancollebot.adapters.poi.cdp_targets.select_poi_renderer", return_value=target) as select,
            patch("kancollebot.adapters.poi.cdp_websocket.CdpWebSocketTransport", return_value=transport) as factory,
        ):
            session = connect_game_webview()
        self.assertIsInstance(session, CdpPoiSession)
        self.assertIs(session.target, target)
        self.assertIs(session.transport, transport)
        select.assert_called_once_with(
            (target,), title_hint="", url_hint="https://play.games.dmm.com/game/kancolle"
        )
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
