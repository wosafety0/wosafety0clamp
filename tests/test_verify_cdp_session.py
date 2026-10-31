from __future__ import annotations

import contextlib
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tools.verify_cdp_session import main


class VerifyCdpSessionTests(unittest.TestCase):
    def _session(self):
        session = MagicMock()
        session.__enter__.return_value = session
        session.__exit__.return_value = False
        session.target = SimpleNamespace(
            target_id="game-webview",
            title="KanColle",
            url="https://play.games.dmm.com/game/kancolle",
        )
        session.transport.send.return_value = {
            "cssVisualViewport": {"clientWidth": 1200, "clientHeight": 720}
        }
        return session

    def test_selects_embedded_game_webview_and_only_reads_layout_metrics(self):
        session = self._session()
        output = io.StringIO()
        with (
            patch("tools.verify_cdp_session.connect_game_webview", return_value=session) as connect,
            contextlib.redirect_stdout(output),
        ):
            result = main([])

        self.assertEqual(result, 0)
        connect.assert_called_once_with(
            "http://127.0.0.1:9222",
            url_hint="https://play.games.dmm.com/game/kancolle",
            timeout=3.0,
        )
        session.transport.send.assert_called_once_with("Page.getLayoutMetrics", {})
        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "PASS_READ_ONLY_CDP_PREFLIGHT")
        self.assertEqual(report["target"]["type"], "webview")
        self.assertEqual(report["target"]["url"], "https://play.games.dmm.com/game/kancolle")
        self.assertEqual(report["url_hint"], "https://play.games.dmm.com/game/kancolle")
        self.assertFalse(report["mouse_input_sent"])
        self.assertFalse(report["javascript_evaluated"])
        self.assertFalse(report["gameplay_action_sent"])

    def test_fails_closed_when_no_matching_game_webview_exists(self):
        output = io.StringIO()
        with (
            patch(
                "tools.verify_cdp_session.connect_game_webview",
                side_effect=RuntimeError("Expected exactly one matching target, found 0"),
            ) as connect,
            contextlib.redirect_stdout(output),
        ):
            result = main([])

        self.assertEqual(result, 2)
        connect.assert_called_once()
        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "FAILED")
        self.assertFalse(report["gameplay_action_sent"])
        self.assertNotIn("target", report)

    def test_rejects_invalid_viewport_without_reporting_success(self):
        session = self._session()
        session.transport.send.return_value = {"cssVisualViewport": {"clientWidth": 0, "clientHeight": 720}}
        output = io.StringIO()
        with (
            patch("tools.verify_cdp_session.connect_game_webview", return_value=session),
            contextlib.redirect_stdout(output),
        ):
            result = main([])

        self.assertEqual(result, 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "FAILED")
        session.transport.send.assert_called_once_with("Page.getLayoutMetrics", {})


if __name__ == "__main__":
    unittest.main()
