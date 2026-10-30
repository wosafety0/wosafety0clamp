"""Static safety contract for passive observer output-mode persistence."""
import unittest
from pathlib import Path


SOURCE = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "hand_sandbox"
    / "poi_port_response_observer_20261029.js"
)


class PassivePortObserverSourceTests(unittest.TestCase):
    def test_witness_mode_does_not_persist_snapshot(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn(
            "const witnessMode = process.env.KCB_POI_FRESHNESS_WITNESS_STDOUT === '1';",
            source,
        )
        start = source.index("if (witnessMode) {", source.index("const witnessMode"))
        split = source.index("} else {", start)
        end = source.index("return {", split)
        witness_branch = source[start:split]
        disk_branch = source[split:end]
        self.assertIn("persisted: false", witness_branch)
        self.assertNotIn("fs.mkdirSync", witness_branch)
        self.assertNotIn("fs.writeFileSync", witness_branch)
        self.assertNotIn("filePath", witness_branch)
        self.assertIn("persisted: true", disk_branch)
        self.assertIn("fs.mkdirSync", disk_branch)
        self.assertIn("fs.writeFileSync", disk_branch)
        self.assertIn(
            "witnessJson: witnessMode ? JSON.stringify(snapshot) : null",
            source,
        )


    def test_target_discovery_rejects_non_array_json_before_filtering(self):
        source = SOURCE.read_text(encoding="utf-8")
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
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("wsUrl.protocol !== 'ws:'", source)
        self.assertIn("wsUrl.port !== '9222'", source)
        self.assertIn("wsUrl.username || wsUrl.password || wsUrl.hash", source)
        self.assertIn("'127.0.0.1', 'localhost', '[::1]', '::1'", source)
        self.assertIn("send('Network.enable'", source)
        self.assertIn("send('Network.getResponseBody'", source)
        self.assertNotIn("send('Runtime.evaluate'", source)
        self.assertNotIn("send('Runtime.callFunctionOn'", source)


if __name__ == "__main__":
    unittest.main()
