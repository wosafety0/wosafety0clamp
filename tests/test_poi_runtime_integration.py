import json
import threading
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.request import Request, urlopen

from kancollebot.adapters.poi.cdp_probe import CdpPageProbe
from kancollebot.adapters.poi.runtime import PoiPreflightConfig, run_read_only_preflight
from kancollebot.adapters.poi.webhook_receiver import PoiWebhookReceiver, WEBHOOK_PATH


class PoiRuntimeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.payloads = {
            "/fleets": [
                {
                    "api_id": 1,
                    "api_name": "第1艦隊",
                    "api_ship": [73, -1, -1, -1, -1, -1],
                    "api_mission": [0, 0, 0, 0],
                }
            ],
            "/ships": {
                "73": {"api_name": "赤城", "api_nowhp": 45, "api_maxhp": 69}
            },
        }
        payloads = self.payloads

        class StateHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path not in payloads:
                    self.send_response(404)
                    self.end_headers()
                    return
                body = json.dumps(payloads[self.path], ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                return

        self.state_server = HTTPServer(("127.0.0.1", 0), StateHandler)
        self.state_thread = threading.Thread(target=self.state_server.serve_forever, daemon=True)
        self.state_thread.start()
        self.http_url = f"http://127.0.0.1:{self.state_server.server_address[1]}"

    def tearDown(self):
        self.state_server.shutdown()
        self.state_server.server_close()
        self.state_thread.join(timeout=2)

    def test_runtime_composes_real_receiver_http_adapter_and_health_gate(self):
        now = datetime.now(timezone.utc)

        class ForwarderFixtureReceiver(PoiWebhookReceiver):
            def start(inner_self):
                port = super(ForwarderFixtureReceiver, inner_self).start()
                event = {
                    "path": "/kcsapi/api_port/port",
                    "request": None,
                    "response": {"api_result": 1, "api_data": {}},
                    "stage": "response_received",
                    "timestamp": now.timestamp() * 1000,
                }
                request = Request(
                    f"http://127.0.0.1:{port}{WEBHOOK_PATH}",
                    data=json.dumps(event).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    if response.status != 200:
                        raise AssertionError("fixture Forwarder event was not accepted")
                return port

        def fake_probe(**kwargs):
            return CdpPageProbe(
                checked_at=datetime.now(timezone.utc),
                target_found=True,
                title="Poi - KanColle",
                url="app://poi",
                document_ready_state="complete",
                error_text="KanColle",
            )

        code, report = run_read_only_preflight(
            PoiPreflightConfig(
                http_url=self.http_url,
                cdp_endpoint="http://127.0.0.1:9222",
                title_hint="poi",
                webhook_port=0,
                event_timeout_seconds=2,
            ),
            receiver_factory=ForwarderFixtureReceiver,
            probe_factory=fake_probe,
        )

        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "PASS_READ_ONLY_HEALTH_GATED")
        self.assertTrue(report["forwarder_event_accepted"])
        self.assertTrue(report["page_probe_performed"])
        self.assertTrue(report["http_reads_performed"])
        self.assertTrue(report["http_diagnostic_completed"])
        self.assertEqual(
            report["gates"],
            {
                "forwarder_event_accepted": True,
                "source_event_fresh": True,
                "cdp_target_identity_validated": True,
                "cdp_document_ready": True,
                "fleets_endpoint_read": True,
                "ships_endpoint_read": True,
                "fleet_ship_references_valid": True,
                "gameplay_actions_disabled": True,
            },
        )
        self.assertFalse(report["actions_enabled"])
        self.assertFalse(report["gameplay_actions_enabled"])
        self.assertTrue(report["diagnostic"]["ok"])
        self.assertEqual(report["diagnostic"]["fleet_count"], 1)
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("赤城", serialized)
        self.assertNotIn('"73"', serialized)

    def test_accepted_event_remains_reported_when_page_probe_fails(self):
        now = datetime.now(timezone.utc)

        class ForwarderFixtureReceiver(PoiWebhookReceiver):
            def start(inner_self):
                port = super(ForwarderFixtureReceiver, inner_self).start()
                event = {
                    "path": "/kcsapi/api_port/port",
                    "request": None,
                    "response": {"api_result": 1, "api_data": {}},
                    "stage": "response_received",
                    "timestamp": now.timestamp() * 1000,
                }
                request = Request(
                    f"http://127.0.0.1:{port}{WEBHOOK_PATH}",
                    data=json.dumps(event).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    self.assertEqual(response.status, 200)
                return port

        def failing_probe(**kwargs):
            raise RuntimeError("synthetic CDP failure")

        code, report = run_read_only_preflight(
            PoiPreflightConfig(
                http_url=self.http_url,
                webhook_port=0,
                event_timeout_seconds=2,
            ),
            receiver_factory=ForwarderFixtureReceiver,
            probe_factory=failing_probe,
        )
        self.assertEqual(code, 2)
        self.assertEqual(report["status"], "FAILED")
        self.assertEqual(report["reason"], "preflight_failed_RuntimeError")
        self.assertTrue(report["forwarder_event_accepted"])
        self.assertTrue(report["gates"]["forwarder_event_accepted"])
        self.assertTrue(report["gates"]["source_event_fresh"])
        self.assertFalse(report["http_reads_performed"])
        self.assertFalse(report["actions_enabled"])

    def test_legacy_http_module_entrypoint_delegates_to_gated_runtime(self):
        from unittest.mock import patch
        from kancollebot.adapters.poi import http_read_only

        with patch("kancollebot.adapters.poi.runtime.main", return_value=7) as runtime_main:
            self.assertEqual(http_read_only.main(), 7)
        runtime_main.assert_called_once_with()

    def test_missing_forwarder_event_fails_before_any_http_read(self):
        calls = {"http_adapter_constructed": 0, "probe": 0}

        class SilentReceiver:
            def __init__(self, **kwargs):
                self.port = kwargs["port"]

            def start(self):
                return 9223

            def get_event(self, timeout=0):
                return None

            def stop(self):
                return None

        class AdapterFactory:
            def __init__(self, **kwargs):
                calls["http_adapter_constructed"] += 1

            def diagnose(self):
                raise AssertionError("HTTP read must not happen without a fresh event")

        def fake_probe(**kwargs):
            calls["probe"] += 1
            raise AssertionError("CDP probe must not run without a fresh event")

        ticks = iter([0.0, 0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0])
        def monotonic():
            try:
                return next(ticks)
            except StopIteration:
                return 3.0

        code, report = run_read_only_preflight(
            PoiPreflightConfig(webhook_port=0, event_timeout_seconds=1),
            receiver_factory=SilentReceiver,
            adapter_factory=AdapterFactory,
            probe_factory=fake_probe,
            monotonic=monotonic,
        )

        self.assertEqual(code, 2)
        self.assertEqual(report["reason"], "fresh_successful_forwarder_event_timeout")
        self.assertFalse(report["http_reads_performed"])
        self.assertFalse(report["actions_enabled"])
        self.assertEqual(calls["http_adapter_constructed"], 1)
        self.assertEqual(calls["probe"], 0)


if __name__ == "__main__":
    unittest.main()
