"""P1: one-frame all-state probes, with the two-frame click gate left intact.

The probe refactor removes the N-states x M-samples screenshot loop from the
global settlement drain. These tests prove the three things that must not
change while doing so:

* one screenshot is scored against EVERY candidate state, Top 1 / Top 2 are
  ranked, and 0.90 / 0.02 are the decision thresholds;
* an UNKNOWN (or a broken probe) never authorises a fixed-coordinate click;
* the click path still goes through ``_confirm`` -> the injected verifier that
  requires two consecutive independently captured frames.

They also lock in the MISSION_SELECTED recalibration and the Fleet 4 supply
tab template using synthetic frames rebuilt from the checked-in descriptors,
so the suite never depends on untracked live screenshots.
"""
from __future__ import annotations

import base64
import inspect
import json
import struct
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from kancollebot.adapters.poi import visual_fingerprint as vf
from kancollebot.adapters.poi.continuous_expedition_ui import (
    CdpContinuousExpeditionUiDriver,
    ContinuousUiDriverError,
)
from kancollebot.adapters.poi.visual_fingerprint import (
    FingerprintRanking,
    classify_screenshot_fingerprints,
    rank_screenshot_fingerprints,
)
from kancollebot.expeditions.continuous_dispatch_controller import UiStateEvidence
from kancollebot.expeditions.settlement_drain import DrainPageState
from kancollebot.expeditions.settlement_drain_ui import (
    PoiSettlementDrainDriver,
    PoiSettlementDrainObserver,
)

ROOT = Path(__file__).resolve().parents[1]
DOCUMENT_PATH = (
    ROOT / "src" / "kancollebot" / "adapters" / "poi"
    / "ooi_1200x720_fingerprints.json"
)


# -- tiny PNG helpers (same in-memory format the production decoder reads) ----

def _chunk(kind: bytes, data: bytes) -> bytes:
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def _png_rgb(width: int, height: int, rows: list[bytes]) -> str:
    raw = b"".join(b"\x00" + row for row in rows)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    payload = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(raw))
        + _chunk(b"IEND", b"")
    )
    return base64.b64encode(payload).decode("ascii")


def _one_pixel(rgb: tuple[int, int, int]) -> str:
    return _png_rgb(1, 1, [bytes(rgb)])


def _document(**states: dict[str, object]) -> dict[str, object]:
    return {"version": 1, "viewport": [1, 1], "states": states}


def _state(reference: list[int]) -> dict[str, object]:
    return {"crop": [0, 0, 1, 1], "grid": [1, 1], "fingerprint": reference}


def _frame_from_descriptor(document: dict[str, object], state: str) -> str:
    """Rebuild a 1200x720 frame whose given crop is exactly one descriptor.

    Lets the checked-in live templates be exercised without shipping live
    screenshots into the repository.
    """
    template = document["states"][state]
    x, y, crop_w, crop_h = template["crop"]
    columns, grid_rows = template["grid"]
    descriptor = template["fingerprint"]
    width, height = document["viewport"]
    rows = [bytearray(bytes(3) * width) for _ in range(height)]
    index = 0
    for gy in range(grid_rows):
        y0 = y + (gy * crop_h) // grid_rows
        y1 = y + ((gy + 1) * crop_h) // grid_rows
        for gx in range(columns):
            x0 = x + (gx * crop_w) // columns
            x1 = x + ((gx + 1) * crop_w) // columns
            segment = bytes(descriptor[index:index + 3]) * (x1 - x0)
            index += 3
            for py in range(y0, y1):
                rows[py][x0 * 3:x1 * 3] = segment
    return _png_rgb(width, height, [bytes(row) for row in rows])


def _live_document() -> dict[str, object]:
    return json.loads(DOCUMENT_PATH.read_text(encoding="utf-8"))


# -- minimal driver fakes ----------------------------------------------------

class _Input:
    def __init__(self) -> None:
        self.clicks: list[object] = []

    def click(self, point) -> None:
        self.clicks.append(point)


class _Details:
    def reference(self, expedition_id):
        return object()

    def require(self, expedition_id, screenshot_base64):
        return object()


class _Marks:
    def fleets(self):
        return (2, 3, 4)

    def require_decision(self, screenshot_base64):
        return object()

    def require_fleet(self, fleet_id, screenshot_base64):
        return object()


class _ProbeDriver:
    """Fake used by the observer tests: counts every probe path it receives."""

    def __init__(self, state: str | None) -> None:
        self.state = state
        self.single_calls = 0
        self.legacy_page_calls = 0
        self.legacy_home_calls = 0

    def probe_drain_state(self):
        self.single_calls += 1
        return self.state

    def probe_settlement_page(self):
        self.legacy_page_calls += 1
        return None

    def probe_home_port(self):
        self.legacy_home_calls += 1
        return False


def _build_driver(
    *,
    default_state: str,
    composited_state: str = "UNKNOWN",
    classify_error: BaseException | None = None,
    with_probe: bool = True,
):
    captures: list[str] = []
    classifications: list[str] = []
    events: list[tuple[str, dict[str, object]]] = []
    verify_calls: list[tuple[str, int]] = []

    def capture() -> str:
        captures.append("default")
        return "frame-default"

    def capture_composited() -> str:
        captures.append("composited")
        return "frame-composited"

    def classify_frame(payload: str):
        classifications.append(payload)
        if classify_error is not None:
            raise classify_error
        if payload == "frame-composited":
            return composited_state, {"composited": 1.0}
        return default_state, {"default": 1.0}

    def verify_state(expected: str, *, visual_attempts: int = 3) -> UiStateEvidence:
        verify_calls.append((expected, visual_attempts))
        return UiStateEvidence(
            state=expected,
            consecutive_states=(expected, expected),
            evidence="two_consecutive_in_memory_ooi_fingerprints",
        )

    game_input = _Input()
    driver = CdpContinuousExpeditionUiDriver(
        game_input=game_input,
        verify_state=verify_state,
        event_sink=lambda name, payload: events.append((name, dict(payload))),
        details=_Details(),
        marks=_Marks(),
        capture=capture,
        classify_frame=classify_frame if with_probe else None,
        capture_composited=capture_composited if with_probe else None,
        sleep=lambda _seconds: None,
        settle_seconds=0.0,
    )
    return driver, game_input, captures, classifications, events, verify_calls


class FingerprintRankingTests(unittest.TestCase):
    def test_default_thresholds_are_0_90_and_0_02(self) -> None:
        signature = inspect.signature(rank_screenshot_fingerprints)
        self.assertEqual(signature.parameters["minimum_score"].default, 0.90)
        self.assertEqual(signature.parameters["minimum_margin"].default, 0.02)
        wrapper = inspect.signature(classify_screenshot_fingerprints)
        self.assertEqual(wrapper.parameters["minimum_score"].default, 0.90)
        self.assertEqual(wrapper.parameters["minimum_margin"].default, 0.02)

    def test_top1_top2_are_exposed_and_a_clear_winner_is_accepted(self) -> None:
        document = _document(A=_state([100] * 3), B=_state([115] * 3))
        ranking = rank_screenshot_fingerprints(_one_pixel((100, 100, 100)), document)
        self.assertIsInstance(ranking, FingerprintRanking)
        self.assertTrue(ranking.accepted)
        self.assertEqual(ranking.state, "A")
        self.assertEqual(ranking.best_name, "A")
        self.assertEqual(ranking.second_name, "B")
        self.assertAlmostEqual(ranking.best_score, 1.0, places=4)
        self.assertAlmostEqual(ranking.second_score, 1 - 15 / 255, places=4)
        self.assertGreaterEqual(ranking.best_score - ranking.second_score, 0.04)

    def test_margin_below_0_02_is_unknown(self) -> None:
        document = _document(A=_state([100] * 3), B=_state([103] * 3))
        ranking = rank_screenshot_fingerprints(_one_pixel((100, 100, 100)), document)
        self.assertLess(ranking.best_score - ranking.second_score, 0.02)
        self.assertFalse(ranking.accepted)
        self.assertEqual(ranking.state, "UNKNOWN")

    def test_best_score_below_0_90_is_unknown_even_with_a_weak_runner_up(self) -> None:
        document = _document(A=_state([130] * 3), B=_state([200] * 3))
        pixel = _one_pixel((100, 100, 100))
        ranking = rank_screenshot_fingerprints(pixel, document)
        self.assertLess(ranking.best_score, 0.90)
        self.assertGreaterEqual(ranking.best_score - ranking.second_score, 0.04)
        self.assertFalse(ranking.accepted)
        self.assertEqual(ranking.state, "UNKNOWN")

    def test_margin_0_02_is_accepted_but_0_01_is_unknown(self) -> None:
        document = _document(A=_state([100] * 3), B=_state([110] * 3))
        pixel = _one_pixel((100, 100, 100))

        with patch.object(vf, "fingerprint_similarity", side_effect=[0.91, 0.89]):
            ranking = rank_screenshot_fingerprints(pixel, document)
        self.assertTrue(ranking.accepted)
        self.assertEqual(ranking.state, "A")

        with patch.object(vf, "fingerprint_similarity", side_effect=[0.91, 0.90]):
            ranking = rank_screenshot_fingerprints(pixel, document)
        self.assertFalse(ranking.accepted)
        self.assertEqual(ranking.state, "UNKNOWN")

    def test_empty_document_is_unknown(self) -> None:
        ranking = rank_screenshot_fingerprints(_one_pixel((0, 0, 0)), _document())
        self.assertEqual(ranking.state, "UNKNOWN")
        self.assertFalse(ranking.accepted)
        self.assertEqual(ranking.scores, {})

    def test_wrapper_delegates_to_the_same_decision(self) -> None:
        document = _document(A=_state([100] * 3), B=_state([115] * 3))
        pixel = _one_pixel((100, 100, 100))
        ranking = rank_screenshot_fingerprints(pixel, document)
        state, scores = classify_screenshot_fingerprints(pixel, document)
        self.assertEqual(state, ranking.state)
        self.assertEqual(scores, ranking.scores)

    def test_canonical_variant_merges_into_top1_and_is_suppressed(self) -> None:
        document = _document(
            MISSION_SELECTED=_state([100] * 3),
            MISSION_SELECTED_M1=_state([130] * 3),
            SUPPLY_MENU=_state([200] * 3),
        )
        pixel = _one_pixel((130, 130, 130))
        with patch.object(
            vf, "CANONICAL_STATE_VARIANTS",
            {"MISSION_SELECTED": ("MISSION_SELECTED_M1",)},
        ):
            ranking = rank_screenshot_fingerprints(pixel, document)
        self.assertEqual(ranking.state, "MISSION_SELECTED")
        self.assertAlmostEqual(ranking.best_score, 1.0, places=4)
        self.assertEqual(ranking.second_name, "SUPPLY_MENU")
        self.assertEqual(ranking.scores["MISSION_SELECTED"], 1.0)


class SingleFrameProbeTests(unittest.TestCase):
    def test_one_round_uses_exactly_one_screenshot_and_no_verifier_calls(self) -> None:
        driver, _input, captures, classifications, events, verify_calls = _build_driver(
            default_state="RETURN_RESULT"
        )
        self.assertEqual(driver.probe_drain_state(), "RETURN_RESULT")
        self.assertEqual(captures, ["default"])
        self.assertEqual(classifications, ["frame-default"])
        self.assertEqual(verify_calls, [])
        probe_events = [e for name, e in events if name == "continuous_ui_single_frame_probe"]
        self.assertEqual(len(probe_events), 1)
        self.assertEqual(probe_events[0]["screenshots"], 1)
        self.assertFalse(probe_events[0]["authorizes_click"])
        self.assertEqual(probe_events[0]["clicks_sent"], 0)

    def test_composited_frame_is_taken_only_when_default_could_hide_a_notice(self) -> None:
        driver, _input, captures, _cls, events, _v = _build_driver(
            default_state="HOME_PORT", composited_state="HOME_PORT_RETURN_NOTICE"
        )
        self.assertEqual(driver.probe_drain_state(), "HOME_PORT_RETURN_NOTICE")
        self.assertEqual(captures, ["default", "composited"])
        layers = [e["layer"] for name, e in events if name == "continuous_ui_single_frame_probe"]
        self.assertEqual(layers, ["default", "composited"])

    def test_unknown_default_also_checks_the_composited_layer(self) -> None:
        driver, _input, captures, _cls, _events, _v = _build_driver(
            default_state="UNKNOWN", composited_state="UNKNOWN"
        )
        self.assertEqual(driver.probe_drain_state(), "UNKNOWN")
        self.assertEqual(captures, ["default", "composited"])

    def test_broken_probe_fails_closed(self) -> None:
        driver, _input, _captures, _cls, events, _v = _build_driver(
            default_state="RETURN_RESULT", classify_error=RuntimeError("decoder broke")
        )
        self.assertEqual(driver.probe_drain_state(), "UNKNOWN")
        failures = [e for name, e in events if name == "continuous_ui_single_frame_probe_failed"]
        self.assertEqual(len(failures), 1)
        self.assertFalse(failures[0]["authorizes_click"])
        self.assertIn("decoder broke", failures[0]["reason"])

    def test_legacy_path_is_preserved_when_capability_is_not_injected(self) -> None:
        driver, _input, captures, _cls, _events, verify_calls = _build_driver(
            default_state="RETURN_RESULT", with_probe=False
        )
        self.assertIsNone(driver.probe_drain_state())
        # Three settlement screens, one verifier call each (the verifier itself
        # takes a multi-sample, two-consecutive-frame series per call).
        self.assertEqual(
            driver.probe_settlement_page(), "HOME_PORT_RETURN_NOTICE"
        )
        self.assertEqual(len(verify_calls), 1)
        self.assertEqual(captures, [])

    def test_legacy_probe_costs_nine_screenshot_samples_when_no_page_is_up(self) -> None:
        driver, _input, _captures, _cls, _events, _v = _build_driver(
            default_state="UNKNOWN", with_probe=False
        )
        calls: list[tuple[str, int]] = []

        def refusing(expected: str, *, visual_attempts: int = 3):
            calls.append((expected, visual_attempts))
            raise ContinuousUiDriverError("not this screen")

        driver._verify_state = refusing
        self.assertIsNone(driver.probe_settlement_page())
        # Three candidate screens, three visual samples each = nine captures.
        self.assertEqual(len(calls), 3)
        self.assertEqual(sum(samples for _, samples in calls), 9)

    def test_single_frame_probe_replaces_those_nine_with_one_capture(self) -> None:
        driver, _input, captures, classifications, _events, verify_calls = _build_driver(
            default_state="RETURN_RESULT"
        )
        self.assertEqual(driver.probe_settlement_page(), "RETURN_RESULT")
        self.assertEqual(len(captures), 1)
        self.assertEqual(len(classifications), 1)
        self.assertEqual(verify_calls, [])

    def test_probe_never_advances_a_page_by_itself(self) -> None:
        driver, game_input, _c, _cl, _e, _v = _build_driver(default_state="BONUS_ITEMS")
        driver.probe_drain_state()
        self.assertEqual(game_input.clicks, [])

    def test_observer_uses_one_probe_round_and_maps_states(self) -> None:
        cases = {
            "RETURN_RESULT": DrainPageState.EXPEDITION_RESULT,
            "BONUS_ITEMS": DrainPageState.REWARD_PANEL,
            "HOME_PORT_RETURN_NOTICE": DrainPageState.RETURN_NOTICE,
            "HOME_PORT": DrainPageState.HOME_PORT,
            "UNKNOWN": DrainPageState.UNKNOWN,
            "SUPPLY_MENU": DrainPageState.UNKNOWN,
        }
        for state, expected in cases.items():
            with self.subTest(state=state):
                fake = _ProbeDriver(state)
                self.assertIs(PoiSettlementDrainObserver(fake).observe(), expected)
                self.assertEqual(fake.single_calls, 1)
                self.assertEqual(fake.legacy_page_calls, 0)
                self.assertEqual(fake.legacy_home_calls, 0)

    def test_observer_falls_back_when_single_frame_probe_is_unavailable(self) -> None:
        fake = _ProbeDriver(None)
        self.assertIs(PoiSettlementDrainObserver(fake).observe(), DrainPageState.UNKNOWN)
        self.assertEqual(fake.legacy_page_calls, 1)
        self.assertEqual(fake.legacy_home_calls, 1)


class ClickGatePreservationTests(unittest.TestCase):
    def test_advance_still_requires_the_two_consecutive_frame_verifier(self) -> None:
        driver, game_input, _captures, _cls, events, verify_calls = _build_driver(
            default_state="BONUS_ITEMS"
        )
        driver.advance_settlement_page("BONUS_ITEMS")
        self.assertEqual([expected for expected, _ in verify_calls], ["BONUS_ITEMS"])
        self.assertGreaterEqual(verify_calls[0][1], 2)
        confirmed = [
            e for name, e in events if name == "continuous_ui_state_confirmed"
        ]
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(confirmed[0]["consecutive_states"], ("BONUS_ITEMS", "BONUS_ITEMS"))
        self.assertEqual(len(game_input.clicks), 1)

    def test_a_single_frame_verdict_alone_does_not_authorise_the_click(self) -> None:
        driver, game_input, _captures, _cls, _events, verify_calls = _build_driver(
            default_state="BONUS_ITEMS"
        )
        # Probe says BONUS_ITEMS, but the two-frame verifier disagrees.
        driver._verify_state = lambda expected, *, visual_attempts=3: UiStateEvidence(
            state=expected,
            consecutive_states=(expected, "HOME_PORT"),
            evidence="two_consecutive_in_memory_ooi_fingerprints",
        )
        with self.assertRaisesRegex(ContinuousUiDriverError, "two consecutive"):
            driver.advance_settlement_page("BONUS_ITEMS")
        self.assertEqual(game_input.clicks, [])

    def test_unknown_page_is_not_advanceable(self) -> None:
        driver, game_input, _c, _cl, _e, _v = _build_driver(default_state="UNKNOWN")
        with self.assertRaises(ContinuousUiDriverError):
            driver.advance_settlement_page("HOME_PORT")
        self.assertEqual(game_input.clicks, [])
        bridge_driver = _ProbeDriver("UNKNOWN")
        with self.assertRaises(ValueError):
            PoiSettlementDrainDriver(bridge_driver).advance(DrainPageState.UNKNOWN)


class TemplateCalibrationTests(unittest.TestCase):
    def test_mission_selected_variant_has_identical_crop_and_grid(self) -> None:
        document = _live_document()
        canonical = document["states"]["MISSION_SELECTED"]
        variant = document["states"]["MISSION_SELECTED_M1"]
        self.assertEqual(variant["crop"], canonical["crop"])
        self.assertEqual(variant["grid"], canonical["grid"])
        self.assertEqual(len(variant["fingerprint"]), len(canonical["fingerprint"]))
        self.assertIn("MISSION_SELECTED", vf.CANONICAL_STATE_VARIANTS)
        self.assertIn("MISSION_SELECTED_M1", vf.CANONICAL_STATE_VARIANTS["MISSION_SELECTED"])

    def test_recalibrated_page_passes_the_original_thresholds(self) -> None:
        document = _live_document()
        frame = _frame_from_descriptor(document, "MISSION_SELECTED_M1")
        ranking = rank_screenshot_fingerprints(frame, document)
        self.assertEqual(ranking.state, "MISSION_SELECTED")
        self.assertGreaterEqual(ranking.best_score, 0.97)
        self.assertGreaterEqual(ranking.best_score - ranking.second_score, 0.04)

    def test_wrong_pages_still_do_not_classify_as_mission_selected(self) -> None:
        document = _live_document()
        for state in ("EXPEDITION_LIST", "HOME_PORT", "SUPPLY_MENU"):
            with self.subTest(state=state):
                frame = _frame_from_descriptor(document, state)
                ranking = rank_screenshot_fingerprints(frame, document)
                self.assertNotEqual(ranking.state, "MISSION_SELECTED")

    def test_fleet4_supply_template_is_present_and_discriminates_selected_tabs(self) -> None:
        document = _live_document()
        self.assertIn("SUPPLY_FLEET_4_SELECTED", document["states"])
        cases = {
            "SUPPLY_FLEET_4_SELECTED": "SUPPLY_FLEET_4_SELECTED",
            "SUPPLY_FLEET_1_SELECTED": "SUPPLY_FLEET_1_SELECTED",
            "SUPPLY_FLEET_3_SELECTED": "SUPPLY_FLEET_3_SELECTED",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                frame = _frame_from_descriptor(document, source)
                ranking = rank_screenshot_fingerprints(frame, document)
                self.assertEqual(ranking.state, expected)
                self.assertGreaterEqual(ranking.best_score, 0.97)
                self.assertGreaterEqual(
                    ranking.best_score - ranking.second_score, 0.04
                )

    def test_fleet4_template_rejects_a_non_supply_page(self) -> None:
        document = _live_document()
        frame = _frame_from_descriptor(document, "MISSION_SELECTED")
        ranking = rank_screenshot_fingerprints(frame, document)
        self.assertNotEqual(ranking.state, "SUPPLY_FLEET_4_SELECTED")


if __name__ == "__main__":
    unittest.main()
