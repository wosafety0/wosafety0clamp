"""Tests for the offline expedition catalogue (the account-independent ceiling)."""
import unittest

from kancollebot.calibration import (
    AREA_KANTO,
    AREA_NANSEI_ISLANDS,
    AREA_NORTHERN,
    AREA_SOUTHERN,
    AREA_SOUTHWEST,
    AREA_WESTERN,
    DEFAULT_CATALOG,
    DEFAULT_TAB_AREAS,
    CatalogEntry,
    ExpeditionCatalog,
    ExpeditionCatalogError,
)


class DefaultCatalogTests(unittest.TestCase):
    def test_catalogue_covers_every_area_the_wiki_lists(self) -> None:
        self.assertEqual(len(DEFAULT_CATALOG), 63)
        self.assertEqual(
            DEFAULT_CATALOG.areas(),
            (
                AREA_KANTO,
                AREA_NANSEI_ISLANDS,
                AREA_NORTHERN,
                AREA_WESTERN,
                AREA_SOUTHERN,
                AREA_SOUTHWEST,
            ),
        )
        self.assertEqual(len(DEFAULT_CATALOG.numbers_in(AREA_KANTO)), 14)
        self.assertEqual(len(DEFAULT_CATALOG.numbers_in(AREA_NANSEI_ISLANDS)), 14)
        self.assertEqual(len(DEFAULT_CATALOG.numbers_in(AREA_NORTHERN)), 8)
        self.assertEqual(len(DEFAULT_CATALOG.numbers_in(AREA_WESTERN)), 11)
        self.assertEqual(len(DEFAULT_CATALOG.numbers_in(AREA_SOUTHERN)), 10)
        self.assertEqual(len(DEFAULT_CATALOG.numbers_in(AREA_SOUTHWEST)), 6)

    def test_every_entry_has_a_unique_number_and_a_name(self) -> None:
        seen: set[str] = set()
        for entry in DEFAULT_CATALOG.entries:
            self.assertTrue(entry.number)
            self.assertTrue(entry.name)
            self.assertTrue(entry.area)
            self.assertNotIn(entry.number, seen)
            seen.add(entry.number)

    def test_numbers_are_sorted_within_an_area(self) -> None:
        self.assertEqual(
            DEFAULT_CATALOG.numbers_in(AREA_KANTO),
            ("01", "02", "03", "04", "05", "06", "07", "08",
             "A1", "A2", "A3", "A4", "A5", "A6"),
        )
        # 41..46 live in 南西海域, not at the end of 西方海域.
        self.assertEqual(DEFAULT_CATALOG.area_of("41"), AREA_SOUTHWEST)
        self.assertEqual(DEFAULT_CATALOG.area_of("32"), AREA_WESTERN)

    def test_area_and_name_lookup(self) -> None:
        self.assertEqual(DEFAULT_CATALOG.name_of("01"), "練習航海")
        self.assertEqual(DEFAULT_CATALOG.name_of("a5"), "小笠原沖哨戒線")
        self.assertEqual(DEFAULT_CATALOG.area_of("B1"), AREA_NANSEI_ISLANDS)
        self.assertIsNone(DEFAULT_CATALOG.name_of("99"))
        self.assertIsNone(DEFAULT_CATALOG.area_of("99"))

    def test_querying_an_unknown_area_is_an_error(self) -> None:
        with self.assertRaises(ExpeditionCatalogError):
            DEFAULT_CATALOG.numbers_in("火星海域")

    def test_source_is_recorded_so_the_data_can_age_visibly(self) -> None:
        self.assertIn("wiki", DEFAULT_CATALOG.source)
        self.assertEqual(DEFAULT_CATALOG.retrieved_on, "2026-11-04")


class TabAreaTests(unittest.TestCase):
    """The screen's tab order was read off a live capture, not inferred."""

    def test_all_seven_tabs_are_named(self) -> None:
        self.assertEqual(len(DEFAULT_TAB_AREAS), 7)
        self.assertTrue(all(DEFAULT_TAB_AREAS))

    def test_the_six_listed_areas_match_the_catalogue(self) -> None:
        self.assertEqual(set(DEFAULT_TAB_AREAS[:6]), set(DEFAULT_CATALOG.areas()))

    def test_the_seventh_tab_is_not_in_the_catalogue(self) -> None:
        self.assertNotIn(DEFAULT_TAB_AREAS[6], DEFAULT_CATALOG.areas())

    def test_the_game_tab_order_differs_from_the_wiki_table_order(self) -> None:
        # 南西海域 is the fourth tab on screen; the wiki table lists it last.
        self.assertEqual(DEFAULT_TAB_AREAS[3], AREA_SOUTHWEST)
        self.assertEqual(list(DEFAULT_CATALOG.areas())[-1], AREA_SOUTHWEST)
        self.assertNotEqual(
            list(DEFAULT_CATALOG.areas()), list(DEFAULT_TAB_AREAS[:6])
        )


class AreaOfNumbersTests(unittest.TestCase):
    def test_homogeneous_numbers_identify_their_area(self) -> None:
        self.assertEqual(
            DEFAULT_CATALOG.area_of_numbers(("09", "16", "B1")), AREA_NANSEI_ISLANDS
        )

    def test_mixed_numbers_are_not_guessed(self) -> None:
        self.assertIsNone(DEFAULT_CATALOG.area_of_numbers(("01", "09")))

    def test_unrecognised_numbers_are_ignored(self) -> None:
        self.assertEqual(DEFAULT_CATALOG.area_of_numbers(("01", "ZZ")), AREA_KANTO)

    def test_nothing_recognised_returns_none(self) -> None:
        self.assertIsNone(DEFAULT_CATALOG.area_of_numbers(("ZZ", "YY")))


class CoverageTests(unittest.TestCase):
    def test_live_unlock_state_is_not_the_whole_area(self) -> None:
        """The 2026-11-04 live walk saw 12 rows; the area defines 14."""
        live = ("01", "02", "03", "04", "05", "06", "07", "08",
                "A1", "A2", "A3", "A4")
        coverage = DEFAULT_CATALOG.coverage(live, area=AREA_KANTO)
        self.assertEqual(len(coverage.observed), 12)
        self.assertEqual(coverage.missing, ("A5", "A6"))
        self.assertEqual(coverage.foreign, ())
        self.assertFalse(coverage.is_complete)

    def test_full_area_is_complete(self) -> None:
        coverage = DEFAULT_CATALOG.coverage(
            DEFAULT_CATALOG.numbers_in(AREA_NORTHERN), area=AREA_NORTHERN
        )
        self.assertTrue(coverage.is_complete)
        self.assertEqual(coverage.missing, ())

    def test_numbers_outside_the_area_are_reported_not_dropped(self) -> None:
        coverage = DEFAULT_CATALOG.coverage(("01", "09"), area=AREA_KANTO)
        self.assertEqual(coverage.foreign, ("09",))
        self.assertIn("09", DEFAULT_CATALOG.numbers_in(AREA_NANSEI_ISLANDS))

    def test_duplicates_collapse_and_order_is_normalised(self) -> None:
        coverage = DEFAULT_CATALOG.coverage(("a1", "01", "01", " A1 "), area=AREA_KANTO)
        self.assertEqual(coverage.observed, ("01", "A1"))
        self.assertEqual(len(coverage.missing), 12)

    def test_comparison_without_an_area_uses_the_whole_catalogue(self) -> None:
        coverage = DEFAULT_CATALOG.coverage(("01", "B1"))
        self.assertIsNone(coverage.area)
        self.assertEqual(len(coverage.missing), 61)

    def test_unknown_number_is_foreign_rather_than_silently_kept(self) -> None:
        coverage = DEFAULT_CATALOG.coverage(("01", "Z9"), area=AREA_KANTO)
        self.assertEqual(coverage.foreign, ("Z9",))

    def test_describe_mentions_the_gap(self) -> None:
        text = DEFAULT_CATALOG.coverage(("01",), area=AREA_KANTO).describe()
        self.assertIn("observed 1", text)
        self.assertIn("missing", text)


class CatalogueValidationTests(unittest.TestCase):
    def test_a_catalogue_needs_at_least_one_entry(self) -> None:
        with self.assertRaises(ExpeditionCatalogError):
            ExpeditionCatalog(())

    def test_duplicate_numbers_are_rejected(self) -> None:
        with self.assertRaisesRegex(ExpeditionCatalogError, "appears twice"):
            ExpeditionCatalog(
                (
                    CatalogEntry("01", "a", AREA_KANTO),
                    CatalogEntry("01", "b", AREA_KANTO),
                )
            )

    def test_an_entry_without_a_name_is_rejected(self) -> None:
        with self.assertRaisesRegex(ExpeditionCatalogError, "has no name"):
            ExpeditionCatalog((CatalogEntry("01", "", AREA_KANTO),))

    def test_an_unparseable_number_is_rejected(self) -> None:
        with self.assertRaises(ExpeditionCatalogError):
            ExpeditionCatalog((CatalogEntry("X99", "a", AREA_KANTO),))

    def test_a_custom_catalogue_can_stand_in_for_the_default(self) -> None:
        small = ExpeditionCatalog((CatalogEntry("01", "テスト", AREA_KANTO),))
        self.assertEqual(len(small), 1)
        self.assertEqual(small.name_of("01"), "テスト")
        self.assertEqual(small.coverage(("01",), area=AREA_KANTO).missing, ())


if __name__ == "__main__":
    unittest.main()
