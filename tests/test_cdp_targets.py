from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from kancollebot.adapters.poi.cdp_targets import (
    CdpDiscoveryError,
    CdpTarget,
    list_page_targets,
    list_webview_targets,
    select_poi_renderer,
)


class CdpTargetTests(unittest.TestCase):
    def test_list_page_targets_filters_non_pages(self) -> None:
        payload = [
            {"type": "page", "id": "poi-1", "title": "Poi - KanColle",
             "url": "app://poi", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/1"},
            {"type": "service_worker", "id": "worker", "title": "worker",
             "url": "app://poi", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/2"},
            {"type": "page", "id": "missing", "title": "missing", "url": "app://poi"},
        ]
        with patch("kancollebot.adapters.poi.cdp_targets._get_json", return_value=payload):
            targets = list_page_targets()
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].target_id, "poi-1")

    def test_list_webview_targets_selects_embedded_game_not_poi_shell(self) -> None:
        payload = [
            {"type": "page", "id": "poi-shell", "title": "poi",
             "url": "file:///D:/Games/poi/resources/app.asar/index.html",
             "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/shell"},
            {"type": "webview", "id": "game-view", "title": "KanColle - DMM",
             "url": "https://play.games.dmm.com/game/kancolle",
             "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/game-view"},
            {"type": "service_worker", "id": "worker", "title": "worker",
             "url": "https://play.games.dmm.com/sw.js",
             "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/worker"},
        ]
        with patch("kancollebot.adapters.poi.cdp_targets._get_json", return_value=payload):
            targets = list_webview_targets()
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].target_id, "game-view")

    def test_non_loopback_endpoint_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            list_page_targets("http://192.0.2.10:9222")

    def test_non_loopback_websocket_target_is_rejected(self) -> None:
        payload = [{
            "type": "page", "id": "poi-1", "title": "Poi", "url": "app://poi",
            "webSocketDebuggerUrl": "ws://192.0.2.10:9222/devtools/page/1",
        }]
        with patch("kancollebot.adapters.poi.cdp_targets._get_json", return_value=payload):
            with self.assertRaises(CdpDiscoveryError):
                list_page_targets()

    def test_select_requires_exactly_one_match(self) -> None:
        targets = (
            CdpTarget("1", "Poi - KanColle", "app://poi",
                      "ws://127.0.0.1:9222/devtools/page/1"),
            CdpTarget("2", "Poi settings", "app://settings",
                      "ws://127.0.0.1:9222/devtools/page/2"),
        )
        self.assertEqual(select_poi_renderer(targets, title_hint="KanColle").target_id, "1")
        with self.assertRaises(CdpDiscoveryError):
            select_poi_renderer(targets, title_hint="Poi")

    def test_empty_hints_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            select_poi_renderer(() , title_hint="", url_hint="")


if __name__ == "__main__":
    unittest.main()
