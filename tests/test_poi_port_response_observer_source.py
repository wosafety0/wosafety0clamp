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


if __name__ == "__main__":
    unittest.main()
