from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from kancollebot import cli


class UnifiedCliTests(unittest.TestCase):
    def test_monitor_subcommand_forwards_all_arguments(self):
        implementation = Mock(return_value=0)
        module = type("RuntimeModule", (), {"_main_impl": implementation})()
        with patch.object(cli.importlib, "import_module", return_value=module) as importer:
            self.assertEqual(cli.main(["monitor", "--once", "--poll-seconds", "2"]), 0)
        importer.assert_called_once_with("run_continuous_expedition_monitor")
        implementation.assert_called_once_with(["--once", "--poll-seconds", "2"])

    def test_dispatch_subcommand_forwards_explicit_authorization_flag(self):
        implementation = Mock(return_value=2)
        module = type("RuntimeModule", (), {"_main_impl": implementation})()
        with patch.object(cli.importlib, "import_module", return_value=module) as importer:
            result = cli.main(["dispatch-once", "--authorize-ui-actions", "--max-state-age-seconds", "20"])
        self.assertEqual(result, 2)
        importer.assert_called_once_with("one_expedition_ui_loop")
        implementation.assert_called_once_with(["--authorize-ui-actions", "--max-state-age-seconds", "20"])

    def test_command_is_required(self):
        with self.assertRaises(SystemExit):
            cli.main([])


if __name__ == "__main__":
    unittest.main()
