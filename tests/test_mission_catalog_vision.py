import base64
import unittest

from kancollebot.expeditions.mission_catalog_scan import MissionRowObservation
from kancollebot.expeditions.mission_catalog_vision import (
    ExpeditionListVisualProfile,
    ExpeditionListVisionError,
    MissionRowDetection,
    MissionRowTemplate,
    ScrollbarThumbDetection,
    build_expedition_list_page,
    recognize_expedition_list_screenshot,
)


def encoded(value=b"png"):
    return base64.b64encode(value).decode("ascii")


def template(mission_id, name=None):
    return MissionRowTemplate(mission_id, name or f"Mission {mission_id}", encoded())


def profile(*, calibrated=True, templates=None):
    return ExpeditionListVisualProfile(
        list_region=(100, 100, 500, 450),
        scrollbar_track_region=(620, 100, 12, 200),
        scrollbar_thumb_template_base64=encoded(b"thumb"),
        mission_templates=tuple(templates or (template(1), template(2), template(3))),
        scrollbar_scale_verified=calibrated,
    )


def detection(mission_id, y, *, x=120, score=0.99, name=None):
    return MissionRowDetection(
        MissionRowObservation(mission_id, name or f"Mission {mission_id}"),
        (x, y, 40, 18),
        score,
    )


def thumb(top, *, height=40, score=0.99, track=(620, 100, 12, 200)):
    return ScrollbarThumbDetection(
        (track[0], top, 12, height),
        track,
        score,
    )


class MissionRowTemplateTests(unittest.TestCase):
    def test_trims_template_name(self):
        self.assertEqual(template(1, "  North Sea  ").name, "North Sea")

    def test_rejects_invalid_mission_ids(self):
        for value in (True, 0, -2):
            with self.subTest(value=value):
                with self.assertRaises(ExpeditionListVisionError):
                    template(value)

    def test_rejects_empty_name(self):
        with self.assertRaisesRegex(ExpeditionListVisionError, "non-empty"):
            template(1, " ")

    def test_rejects_invalid_base64(self):
        with self.assertRaisesRegex(ExpeditionListVisionError, "base64"):
            MissionRowTemplate(1, "Mission 1", "not base64!")


class ExpeditionListVisualProfileTests(unittest.TestCase):
    def test_accepts_bounded_calibrated_profile(self):
        self.assertTrue(profile().scrollbar_scale_verified)

    def test_rejects_duplicate_template_ids(self):
        with self.assertRaisesRegex(ExpeditionListVisionError, "unique"):
            profile(templates=(template(1), template(1, "Duplicate")))

    def test_rejects_list_region_outside_viewport(self):
        invalid = ExpeditionListVisualProfile.__new__(ExpeditionListVisualProfile)
        object.__setattr__(invalid, "list_region", (1000, 100, 500, 450))
        object.__setattr__(invalid, "scrollbar_track_region", (620, 100, 12, 200))
        object.__setattr__(invalid, "scrollbar_thumb_template_base64", encoded())
        object.__setattr__(invalid, "mission_templates", (template(1),))
        object.__setattr__(invalid, "viewport", (1200, 720))
        object.__setattr__(invalid, "minimum_row_score", 0.94)
        object.__setattr__(invalid, "row_ambiguity_margin", 0.06)
        object.__setattr__(invalid, "row_center_tolerance_px", 14.0)
        object.__setattr__(invalid, "minimum_scrollbar_score", 0.95)
        object.__setattr__(invalid, "scrollbar_ambiguity_margin", 0.05)
        object.__setattr__(invalid, "bottom_tolerance_px", 2.0)
        object.__setattr__(invalid, "scrollbar_scale_verified", True)
        with self.assertRaisesRegex(ExpeditionListVisionError, "outside"):
            invalid.__post_init__()

    def test_rejects_non_finite_threshold(self):
        with self.assertRaisesRegex(ExpeditionListVisionError, "finite"):
            ExpeditionListVisualProfile(
                list_region=(100, 100, 500, 450),
                scrollbar_track_region=(620, 100, 12, 200),
                scrollbar_thumb_template_base64=encoded(),
                mission_templates=(template(1),),
                minimum_row_score=float("nan"),
            )


class BuildExpeditionListPageTests(unittest.TestCase):
    def test_requires_explicit_scrollbar_scale_calibration(self):
        with self.assertRaisesRegex(ExpeditionListVisionError, "not been calibrated"):
            build_expedition_list_page(
                profile(calibrated=False),
                (detection(1, 120),),
                thumb(120),
            )

    def test_builds_page_using_normalized_thumb_geometry(self):
        result = build_expedition_list_page(
            profile(),
            (detection(2, 180), detection(1, 130)),
            thumb(130),
        )
        self.assertEqual(result.page.scroll_top_px, 30.0)
        self.assertEqual(result.page.viewport_height_px, 40.0)
        self.assertEqual(result.page.content_height_px, 200.0)
        self.assertAlmostEqual(result.scroll_progress, 30 / 160)
        self.assertFalse(result.page.bottom_confirmed)
        self.assertEqual(
            [item.mission_id for item in result.page.rows],
            [1, 2],
        )

    def test_confirms_bottom_only_when_thumb_reaches_calibrated_track_end(self):
        result = build_expedition_list_page(
            profile(),
            (detection(1, 130),),
            thumb(260),
        )
        self.assertTrue(result.page.bottom_confirmed)
        self.assertEqual(result.scroll_progress, 1.0)

    def test_near_bottom_within_calibrated_tolerance_is_confirmed(self):
        result = build_expedition_list_page(
            profile(),
            (detection(1, 130),),
            thumb(259),
        )
        self.assertTrue(result.page.bottom_confirmed)

    def test_rejects_scrollbar_from_wrong_track(self):
        bad_track = (621, 100, 12, 200)
        with self.assertRaisesRegex(ExpeditionListVisionError, "different track"):
            build_expedition_list_page(
                profile(),
                (detection(1, 130),),
                thumb(130, track=bad_track),
            )

    def test_rejects_low_confidence_scrollbar(self):
        with self.assertRaisesRegex(ExpeditionListVisionError, "below"):
            build_expedition_list_page(
                profile(),
                (detection(1, 130),),
                thumb(130, score=0.80),
            )

    def test_rejects_missing_mission_rows(self):
        with self.assertRaisesRegex(ExpeditionListVisionError, "at least one"):
            build_expedition_list_page(profile(), (), thumb(130))

    def test_rejects_row_outside_list_region(self):
        with self.assertRaisesRegex(ExpeditionListVisionError, "outside"):
            build_expedition_list_page(profile(), (detection(1, 130, x=590),), thumb(130))

    def test_rejects_unconfigured_mission_id(self):
        with self.assertRaisesRegex(ExpeditionListVisionError, "unknown"):
            build_expedition_list_page(
                profile(),
                (detection(9, 130),),
                thumb(130),
            )

    def test_rejects_wrong_trusted_mission_name(self):
        with self.assertRaisesRegex(ExpeditionListVisionError, "trusted"):
            build_expedition_list_page(
                profile(),
                (detection(1, 130, name="Wrong name"),),
                thumb(130),
            )

    def test_rejects_low_confidence_row(self):
        with self.assertRaisesRegex(ExpeditionListVisionError, "below threshold"):
            build_expedition_list_page(
                profile(),
                (detection(1, 130, score=0.75),),
                thumb(130),
            )

    def test_rejects_same_row_with_two_ambiguous_mission_templates(self):
        with self.assertRaisesRegex(ExpeditionListVisionError, "ambiguously"):
            build_expedition_list_page(
                profile(),
                (detection(1, 130, score=0.97), detection(2, 132, score=0.96)),
                thumb(130),
            )

    def test_resolves_same_row_only_when_score_margin_is_sufficient(self):
        result = build_expedition_list_page(
            profile(),
            (detection(1, 130, score=0.99), detection(2, 132, score=0.94)),
            thumb(130),
        )
        self.assertEqual([item.mission_id for item in result.page.rows], [1])

    def test_rejects_duplicate_mission_id_on_separate_rows(self):
        with self.assertRaisesRegex(ExpeditionListVisionError, "same mission template"):
            build_expedition_list_page(
                profile(),
                (detection(1, 130), detection(1, 220)),
                thumb(130),
            )

    def test_rejects_scroll_thumb_outside_track(self):
        with self.assertRaisesRegex(ExpeditionListVisionError, "inside"):
            thumb(80)

    def test_rejects_thumb_width_exceeding_track(self):
        with self.assertRaisesRegex(ExpeditionListVisionError, "inside"):
            ScrollbarThumbDetection((620, 130, 20, 40), (620, 100, 12, 200), 0.99)


class FakeArray:
    def __init__(self, height, width, tag):
        self.shape = (height, width)
        self.size = height * width
        self.tag = tag

    def __getitem__(self, key):
        y_slice, x_slice = key
        y0 = y_slice.start or 0
        y1 = y_slice.stop if y_slice.stop is not None else self.shape[0]
        x0 = x_slice.start or 0
        x1 = x_slice.stop if x_slice.stop is not None else self.shape[1]
        return FakeArray(y1 - y0, x1 - x0, f"{self.tag}@{x0},{y0},{x1 - x0},{y1 - y0}")


class FakeScoreMap:
    def __init__(self, shape, matches):
        self.shape = shape
        self.matches = [list(item) for item in matches]

    def copy(self):
        return FakeScoreMap(self.shape, self.matches)

    def __setitem__(self, key, value):
        y_slice, x_slice = key
        y0, y1 = y_slice.start or 0, y_slice.stop
        x0, x1 = x_slice.start or 0, x_slice.stop
        for item in self.matches:
            _, x, y = item
            if x0 <= x < x1 and y0 <= y < y1:
                item[0] = float(value)


class FakeNumpy:
    uint8 = object()

    @staticmethod
    def frombuffer(raw, dtype):
        return raw


class FakeCv2:
    IMREAD_GRAYSCALE = 0
    TM_CCOEFF_NORMED = 5

    def __init__(self, *, second_thumb_score=-1.0, frame_size=(1200, 720)):
        self.second_thumb_score = second_thumb_score
        self.frame_size = frame_size

    def imdecode(self, raw, flags):
        if raw == b"frame":
            return FakeArray(self.frame_size[1], self.frame_size[0], "frame")
        if raw == b"m1":
            return FakeArray(18, 40, "m1")
        if raw == b"m2":
            return FakeArray(18, 40, "m2")
        if raw == b"thumb":
            return FakeArray(40, 12, "thumb")
        return None

    def matchTemplate(self, image, template_image, method):
        if image.tag == "frame@100,100,500,450" and template_image.tag == "m1":
            return FakeScoreMap((433, 461), [(0.99, 10, 20), (-0.5, 80, 200)])
        if image.tag == "frame@100,100,500,450" and template_image.tag == "m2":
            return FakeScoreMap((433, 461), [(0.99, 20, 100), (-0.5, 80, 200)])
        if image.tag == "frame@620,100,12,200" and template_image.tag == "thumb":
            return FakeScoreMap(
                (161, 1),
                [(0.99, 0, 30), (self.second_thumb_score, 0, 100)],
            )
        raise AssertionError(f"unexpected fake image/template pair: {image.tag}, {template_image.tag}")

    @staticmethod
    def minMaxLoc(score_map):
        ranked = sorted(score_map.matches, key=lambda item: item[0])
        minimum = ranked[0]
        maximum = ranked[-1]
        return minimum[0], maximum[0], (minimum[1], minimum[2]), (maximum[1], maximum[2])


class ScreenshotRecognitionTests(unittest.TestCase):
    def make_profile(self):
        return ExpeditionListVisualProfile(
            list_region=(100, 100, 500, 450),
            scrollbar_track_region=(620, 100, 12, 200),
            scrollbar_thumb_template_base64=encoded(b"thumb"),
            mission_templates=(
                MissionRowTemplate(1, "Mission One", encoded(b"m1")),
                MissionRowTemplate(2, "Mission Two", encoded(b"m2")),
            ),
            scrollbar_scale_verified=True,
        )

    def test_recognizes_rows_and_scrollbar_from_mocked_png_frame(self):
        result = recognize_expedition_list_screenshot(
            encoded(b"frame"),
            self.make_profile(),
            cv2_module=FakeCv2(),
            numpy_module=FakeNumpy(),
        )
        self.assertEqual([row.mission_id for row in result.page.rows], [1, 2])
        self.assertEqual([row.bounds[:2] for row in result.row_matches], [(110, 120), (120, 200)])
        self.assertEqual(result.page.scroll_top_px, 30.0)
        self.assertFalse(result.page.bottom_confirmed)
        self.assertEqual(result.scroll_progress, 30 / 160)

    def test_rejects_wrong_viewport_before_matching(self):
        with self.assertRaisesRegex(ExpeditionListVisionError, "viewport"):
            recognize_expedition_list_screenshot(
                encoded(b"frame"),
                self.make_profile(),
                cv2_module=FakeCv2(frame_size=(1280, 720)),
                numpy_module=FakeNumpy(),
            )

    def test_rejects_ambiguous_scrollbar_thumb_matches(self):
        with self.assertRaisesRegex(ExpeditionListVisionError, "multiple similarly strong"):
            recognize_expedition_list_screenshot(
                encoded(b"frame"),
                self.make_profile(),
                cv2_module=FakeCv2(second_thumb_score=0.98),
                numpy_module=FakeNumpy(),
            )


if __name__ == "__main__":
    unittest.main()
