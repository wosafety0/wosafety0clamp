import unittest

from kancollebot.expeditions.mission_catalog_scan import (
    ExpeditionListPageObservation,
    ExpeditionListScanAccumulator,
    ExpeditionListScanError,
    ExpeditionListScanStatus,
    MissionRowObservation,
)


def row(mission_id, name=None):
    return MissionRowObservation(mission_id, name or f"Mission {mission_id}")


def page(top, rows, *, bottom=False, content=900, viewport=300):
    return ExpeditionListPageObservation(
        scroll_top_px=top,
        viewport_height_px=viewport,
        content_height_px=content,
        bottom_confirmed=bottom,
        rows=tuple(rows),
    )


class MissionRowObservationTests(unittest.TestCase):
    def test_trims_mission_name(self):
        self.assertEqual(row(1, "  Test  ").name, "Test")

    def test_rejects_boolean_or_non_positive_mission_id(self):
        for mission_id in (True, 0, -1):
            with self.subTest(mission_id=mission_id):
                with self.assertRaises(ExpeditionListScanError):
                    row(mission_id)

    def test_rejects_empty_name(self):
        with self.assertRaisesRegex(ExpeditionListScanError, "non-empty"):
            row(1, "  ")


class ExpeditionListPageObservationTests(unittest.TestCase):
    def test_rejects_non_finite_geometry(self):
        with self.assertRaisesRegex(ExpeditionListScanError, "finite"):
            page(float("nan"), [row(1)])

    def test_rejects_empty_page(self):
        with self.assertRaisesRegex(ExpeditionListScanError, "at least one"):
            page(0, [])

    def test_rejects_duplicate_mission_ids_within_page(self):
        with self.assertRaisesRegex(ExpeditionListScanError, "duplicate"):
            page(0, [row(1), row(1)])

    def test_bottom_geometry_is_explicit(self):
        observation = page(600, [row(4)], bottom=True)
        self.assertTrue(observation.geometry_reaches_bottom)


class ExpeditionListScanAccumulatorTests(unittest.TestCase):
    def test_overlapping_pages_are_deduplicated_in_first_seen_order(self):
        scan = ExpeditionListScanAccumulator()
        first = scan.add_page(page(0, [row(1), row(2), row(3)]))
        second = scan.add_page(page(300, [row(3), row(4), row(5)]))
        final = scan.add_page(page(600, [row(5), row(6)], bottom=True))
        self.assertEqual(first.status, ExpeditionListScanStatus.IN_PROGRESS)
        self.assertEqual(second.pages_scanned, 2)
        self.assertTrue(final.complete)
        self.assertEqual([item.mission_id for item in final.missions], [1, 2, 3, 4, 5, 6])
        self.assertEqual(scan.finish(), final)

    def test_first_page_must_start_at_top(self):
        with self.assertRaisesRegex(ExpeditionListScanError, "start at the top"):
            ExpeditionListScanAccumulator().add_page(page(2, [row(1)]))

    def test_scroll_must_advance(self):
        scan = ExpeditionListScanAccumulator()
        scan.add_page(page(0, [row(1)]))
        with self.assertRaisesRegex(ExpeditionListScanError, "did not advance"):
            scan.add_page(page(0, [row(1)]))

    def test_scroll_cannot_move_backwards(self):
        scan = ExpeditionListScanAccumulator()
        scan.add_page(page(0, [row(1)]))
        scan.add_page(page(120, [row(2)]))
        with self.assertRaisesRegex(ExpeditionListScanError, "did not advance"):
            scan.add_page(page(100, [row(2)]))

    def test_scroll_jump_larger_than_viewport_is_rejected(self):
        scan = ExpeditionListScanAccumulator()
        scan.add_page(page(0, [row(1)]))
        with self.assertRaisesRegex(ExpeditionListScanError, "exceeds one viewport"):
            scan.add_page(page(302, [row(5)]))

    def test_viewport_or_content_geometry_cannot_change_mid_scan(self):
        scan = ExpeditionListScanAccumulator()
        scan.add_page(page(0, [row(1)]))
        with self.assertRaisesRegex(ExpeditionListScanError, "viewport height changed"):
            scan.add_page(page(240, [row(2)], viewport=320))

    def test_conflicting_mission_name_across_pages_is_rejected(self):
        scan = ExpeditionListScanAccumulator()
        scan.add_page(page(0, [row(1, "Mission A")]))
        with self.assertRaisesRegex(ExpeditionListScanError, "conflicting names"):
            scan.add_page(page(240, [row(1, "Different Mission")]))

    def test_rejected_page_does_not_partially_add_new_missions(self):
        scan = ExpeditionListScanAccumulator()
        scan.add_page(page(0, [row(1, "Mission A")]))
        with self.assertRaisesRegex(ExpeditionListScanError, "conflicting names"):
            scan.add_page(page(240, [row(2, "Mission B"), row(1, "Different Mission A")]))
        snapshot = scan.snapshot()
        self.assertEqual(snapshot.pages_scanned, 1)
        self.assertEqual([item.mission_id for item in snapshot.missions], [1])

    def test_bottom_flag_without_bottom_geometry_is_rejected(self):
        scan = ExpeditionListScanAccumulator()
        with self.assertRaisesRegex(ExpeditionListScanError, "conflicts"):
            scan.add_page(page(0, [row(1)], bottom=True))

    def test_reaching_geometry_bottom_without_visual_confirmation_is_rejected(self):
        scan = ExpeditionListScanAccumulator()
        scan.add_page(page(0, [row(1)]))
        scan.add_page(page(300, [row(2)]))
        with self.assertRaisesRegex(ExpeditionListScanError, "conflicts"):
            scan.add_page(page(600, [row(4)], bottom=False))

    def test_finish_before_verified_bottom_fails_closed(self):
        scan = ExpeditionListScanAccumulator()
        scan.add_page(page(0, [row(1)]))
        with self.assertRaisesRegex(ExpeditionListScanError, "incomplete"):
            scan.finish()

    def test_no_pages_cannot_finish(self):
        with self.assertRaisesRegex(ExpeditionListScanError, "incomplete"):
            ExpeditionListScanAccumulator().finish()

    def test_no_pages_can_be_added_after_completion(self):
        scan = ExpeditionListScanAccumulator()
        scan.add_page(page(0, [row(1)], bottom=True, content=300))
        with self.assertRaisesRegex(ExpeditionListScanError, "already complete"):
            scan.add_page(page(0, [row(2)], bottom=True, content=300))


if __name__ == "__main__":
    unittest.main()
