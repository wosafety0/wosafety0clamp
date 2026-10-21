from __future__ import annotations

import unittest

from kancollebot.adapters.poi.cdp_input import (
    CdpInput,
    CdpInputError,
    RecordedPoint,
)


class FakeTransport:
    def __init__(self, *, fail_at: int | None = None) -> None:
        self.calls = []
        self.fail_at = fail_at

    def send(self, method, params):
        self.calls.append((method, params))
        if self.fail_at == len(self.calls):
            raise OSError("simulated transport failure")
        if method == "Page.captureScreenshot":
            return {"data": "cG5n"}
        return {}


class CdpInputTests(unittest.TestCase):
    def test_click_sends_move_press_release_in_order(self):
        transport = FakeTransport()
        CdpInput(transport, settle_seconds=0).click(
            RecordedPoint(120, 80, "expedition-start")
        )
        self.assertEqual(
            [call[1]["type"] for call in transport.calls],
            ["mouseMoved", "mousePressed", "mouseReleased"],
        )
        self.assertEqual(transport.calls[1][1]["button"], "left")
        self.assertEqual(transport.calls[2][1]["button"], "left")

    def test_transport_failure_is_reported_as_ambiguous_without_retry(self):
        transport = FakeTransport(fail_at=2)
        with self.assertRaises(CdpInputError):
            CdpInput(transport, settle_seconds=0).click(
                RecordedPoint(120, 80, "expedition-start")
            )
        self.assertEqual(len(transport.calls), 2)

    def test_screenshot_returns_payload(self):
        transport = FakeTransport()
        self.assertEqual(CdpInput(transport).capture_screenshot(), "cG5n")

    def test_invalid_coordinates_are_rejected(self):
        with self.assertRaises(ValueError):
            RecordedPoint(float("nan"), 20, "bad")

    def test_invalid_button_is_rejected_before_input(self):
        transport = FakeTransport()
        with self.assertRaises(ValueError):
            CdpInput(transport, settle_seconds=0).click(
                RecordedPoint(120, 80, "target"), button="x"
            )
        self.assertEqual(transport.calls, [])


if __name__ == "__main__":
    unittest.main()
