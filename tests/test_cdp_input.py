from __future__ import annotations

import os
import unittest

from kancollebot.adapters.poi.cdp_input import (
    CdpInput,
    CdpInputError,
    RecordedPoint,
    home_port_candidates,
    home_port_point,
)


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

    def test_visual_target_point_accepts_only_exact_approved_fleet2_ship_regions(self):
        from kancollebot.adapters.poi.cdp_input import VisualTargetPoint

        approved = (
            ("fleet2-ship1-checkbox", (150, 225, 55, 50), (166, 240, 22, 23)),
            ("fleet2-ship2-checkbox", (150, 302, 55, 50), (166, 317, 22, 23)),
            ("fleet2-ship3-checkbox", (150, 379, 55, 50), (166, 394, 22, 23)),
            ("fleet2-ship4-checkbox", (150, 456, 55, 50), (166, 471, 22, 23)),
        )
        for label, region, bounds in approved:
            with self.subTest(label=label):
                x, y, width, height = bounds
                point = VisualTargetPoint(
                    x=x + width / 2, y=y + height / 2, label=label,
                    bounds=bounds, region=region,
                    source_frame_sha256="a" * 64,
                    locator="opencv-canny-square-v1",
                )
                self.assertEqual(point.region, region)

    def test_visual_target_point_accepts_fleet3_rows_only_in_independent_regions(self):
        from kancollebot.adapters.poi.cdp_input import VisualTargetPoint

        rows = (
            ("fleet3-ship1-checkbox", (150, 225, 55, 50), (166, 240, 22, 23)),
            ("fleet3-ship2-checkbox", (150, 302, 55, 50), (166, 317, 22, 23)),
            ("fleet3-ship3-checkbox", (150, 379, 55, 50), (166, 394, 22, 23)),
            ("fleet3-ship4-checkbox", (150, 456, 55, 50), (166, 471, 22, 23)),
        )
        for label, region, bounds in rows:
            with self.subTest(label=label):
                x, y, width, height = bounds
                point = VisualTargetPoint(
                    x=x + width / 2, y=y + height / 2, label=label,
                    bounds=bounds, region=region,
                    source_frame_sha256="c" * 64,
                    locator="opencv-canny-square-v1",
                )
                self.assertEqual(point.label, label)

    def test_visual_target_point_accepts_only_bounded_fleet_wide_supply_button(self):
        from kancollebot.adapters.poi.cdp_input import VisualTargetPoint

        point = VisualTargetPoint(
            x=1108.0, y=619.0, label="fleet-supply-all-button",
            bounds=(1062, 604, 92, 30), region=(1050, 590, 145, 60),
            source_frame_sha256="d" * 64, locator="opencv-canny-button-v1",
        )
        self.assertEqual(point.label, "fleet-supply-all-button")
        with self.assertRaisesRegex(ValueError, "geometry or locator"):
            VisualTargetPoint(
                x=1108.0, y=619.0, label="fleet-supply-all-button",
                bounds=(1062, 604, 92, 30), region=(1050, 590, 145, 60),
                source_frame_sha256="d" * 64, locator="opencv-canny-square-v1",
            )

    def test_visual_target_point_accepts_bounded_supply_fleet1_tab(self):
        from kancollebot.adapters.poi.cdp_input import VisualTargetPoint

        point = VisualTargetPoint(
            x=220.0, y=179.5, label="supply-fleet1-tab",
            bounds=(203, 164, 34, 31), region=(195, 155, 50, 50),
            source_frame_sha256="e" * 64,
            locator="opencv-canny-tab-v1",
        )
        self.assertEqual(point.label, "supply-fleet1-tab")

    def test_visual_target_point_accepts_bounded_supply_fleet_tab(self):
        from kancollebot.adapters.poi.cdp_input import VisualTargetPoint

        point = VisualTargetPoint(
            x=310.0, y=179.5, label="supply-fleet3-tab",
            bounds=(293, 164, 34, 31), region=(285, 155, 50, 50),
            source_frame_sha256="b" * 64,
            locator="opencv-canny-tab-v1",
        )
        self.assertEqual(point.label, "supply-fleet3-tab")

    def test_visual_target_point_rejects_bounds_outside_region(self):
        from kancollebot.adapters.poi.cdp_input import VisualTargetPoint
        with self.assertRaisesRegex(ValueError, "approved"):
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
                with self.assertRaisesRegex(ValueError, "approved"):
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

    def test_screenshot_defaults_to_the_uncomposited_surface(self):
        # Every calibrated fingerprint except the return notice was recorded
        # with fromSurface=True; the default must not drift away from that.
        transport = FakeTransport()
        CdpInput(transport).capture_screenshot()
        self.assertIs(transport.calls[-1][1]["fromSurface"], True)

    def test_screenshot_can_ask_for_the_composited_surface(self):
        # fromSurface=True drops the Home Port return notice bubble entirely
        # (2026-11-05: notice 0.8332 vs 0.9994 on the same frame), so the
        # settlement chain has to request the composited surface by name.
        transport = FakeTransport()
        CdpInput(transport).capture_screenshot(from_surface=False)
        self.assertIs(transport.calls[-1][1]["fromSurface"], False)

    def test_screenshot_rejects_a_non_bool_surface_choice(self):
        transport = FakeTransport()
        with self.assertRaisesRegex(CdpInputError, "from_surface"):
            CdpInput(transport).capture_screenshot(from_surface="yes")
        self.assertEqual(transport.calls, [])

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


class HomePortTargetTests(unittest.TestCase):
    def test_default_target_is_live_verified_top_left_corner(self):
        saved = os.environ.pop("KANCOLLEBOT_HOME_PORT_POINT", None)
        try:
            point = home_port_point()
        finally:
            if saved is not None:
                os.environ["KANCOLLEBOT_HOME_PORT_POINT"] = saved
        self.assertEqual((point.x, point.y, point.label), (50.0, 50.0, "home-port"))

    def test_env_override_sets_explicit_pixel(self):
        saved = os.environ.get("KANCOLLEBOT_HOME_PORT_POINT")
        os.environ["KANCOLLEBOT_HOME_PORT_POINT"] = "70,80"
        try:
            point = home_port_point()
        finally:
            if saved is None:
                os.environ.pop("KANCOLLEBOT_HOME_PORT_POINT", None)
            else:
                os.environ["KANCOLLEBOT_HOME_PORT_POINT"] = saved
        self.assertEqual((point.x, point.y, point.label), (70.0, 80.0, "home-port"))

    def test_env_override_rejects_bad_format(self):
        saved = os.environ.get("KANCOLLEBOT_HOME_PORT_POINT")
        os.environ["KANCOLLEBOT_HOME_PORT_POINT"] = "not-a-point"
        try:
            with self.assertRaises(ValueError):
                home_port_point()
        finally:
            if saved is None:
                os.environ.pop("KANCOLLEBOT_HOME_PORT_POINT", None)
            else:
                os.environ["KANCOLLEBOT_HOME_PORT_POINT"] = saved

    def test_candidates_begin_with_primary_and_stay_in_large_button(self):
        primary = RecordedPoint(50.0, 50.0, "home-port")
        candidates = home_port_candidates(primary)
        self.assertEqual(candidates[0], primary)
        for candidate in candidates:
            self.assertEqual(candidate.label, "home-port")
            self.assertGreaterEqual(candidate.x, 0)
            self.assertGreaterEqual(candidate.y, 0)
        self.assertGreater(len(candidates), 1)


if __name__ == "__main__":
    unittest.main()
