"""Offline tests for the in-process passive mission-result witness provider."""
from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kancollebot.expeditions.continuous_dispatch_controller import (
    SqliteDispatchEventLog,
)
from kancollebot.expeditions.mission_result_witness import (
    MISSION_RESULT_EVENT,
    DispatchRecord,
    latest_witness_for_fleet,
    read_witnesses,
)
from kancollebot.expeditions.mission_result_witness_provider import (
    MissionResultWitnessProvider,
    capture_to_fields,
    receipt_is_actionable,
)

UTC = timezone.utc
REQUESTED_AT = "2026-11-06T10:36:50.422000+00:00"
RESPONSE_AT = "2026-11-06T10:36:51.345000+00:00"
SHIP_IDS = [12613, 16733, 16323, 13519]


def make_capture(
    *,
    fleet_id: int = 2,
    quest_name: str = "警備任務",
    clear_result: int = 1,
    receipt_status: str = "complete",
    material: list[int] | None = None,
    with_receipt: bool = True,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "apiDeckId": fleet_id,
        "apiQuestName": quest_name,
        "apiClearResult": clear_result,
        "apiShipId": [-1, *SHIP_IDS],  # game sentinel must be filtered
    }
    if with_receipt:
        data["apiRewardReceipt"] = {
            "status": receipt_status,
            "material": [30, 30, 40, 0] if material is None else material,
            "useitemFlags": [0, 0],
            "items": [],
        }
    return {
        "source": {
            "kind": "network_response",
            "path": "/kcsapi/api_req_mission/result",
            "host": "127.0.0.1",
            "httpStatus": 200,
            "requestedAt": REQUESTED_AT,
            "responseReceivedAt": RESPONSE_AT,
        },
        "data": data,
    }


class FakeObserver:
    def __init__(self, capture: dict[str, Any] | None, *, raises: bool = False) -> None:
        self.capture = capture
        self.raises = raises
        self.last_status = "not_started"
        self.last_detail: Any = None
        self.calls = 0

    def capture_cancellable(
        self, cancel_event: threading.Event
    ) -> dict[str, Any] | None:
        self.calls += 1
        if self.raises:
            raise RuntimeError("observer exploded")
        if self.capture is None:
            self.last_status = "natural_response_timeout"
        else:
            self.last_status = "captured"
        return self.capture


class CaptureTranslationTests(unittest.TestCase):
    def test_camel_case_receipt_translates_to_durable_fields(self) -> None:
        fields = capture_to_fields(make_capture())
        self.assertEqual(fields["fleet_id"], 2)
        self.assertEqual(fields["quest_name"], "警備任務")
        self.assertEqual(fields["clear_result"], 1)
        self.assertEqual(fields["requested_at"], REQUESTED_AT)
        receipt = fields["reward_receipt"]
        assert isinstance(receipt, dict)
        self.assertEqual(receipt["material"], [30, 30, 40, 0])
        self.assertEqual(receipt["useitem_flags"], [0, 0])
        self.assertEqual(receipt["items"], [])

    def test_capture_without_data_or_source_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            capture_to_fields({"data": {}})
        with self.assertRaises(ValueError):
            capture_to_fields({"source": {}})

    def test_parse_witness_filters_sentinel_and_keeps_receipt(self) -> None:
        from kancollebot.expeditions.mission_result_witness import parse_witness

        witness = parse_witness(capture_to_fields(make_capture()))
        self.assertEqual(witness.ship_ids, tuple(SHIP_IDS))
        self.assertEqual(witness.result_kind, "success")
        self.assertIsNotNone(witness.reward_receipt)
        assert witness.reward_receipt is not None
        self.assertEqual(witness.reward_receipt.status, "complete")


class ReceiptActionableTests(unittest.TestCase):
    def test_complete_receipt_with_known_result_is_actionable(self) -> None:
        from kancollebot.expeditions.mission_result_witness import parse_witness

        self.assertTrue(receipt_is_actionable(parse_witness(capture_to_fields(make_capture()))))
        self.assertTrue(
            receipt_is_actionable(parse_witness(capture_to_fields(make_capture(clear_result=2))))
        )

    def test_missing_incomplete_or_unknown_result_is_not_actionable(self) -> None:
        from kancollebot.expeditions.mission_result_witness import parse_witness

        cases = [
            make_capture(with_receipt=False),
            make_capture(receipt_status="incomplete"),
            make_capture(receipt_status="no_reward"),
            make_capture(clear_result=99),
            make_capture(material=[30, 30]),
        ]
        for capture in cases:
            with self.subTest(capture=capture):
                witness = parse_witness(capture_to_fields(capture))
                self.assertFalse(receipt_is_actionable(witness))
        self.assertFalse(receipt_is_actionable(None))


class ProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / "events.sqlite3")
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.sink_events: list[tuple[str, dict[str, Any]]] = []
        self.clock = lambda: datetime(2026, 11, 6, 10, 40, 0, tzinfo=UTC)

    def _log(self, event: str, fields: Any) -> None:
        self.events.append((event, dict(fields)))
        SqliteDispatchEventLog(self.db)(event, fields)

    def _sink(self, event: str, fields: Any) -> None:
        self.sink_events.append((event, dict(fields)))

    def _provider(self, capture: dict[str, Any] | None, *, raises: bool = False):
        observer = FakeObserver(capture, raises=raises)
        provider = MissionResultWitnessProvider(
            observer=observer,
            event_log=self._log,
            event_sink=self._sink,
            clock=self.clock,
        )
        return observer, provider

    def _arm_and_wait(self, provider: MissionResultWitnessProvider) -> None:
        provider.arm()
        self.assertTrue(provider.wait(timeout_seconds=2.0))

    def test_arm_before_capture_records_durable_witness(self) -> None:
        _, provider = self._provider(make_capture())
        self.assertFalse(provider.armed)
        self.assertEqual(provider.status, "not_armed")
        self._arm_and_wait(provider)
        self.assertTrue(provider.armed)
        self.assertEqual(provider.status, "captured")
        self.assertTrue(provider.receipt_actionable)
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0][0], MISSION_RESULT_EVENT)
        self.assertEqual(self.events[0][1]["fleet_id"], 2)
        self.assertEqual(self.events[0][1]["clear_result"], 1)
        # The durable row must be readable by the existing witness API.
        rows = SqliteDispatchEventLog(self.db).load_all()
        witnesses = read_witnesses(rows)
        self.assertEqual(len(witnesses), 1)
        self.assertEqual(witnesses[0].ship_ids, tuple(SHIP_IDS))
        self.assertEqual(latest_witness_for_fleet(rows, 2).fleet_id, 2)
        self.assertIsNone(latest_witness_for_fleet(rows, 3))
        # Diagnostics prove no game request/input was sent.
        self.assertTrue(
            all(f.get("game_api_requests") is False
                for _, f in self.sink_events if "game_api_requests" in f)
        )
        self.assertTrue(
            all(f.get("game_input_sent") is False
                for _, f in self.sink_events if "game_input_sent" in f)
        )

    def test_verdict_allows_matching_fleet_ships_and_quest(self) -> None:
        _, provider = self._provider(make_capture())
        self._arm_and_wait(provider)
        verdict = provider.verdict_for(
            fleet_id=2,
            expected_ship_ids=tuple(SHIP_IDS),
            expected_quest_name="警備任務",
            dispatch_record=DispatchRecord(
                fleet_id=2, mission_id=3, ship_ids=tuple(SHIP_IDS),
                quest_name="警備任務",
                observed_at=datetime(2026, 11, 6, 9, 43, 24, tzinfo=UTC),
            ),
        )
        self.assertTrue(verdict.allowed)
        self.assertEqual(verdict.reason, "witness_agrees_with_dispatch_record")

    def test_verdict_without_dispatch_record_is_refused(self) -> None:
        # The witness alone is only an observation of a fleet; attribution
        # requires the durable dispatch identity too.
        _, provider = self._provider(make_capture())
        self._arm_and_wait(provider)
        verdict = provider.verdict_for(
            fleet_id=2,
            expected_ship_ids=tuple(SHIP_IDS),
            expected_quest_name="警備任務",
        )
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.reason, "witness_without_dispatch_record")

    def test_verdict_refuses_fleet_ship_and_quest_mismatches(self) -> None:
        _, provider = self._provider(make_capture())
        self._arm_and_wait(provider)
        cases = [
            ({"fleet_id": 3}, "witness_fleet_mismatch"),
            ({"fleet_id": 2, "expected_ship_ids": (1, 2, 3, 4)}, "witness_ship_set_mismatch"),
            ({"fleet_id": 2, "expected_quest_name": "練習航海"}, "witness_quest_name_mismatch"),
        ]
        for kwargs, reason in cases:
            with self.subTest(reason=reason):
                verdict = provider.verdict_for(**kwargs)
                self.assertFalse(verdict.allowed)
                self.assertEqual(verdict.reason, reason)

    def test_cross_check_refuses_conflicting_dispatch_record(self) -> None:
        _, provider = self._provider(make_capture())
        self._arm_and_wait(provider)
        verdict = provider.verdict_for(
            fleet_id=2,
            expected_ship_ids=tuple(SHIP_IDS),
            dispatch_record=DispatchRecord(
                fleet_id=2, mission_id=3, ship_ids=(999, 998, 997, 996),
                quest_name="警備任務",
                observed_at=datetime(2026, 11, 6, 9, 43, 24, tzinfo=UTC),
            ),
        )
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.reason, "witness_conflicts_with_dispatch_record")

    def test_cross_check_allows_agreeing_dispatch_record(self) -> None:
        _, provider = self._provider(make_capture())
        self._arm_and_wait(provider)
        verdict = provider.verdict_for(
            fleet_id=2,
            expected_ship_ids=tuple(SHIP_IDS),
            dispatch_record=DispatchRecord(
                fleet_id=2, mission_id=3, ship_ids=tuple(SHIP_IDS),
                quest_name="警備任務",
                observed_at=datetime(2026, 11, 6, 9, 43, 24, tzinfo=UTC),
            ),
        )
        self.assertTrue(verdict.allowed)
        self.assertEqual(verdict.reason, "witness_agrees_with_dispatch_record")

    def test_no_capture_fails_closed(self) -> None:
        _, provider = self._provider(None)
        self._arm_and_wait(provider)
        self.assertEqual(provider.status, "no_capture")
        self.assertIsNone(provider.witness)
        self.assertFalse(provider.receipt_actionable)
        self.assertEqual(self.events, [])
        verdict = provider.verdict_for(fleet_id=2)
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.reason, "witness_not_yet_observed")

    def test_observer_exception_fails_closed(self) -> None:
        _, provider = self._provider(make_capture(), raises=True)
        self._arm_and_wait(provider)
        self.assertEqual(provider.status, "observer_error")
        self.assertFalse(provider.receipt_actionable)
        self.assertEqual(self.events, [])

    def test_record_failure_fails_closed_and_never_claims_witness(self) -> None:
        observer = FakeObserver(make_capture())

        def broken_log(event: str, fields: Any) -> None:
            raise OSError("disk full")

        provider = MissionResultWitnessProvider(
            observer=observer, event_log=broken_log, event_sink=self._sink, clock=self.clock
        )
        self._arm_and_wait(provider)
        self.assertEqual(provider.status, "record_failed")
        self.assertIsNone(provider.witness)
        self.assertFalse(provider.receipt_actionable)
        self.assertFalse(provider.verdict_for(fleet_id=2).allowed)

    def test_unknown_result_or_incomplete_receipt_is_not_reward_actionable(self) -> None:
        for capture in (make_capture(clear_result=99), make_capture(receipt_status="incomplete")):
            with self.subTest(capture=capture["data"].get("apiClearResult")):
                _, provider = self._provider(capture)
                self._arm_and_wait(provider)
                self.assertEqual(provider.status, "captured")
                self.assertIsNotNone(provider.witness)
                self.assertFalse(provider.receipt_actionable)

    def test_arm_is_idempotently_refused_a_second_time(self) -> None:
        observer, provider = self._provider(make_capture())
        self._arm_and_wait(provider)
        with self.assertRaises(RuntimeError):
            provider.arm()
        self.assertEqual(observer.calls, 1)

    def test_verdict_before_arm_is_a_refusal(self) -> None:
        _, provider = self._provider(make_capture())
        verdict = provider.verdict_for(fleet_id=2)
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.reason, "witness_not_yet_observed")

    def test_cancel_sets_the_cancellation_signal_without_claiming_a_capture(self) -> None:
        observer = FakeObserver(None)
        provider = MissionResultWitnessProvider(
            observer=observer, event_log=self._log, clock=self.clock
        )
        provider.cancel()
        self.assertTrue(provider._cancel.is_set())
        self.assertFalse(provider.armed)
        self.assertEqual(self.events, [])

    def test_event_sink_failure_does_not_turn_into_a_capture(self) -> None:
        def broken_sink(event: str, fields: Any) -> None:
            raise RuntimeError("sink failed")

        observer = FakeObserver(make_capture())
        provider = MissionResultWitnessProvider(
            observer=observer,
            event_log=self._log,
            event_sink=broken_sink,
            clock=self.clock,
        )
        self._arm_and_wait(provider)
        self.assertEqual(provider.status, "captured")
        self.assertEqual(len(self.events), 1)


if __name__ == "__main__":
    unittest.main()
