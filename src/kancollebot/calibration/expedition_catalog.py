"""The authoritative expedition catalogue: a per-area *ceiling*, not a status.

Every account unlocks expeditions at its own pace, and the selection screen
only paints what that account has unlocked. Locked entries are omitted from the
*middle* of the sequence too, so the row count and the row order on screen say
nothing about how many expeditions exist. Two numbers must therefore never be
conflated:

* the **observed set** - rows a scan actually saw (per account, per moment);
* the **catalogue** - every expedition the area defines, which no single account
  necessarily has.

This module holds only the second one. It is deliberately static data plus pure
lookups: it does not read pixels, does not know what is unlocked, and never
infers a status from the catalogue. Its job is to answer "what would a complete
list of this area look like" so that a caller can *report the difference*
instead of mistaking its own view for the whole.

Data provenance (see ``SOURCE``/``RETRIEVED_ON``): the table below comes from the
community wiki's ``遠征の開放要件`` section, which groups expeditions by the sea
area tabs of the selection screen. It was cross-checked against a live capture
of ``鎮守府海域``: the twelve expeditions that account had unlocked (``01`` ..
``08``, ``A1`` .. ``A4``) are exactly a subset of the fourteen listed here, and
the two extras (``A5``/``A6``) were absent from the screen, which is precisely
the gap this module exists to make visible.

Known limits, deliberately not papered over:

* The wiki page's ``詳細一覧表`` was truncated while reading, so expeditions added
  after that section were not verified. ``A5``/``A6`` are the newest numbers in
  the table and are trusted; anything beyond them is unverified.
* The screen has seven area tabs but the wiki table names six areas. Which
  seventh area the extra tab holds has never been observed, so no mapping from
  tab index to area name is asserted here.
* "Not in the catalogue" cannot distinguish *locked* from *not yet scanned*.
  Both look identical in pixels; only the caller knows which it is looking at.
"""
from __future__ import annotations

from dataclasses import dataclass

from .expedition_list import disp_no_sort_key

SOURCE = "wikiwiki.jp/kancolle / 遠征 (遠征の開放要件)"
RETRIEVED_ON = "2026-11-04"

AREA_KANTO = "鎮守府海域"
AREA_NANSEI_ISLANDS = "南西諸島海域"
AREA_NORTHERN = "北方海域"
AREA_WESTERN = "西方海域"
AREA_SOUTHERN = "南方海域"
AREA_SOUTHWEST = "南西海域"

# (area, numbers, names) - kept as ranges so an area reads like its table row.
_TABLE: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        AREA_KANTO,
        ("01", "02", "03", "04", "05", "06", "07", "08",
         "A1", "A2", "A3", "A4", "A5", "A6"),
        ("練習航海", "長距離練習航海", "警備任務", "対潜警戒任務",
         "海上護衛任務", "防空射撃演習", "観艦式予行", "観艦式",
         "兵站強化任務", "海峡警備行動", "長時間対潜警戒",
         "南西方面連絡線哨戒", "小笠原沖哨戒線", "小笠原沖戦闘哨戒"),
    ),
    (
        AREA_NANSEI_ISLANDS,
        ("09", "10", "11", "12", "13", "14", "15", "16",
         "B1", "B2", "B3", "B4", "B5", "B6"),
        ("タンカー護衛任務", "強行偵察任務", "ボーキサイト輸送任務",
         "資源輸送任務", "鼠輸送作戦", "包囲陸戦隊撤収作戦",
         "囮機動部隊支援作戦", "艦隊決戦援護作戦",
         "南西方面航空偵察作戦", "敵泊地強襲反撃作戦",
         "南西諸島離島哨戒作戦", "南西諸島離島防衛作戦",
         "南西諸島捜索撃滅戦", "精鋭水雷戦隊夜襲"),
    ),
    (
        AREA_NORTHERN,
        ("17", "18", "19", "20", "21", "22", "23", "24"),
        ("敵地偵察作戦", "航空機輸送作戦", "北号作戦", "潜水艦哨戒任務",
         "北方鼠輸送作戦", "艦隊演習", "航空戦艦運用演習", "北方航路海上護衛"),
    ),
    (
        AREA_WESTERN,
        ("25", "26", "27", "28", "29", "30", "31", "32", "D1", "D2", "D3"),
        ("通商破壊作戦", "敵母港空襲作戦", "潜水艦通商破壊作戦",
         "西方海域封鎖作戦", "潜水艦派遣演習", "潜水艦派遣作戦",
         "海外艦との接触", "遠洋練習航海",
         "西方海域偵察作戦", "西方潜水艦作戦", "欧州方面友軍との接触"),
    ),
    (
        AREA_SOUTHERN,
        ("33", "34", "35", "36", "37", "38", "39", "40", "E1", "E2"),
        ("前衛支援任務", "艦隊決戦支援任務", "MO作戦", "水上機基地建設",
         "東京急行", "東京急行（弐）", "遠洋潜水艦作戦", "水上機前線輸送",
         "ラバウル方面艦隊進出", "強行鼠輸送作戦"),
    ),
    (
        AREA_SOUTHWEST,
        ("41", "42", "43", "44", "45", "46"),
        ("ブルネイ泊地沖哨戒", "ミ船団護衛(一号船団)", "ミ船団護衛(二号船団)",
         "航空装備輸送任務", "ボーキサイト船団護衛", "南西海域戦闘哨戒"),
    ),
)


# The master-data id of every expedition, keyed by display number. Read from the
# live client's ``const.$missions`` (``api_id`` next to ``api_disp_no``) on
# 2026-11-04: 63 records, exactly the catalogue's 63 numbers. This is a table on
# purpose - the alternate series start at 100, 110, 131 and 141, so deriving an
# id from a display number would be a guess dressed up as a rule.
#
# Poi reports the *id* (``api_mission[1]``), while the expedition list and the
# position index speak display numbers. Without this table a dispatch request
# naming a mission id cannot be turned into a row, which is why "any expedition"
# was impossible: only mission 3's id happened to equal its number.
MISSION_ID_BY_NUMBER: dict[str, int] = {
    "01": 1, "02": 2, "03": 3, "04": 4, "05": 5, "06": 6, "07": 7, "08": 8,
    "09": 9, "10": 10, "11": 11, "12": 12, "13": 13, "14": 14, "15": 15, "16": 16,
    "17": 17, "18": 18, "19": 19, "20": 20, "21": 21, "22": 22, "23": 23, "24": 24,
    "25": 25, "26": 26, "27": 27, "28": 28, "29": 29, "30": 30, "31": 31, "32": 32,
    "33": 33, "34": 34, "35": 35, "36": 36, "37": 37, "38": 38, "39": 39, "40": 40,
    "41": 41, "42": 42, "43": 43, "44": 44, "45": 45, "46": 46,
    "A1": 100, "A2": 101, "A3": 102, "A4": 103, "A5": 104, "A6": 105,
    "B1": 110, "B2": 111, "B3": 112, "B4": 113, "B5": 114, "B6": 115,
    "D1": 131, "D2": 132, "D3": 133,
    "E1": 141, "E2": 142,
}

#: Inverse of :data:`MISSION_ID_BY_NUMBER`; ids are unique in the master data.
NUMBER_BY_MISSION_ID: dict[int, str] = {
    mission_id: number for number, mission_id in MISSION_ID_BY_NUMBER.items()
}


class ExpeditionCatalogError(ValueError):
    """The catalogue data or a query against it is malformed."""


def mission_id_of(number: str) -> int | None:
    """The master-data id of a display number, or ``None`` if unrecorded."""
    if not isinstance(number, str):
        return None
    return MISSION_ID_BY_NUMBER.get(number.strip().upper())


def number_of_mission_id(mission_id: int) -> str | None:
    """The display number of a master-data id, or ``None`` if unrecorded."""
    if isinstance(mission_id, bool) or not isinstance(mission_id, int):
        return None
    return NUMBER_BY_MISSION_ID.get(mission_id)


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    number: str
    name: str
    area: str


@dataclass(frozen=True, slots=True)
class Coverage:
    """How an observed list compares with the catalogue for one area.

    ``missing`` is the whole point: those expeditions exist in the catalogue but
    were not on screen. That is usually a lock the account has not cleared, but
    it can equally be a page the scan never reached - pixels cannot tell the two
    apart, and this type does not pretend otherwise.
    """

    area: str | None
    observed: tuple[str, ...]
    missing: tuple[str, ...]
    foreign: tuple[str, ...]

    @property
    def is_complete(self) -> bool:
        return not self.missing and not self.foreign

    def describe(self) -> str:
        scope = "catalogue" if self.area is None else self.area
        parts = [f"observed {len(self.observed)} of the {scope}"]
        if self.missing:
            parts.append("missing " + ", ".join(self.missing))
        if self.foreign:
            parts.append("not in catalogue " + ", ".join(self.foreign))
        if self.is_complete:
            parts.append("complete")
        return "; ".join(parts)


class ExpeditionCatalog:
    """Immutable lookup table of every expedition, grouped by sea area."""

    def __init__(
        self,
        entries: tuple[CatalogEntry, ...],
        *,
        source: str = SOURCE,
        retrieved_on: str = RETRIEVED_ON,
    ) -> None:
        if not entries:
            raise ExpeditionCatalogError("a catalogue needs at least one entry")
        by_number: dict[str, CatalogEntry] = {}
        for entry in entries:
            if not entry.number:
                raise ExpeditionCatalogError("an entry needs a display number")
            if not entry.name:
                raise ExpeditionCatalogError(f"{entry.number} has no name")
            if not entry.area:
                raise ExpeditionCatalogError(f"{entry.number} has no area")
            try:
                disp_no_sort_key(entry.number)
            except ValueError as error:
                raise ExpeditionCatalogError(str(error)) from error
            existing = by_number.get(entry.number)
            if existing is not None:
                raise ExpeditionCatalogError(
                    f"{entry.number} appears twice ({existing.name} / {entry.name})"
                )
            by_number[entry.number] = entry
        self._entries = entries
        self._by_number = by_number
        self.source = source
        self.retrieved_on = retrieved_on

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> tuple[CatalogEntry, ...]:
        return self._entries

    def areas(self) -> tuple[str, ...]:
        """Areas in table order, which is the on-screen tab order."""
        seen: list[str] = []
        for entry in self._entries:
            if entry.area not in seen:
                seen.append(entry.area)
        return tuple(seen)

    def numbers_in(self, area: str) -> tuple[str, ...]:
        numbers = [entry.number for entry in self._entries if entry.area == area]
        if not numbers:
            raise ExpeditionCatalogError(f"unknown area: {area}")
        return tuple(sorted(numbers, key=disp_no_sort_key))

    def entry_of(self, number: str) -> CatalogEntry | None:
        return self._by_number.get(number.strip().upper())

    def name_of(self, number: str) -> str | None:
        entry = self.entry_of(number)
        return None if entry is None else entry.name

    def area_of(self, number: str) -> str | None:
        entry = self.entry_of(number)
        return None if entry is None else entry.area

    def mission_id_of(self, number: str) -> int | None:
        """The master-data id for a catalogue number this table also knows."""
        if self.entry_of(number) is None:
            return None
        return mission_id_of(number)

    def number_of_mission_id(self, mission_id: int) -> str | None:
        """The catalogue number for a master-data id, if both are known."""
        number = number_of_mission_id(mission_id)
        if number is None or self.entry_of(number) is None:
            return None
        return number

    def area_of_numbers(
        self, numbers: tuple[str, ...] | list[str]
    ) -> str | None:
        """The one area every recognised number belongs to, else ``None``.

        Useful because the tab index cannot be resolved to an area name without
        reading the label: a homogeneous list identifies its own area from the
        numbers alone. Mixed or wholly unrecognised input returns ``None`` rather
        than picking the majority.
        """
        areas = {
            entry.area
            for entry in (self.entry_of(value) for value in numbers)
            if entry is not None
        }
        return next(iter(areas)) if len(areas) == 1 else None

    def coverage(
        self, observed: tuple[str, ...] | list[str], *, area: str | None = None
    ) -> Coverage:
        """Compare what a scan saw with what the catalogue defines.

        Duplicates in ``observed`` are collapsed; order is normalised so the
        result does not depend on scan order. When ``area`` is given the
        comparison is scoped to it and numbers belonging elsewhere land in
        ``foreign`` rather than being silently dropped.
        """
        normalised: list[str] = []
        for value in observed:
            text = value.strip().upper()
            if text and text not in normalised:
                normalised.append(text)
        reference = (
            tuple(self._by_number) if area is None else self.numbers_in(area)
        )
        reference_set = set(reference)
        observed_set = set(normalised)
        return Coverage(
            area=area,
            observed=tuple(sorted(observed_set, key=disp_no_sort_key)),
            missing=tuple(
                sorted(reference_set - observed_set, key=disp_no_sort_key)
            ),
            foreign=tuple(
                sorted(observed_set - reference_set, key=disp_no_sort_key)
            ),
        )


def _build_default() -> ExpeditionCatalog:
    entries: list[CatalogEntry] = []
    for area, numbers, names in _TABLE:
        if len(numbers) != len(names):
            raise ExpeditionCatalogError(
                f"{area} has {len(numbers)} numbers but {len(names)} names"
            )
        entries.extend(
            CatalogEntry(number=number, name=name, area=area)
            for number, name in zip(numbers, names)
        )
    catalogue = ExpeditionCatalog(tuple(entries))
    # The id table is data too, so a typo in either direction must not survive
    # import: a missing id makes that expedition undispatchable, and an extra id
    # would name a number the catalogue does not define.
    known = {entry.number for entry in catalogue.entries}
    if set(MISSION_ID_BY_NUMBER) != known:
        missing = sorted(known - set(MISSION_ID_BY_NUMBER), key=disp_no_sort_key)
        extra = sorted(set(MISSION_ID_BY_NUMBER) - known, key=disp_no_sort_key)
        raise ExpeditionCatalogError(
            f"mission id table does not match the catalogue (missing {missing}, extra {extra})"
        )
    if len(set(MISSION_ID_BY_NUMBER.values())) != len(MISSION_ID_BY_NUMBER):
        raise ExpeditionCatalogError("mission ids must be unique")
    return catalogue


#: The reference ceiling. Never a snapshot of any account's unlocks.
DEFAULT_CATALOG = _build_default()

__all__ = [
    "AREA_KANTO",
    "AREA_NANSEI_ISLANDS",
    "AREA_NORTHERN",
    "AREA_SOUTHERN",
    "AREA_SOUTHWEST",
    "AREA_WESTERN",
    "DEFAULT_CATALOG",
    "MISSION_ID_BY_NUMBER",
    "NUMBER_BY_MISSION_ID",
    "RETRIEVED_ON",
    "SOURCE",
    "CatalogEntry",
    "Coverage",
    "ExpeditionCatalog",
    "ExpeditionCatalogError",
    "mission_id_of",
    "number_of_mission_id",
]
