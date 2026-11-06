"""Tests for the local web console safety boundary."""
from __future__ import annotations

import json
import unittest
from urllib.request import urlopen
from urllib.error import HTTPError

from kancollebot.web_ui import build_status, create_server


class FakeClient:
    def __init__(self, *, timeout: float) -> None:
        self.timeout = timeout

    def health(self) -> dict[str, object]:
        return {"status": "ok", "storeReady": True, "private": "must-not-leak"}


class FailingClient:
    def __init__(self, *, timeout: float) -> None:
        self.timeout = timeout

    def health(self) -> dict[str, object]:
        raise OSError("private endpoint detail")


class WebUiTests(unittest.TestCase):
    def test_status_sanitizes_bridge_response(self) -> None:
        status = build_status(FakeClient)
        self.assertEqual(status["poi_bridge"]["state"], "connected")
        self.assertNotIn("private", json.dumps(status))
        self.assertFalse(status["game_actions_enabled"])

    def test_bridge_failure_does_not_leak_exception_text(self) -> None:
        status = build_status(FailingClient)
        self.assertEqual(status["poi_bridge"]["state"], "error")
        self.assertNotIn("private endpoint detail", json.dumps(status))

    def test_server_serves_dashboard_and_read_only_status(self) -> None:
        server = create_server(port=0)
        try:
            server.timeout = 1
            server_thread = __import__("threading").Thread(target=server.serve_forever)
            server_thread.start()
            base = "http://127.0.0.1:%d" % server.server_port
            with urlopen(base + "/", timeout=2) as response:
                html = response.read().decode("utf-8")
                self.assertEqual(response.status, 200)
                self.assertIn("KancolleBot", html)
                self.assertIn('disabled title="Runner lifecycle API is not integrated"', html)
            with urlopen(base + "/api/health", timeout=2) as response:
                self.assertEqual(json.loads(response.read()), {
                    "status": "ok", "game_actions_enabled": False
                })
            with urlopen(base + "/api/status", timeout=2) as response:
                self.assertEqual(response.status, 200)
                self.assertIn("game_actions_enabled", json.loads(response.read()))
            with self.assertRaises(HTTPError) as caught:
                urlopen(base + "/missing", timeout=2)
            self.assertEqual(caught.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            if "server_thread" in locals():
                server_thread.join(timeout=2)

    def test_server_refuses_non_loopback_bind(self) -> None:
        with self.assertRaises(ValueError):
            create_server(host="0.0.0.0", port=8765)


if __name__ == "__main__":
    unittest.main()
