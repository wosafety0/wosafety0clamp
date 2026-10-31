from __future__ import annotations

import unittest

from kancollebot.adapters.poi.cdp_websocket import _ALLOWED_METHODS


class CdpWebSocketAllowlistTests(unittest.TestCase):
    def test_browser_reload_is_explicitly_allowlisted_for_ui_recovery(self):
        self.assertIn("Page.reload", _ALLOWED_METHODS)

    def test_passive_network_observation_is_allowed_but_mutation_methods_are_blocked(self):
        self.assertIn("Network.enable", _ALLOWED_METHODS)
        self.assertIn("Network.disable", _ALLOWED_METHODS)
        self.assertNotIn("Page.navigate", _ALLOWED_METHODS)
        self.assertNotIn("Fetch.enable", _ALLOWED_METHODS)
        self.assertNotIn("Network.setCookie", _ALLOWED_METHODS)


if __name__ == "__main__":
    unittest.main()
