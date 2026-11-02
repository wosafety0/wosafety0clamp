from __future__ import annotations

import unittest

from kancollebot.adapters.poi.cdp_input import CdpInput, CdpInputError, RecordedPoint


class FakeTransport:
    def __init__(self, *, fail_at: int | None = None, width: int = 1200, height: int = 720) -> None:
        self.calls = []
        self.fail_at = fail_at
        self.width = width
        self.height = height

    def send(self, method, params):
        self.calls.append((method, params))
        if self.fail_at == len(self.calls):
            raise OSError("simulated transport failure")
        if method == "Page.getLayoutMetrics":
            return {"cssVisualViewport": {"clientWidth": self.width, "clientHeight": self.height}}
        if method == "Page.captureScreenshot":
            return {"data": "cG5n"}
        return {}


class CdpInputTests(unittest.TestCase):
    def test_click_checks_viewport_then_sends_move_press_release(self):
        transport = FakeTransport()
        CdpInput(transport, settle_seconds=0).click(RecordedPoint(120, 80, "expedition-start"))
        self.assertEqual(
            [call[0] for call in transport.calls],
            ["Page.getLayoutMetrics"] + ["Input.dispatchMouseEvent"] * 3,
        )
        self.assertEqual(
            [call[1].get("type") for call in transport.calls[1:]],
            ["mouseMoved", "mousePressed", "mouseReleased"],
        )

    def test_visual_target_click_requires_matching_source_frame(self):
        import hashlib

        from kancollebot.adapters.poi.cdp_input import VisualTargetPoint

        payload = "cG5n"
        point = VisualTargetPoint(
            x=177.0,
            y=251.5,
            label="fleet2-ship1-checkbox",
            bounds=(166, 240, 22, 23),
            region=(150, 225, 55, 50),
            source_frame_sha256=hashlib.sha256(b"png").hexdigest(),
            locator="opencv-canny-square-v1",
        )
        transport = FakeTransport()
        CdpInput(transport, settle_seconds=0).click_visual_target(
            point, screenshot_base64=payload
        )
        self.assertEqual([call[0] for call in transport.calls],
                         ["Page.getLayoutMetrics"] + ["Input.dispatchMouseEvent"] * 3)

        mismatch = FakeTransport()
        with self.assertRaisesRegex(CdpInputError, "different screenshot"):
            CdpInput(mismatch, settle_seconds=0).click_visual_target(
                point, screenshot_base64="cG5nMg=="
            )
        self.assertEqual(mismatch.calls, [])

    def test_visual_target_point_rejects_bounds_outside_region(self):
        from kancollebot.adapters.poi.cdp_input import VisualTargetPoint
        with self.assertRaisesRegex(ValueError, "approved Fleet 2"):
            VisualTargetPoint(
                x=177.0, y=251.5, label="fleet2-ship1-checkbox",
                bounds=(166, 240, 22, 23), region=(150, 225, 10, 10),
                source_frame_sha256="a" * 64,
                locator="opencv-canny-square-v1",
            )

    def test_visual_target_point_rejects_unapproved_region_and_label(self):
        from kancollebot.adapters.poi.cdp_input import VisualTargetPoint
        for label, region in (
            ("fleet2-ship1-checkbox", (151, 225, 55, 50)),
            ("other-checkbox", (150, 225, 55, 50)),
        ):
            with self.subTest(label=label, region=region):
                with self.assertRaisesRegex(ValueError, "approved Fleet 2"):
                    VisualTargetPoint(
                        x=177.0, y=251.5, label=label,
                        bounds=(166, 240, 22, 23), region=region,
                        source_frame_sha256="a" * 64,
                        locator="opencv-canny-square-v1",
                    )

    def test_out_of_viewport_point_is_rejected_before_input(self):
        transport = FakeTransport(width=1200, height=720)
        with self.assertRaisesRegex(CdpInputError, "outside"):
            CdpInput(transport, settle_seconds=0).click(RecordedPoint(1200, 720, "edge"))
        self.assertEqual([call[0] for call in transport.calls], ["Page.getLayoutMetrics"])

    def test_transport_failure_is_reported_without_retry(self):
        transport = FakeTransport(fail_at=3)
        with self.assertRaises(CdpInputError):
            CdpInput(transport, settle_seconds=0).click(RecordedPoint(120, 80, "expedition-start"))
        self.assertEqual(len(transport.calls), 3)

    def test_screenshot_returns_payload(self):
        transport = FakeTransport()
        self.assertEqual(CdpInput(transport).capture_screenshot(), "cG5n")

    def test_invalid_coordinates_are_rejected(self):
        with self.assertRaises(ValueError):
            RecordedPoint(float("nan"), 20, "bad")
        with self.assertRaises(ValueError):
            RecordedPoint(True, 20, "bad")

    def test_invalid_button_is_rejected_before_transport(self):
        transport = FakeTransport()
        with self.assertRaises(ValueError):
            CdpInput(transport, settle_seconds=0).click(RecordedPoint(120, 80, "target"), button="x")
        self.assertEqual(transport.calls, [])


if __name__ == "__main__":
    unittest.main()
