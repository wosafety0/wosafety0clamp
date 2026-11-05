"""Static safety contract for the passive Poi response observer."""
import re
import unittest
from pathlib import Path


SOURCE = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "hand_sandbox"
    / "poi_port_response_observer_20261029.js"
)

#: The CDP plumbing both observers share. The safety contract lives here
#: now: one mechanism, one place to check it.
CORE = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "hand_sandbox"
    / "poi_cdp_capture_core.js"
)

#: The expedition-result observer, which shares that core.
RESULT_OBSERVER = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "hand_sandbox"
    / "poi_mission_result_observer_20261105.js"
)


class PassivePortObserverSourceTests(unittest.TestCase):
    def test_child_only_witness_mode_skips_snapshot_persistence(self):
        source = SOURCE.read_text(encoding="utf-8")
        start = source.index("function saveSnapshot")
        end = source.index("function parseApiResponse", start)
        save_block = source[start:end]
        self.assertIn(
            "const stdoutWitness = process.env.KCB_POI_FRESHNESS_WITNESS_STDOUT === '1';",
            save_block,
        )
        self.assertIn("if (!stdoutWitness) {", save_block)
        self.assertEqual(save_block.count("fs.mkdirSync"), 1)
        self.assertEqual(save_block.count("fs.writeFileSync"), 1)
        persist_start = save_block.index("if (!stdoutWitness) {")
        persist_end = save_block.index("\n    }\n    return {", persist_start)
        persist_branch = save_block[persist_start:persist_end]
        self.assertIn("fs.mkdirSync", persist_branch)
        self.assertIn("fs.writeFileSync", persist_branch)
        self.assertIn(
            "witnessJson: stdoutWitness ? JSON.stringify(snapshot) : null",
            save_block,
        )

    def test_target_discovery_rejects_non_array_json_before_filtering(self):
        source = CORE.read_text(encoding="utf-8")
        parse_at = source.index("targets = JSON.parse(body)")
        guard_at = source.index("if (!Array.isArray(targets))", parse_at)
        filter_at = source.index("targets.filter(safeGameTarget)", parse_at)
        self.assertLess(parse_at, guard_at)
        self.assertLess(guard_at, filter_at)
        self.assertIn(
            "return cleanup('TARGET_DISCOVERY_FAILED', 'Local CDP target list must be an array.');",
            source[guard_at:filter_at],
        )

    def test_cdp_socket_remains_loopback_and_network_read_only(self):
        source = CORE.read_text(encoding="utf-8")
        self.assertIn("const TARGETS_URL = 'http://127.0.0.1:9222/json/list';", source)
        self.assertIn("wsUrl.protocol !== 'ws:'", source)
        self.assertIn("wsUrl.port !== '9222'", source)
        self.assertIn("wsUrl.username || wsUrl.password || wsUrl.hash", source)
        self.assertIn("['127.0.0.1', 'localhost', '[::1]', '::1']", source)
        self.assertIn("send('Network.enable'", source)
        self.assertIn("send('Network.getResponseBody'", source)
        self.assertNotIn("send('Runtime.evaluate'", source)
        self.assertNotIn("send('Runtime.callFunctionOn'", source)

    def test_observer_has_no_input_or_page_navigation_call_sites(self):
        sources = {
            "port": SOURCE.read_text(encoding="utf-8"),
            "core": CORE.read_text(encoding="utf-8"),
            "result": RESULT_OBSERVER.read_text(encoding="utf-8"),
        }
        forbidden_methods = (
            "Input.dispatchMouseEvent",
            "Input.dispatchKeyEvent",
            "Page.navigate",
            "Page.reload",
            "Runtime.getProperties",
            "Fetch.enable",
            "Network.setCookie",
            "Network.replayXHR",
        )
        for name, source in sources.items():
            for method in forbidden_methods:
                with self.subTest(observer=name, method=method):
                    self.assertNotIn(f"send('{method}'", source)
                    self.assertNotIn(f'send("{method}"', source)

    def test_observer_send_call_sites_are_literal_network_allowlist(self):
        source = CORE.read_text(encoding="utf-8")
        source = re.sub(r"function\s+send\s*\([^)]*\)\s*\{", "", source, count=1)
        call_sites = re.findall(r"(?<![\w.])send\s*\(", source)
        literal_calls = re.findall(
            r"(?<![\w.])send\s*\(\s*(['\"])([^'\"]+)\1",
            source,
        )
        self.assertEqual(len(call_sites), len(literal_calls))
        self.assertEqual(
            {method for _, method in literal_calls},
            {"Network.enable", "Network.getResponseBody"},
        )
        self.assertEqual(len(literal_calls), 2)

    def test_both_observers_share_the_one_capture_core(self):
        # One Network observation mechanism, not one per endpoint: the port
        # observer and the expedition-result observer both go through it.
        for source_path in (SOURCE, RESULT_OBSERVER):
            with self.subTest(observer=source_path.name):
                source = source_path.read_text(encoding="utf-8")
                self.assertIn("require('./poi_cdp_capture_core.js')", source)
                self.assertIn("createCaptureCore({", source)

    def test_the_result_observer_reads_only_the_fleet_id(self):
        # The request body may carry anything; only api_deck_id may be read,
        # and the raw body must never be written out.
        source = RESULT_OBSERVER.read_text(encoding="utf-8")
        self.assertIn("'api_deck_id'", source)
        self.assertIn("rawRequestBodySaved: false", source)
        self.assertIn("rawResponseBodySaved: false", source)
        self.assertNotIn("action.postBody", source)


if __name__ == "__main__":
    unittest.main()
