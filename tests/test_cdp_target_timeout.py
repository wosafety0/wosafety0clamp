import unittest
from unittest.mock import patch

from kancollebot.adapters.poi.cdp_targets import list_page_targets


class CdpTargetTimeoutTests(unittest.TestCase):
    def test_non_finite_or_non_positive_timeout_is_rejected_before_network(self):
        invalid_timeouts = (
            0,
            -1,
            float("nan"),
            float("inf"),
            -float("inf"),
            True,
            "1",
        )
        for timeout in invalid_timeouts:
            with self.subTest(timeout=timeout):
                with patch(
                    "kancollebot.adapters.poi.cdp_targets._get_json"
                ) as get_json:
                    with self.assertRaisesRegex(
                        ValueError, "timeout must be finite and positive"
                    ):
                        list_page_targets(
                            "http://127.0.0.1:9222", timeout=timeout
                        )
                    get_json.assert_not_called()


if __name__ == "__main__":
    unittest.main()
