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


if __name__ == "__main__":
    unittest.main()
