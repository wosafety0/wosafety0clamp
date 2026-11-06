"""#134: in-process passive reward witness wired into the production settlement.

These are OFFLINE tests. No Poi/CDP connection, no game click, no screenshot and
no new calibration asset is used. They pin the two facts that make the reward
receipt part of the loop rather than an external helper process:

* one observer child captures one natural exchange, so a multi-fleet run keeps
  listening after the first receipt, and a receipt is never borrowed from
  another fleet;
* the real settlement controller consumes the captured witness at close-out,
  bound to the single dispatch instance the ledger attributed. Every failure
  direction stays ``verified=False`` and never rewinds a completed settlement.
"""
from __future__ import annotations

import ast
import json
import sys
import tempfile
import threading
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import one_expedition_ui_loop as entry
from kancollebot.expeditions.continuous_settlement_controller import (
    ContinuousSettlementController,
)
from kancollebot.expeditions.dispatch_registry import SqliteDispatchRegistry
from kancollebot.expeditions.mission_result_witness import (
    MISSION_RESULT_EVENT,
    DispatchRecord,
    WitnessVerdict,
    read_witnesses,
)
from kancollebot.expeditions.mission_result_witness_provider import (
    MissionResultWitnessProvider,
)
from tests.test_settlement_global_drain_barrier import (
    BASE,
    CONFIRMED_AT,
    FLEETS,
    RecordingUi,
    StepClock,
    TwoFleetReader,
    persist,
    prepare_two_returns,
)
from kancollebot.expeditions.continuous_scheduler import (
    ContinuousExpeditionScheduler,
    UiOperationArbiter,
)

UTC = timezone.utc
REQUESTED_AT = (BASE - timedelta(seconds=30)).isoformat()
RESPONSE_AT = (BASE - timedelta(seconds=29)).isoformat()
SHIPS = FLEETS[2]["ships"]
MISSION_ID = FLEETS[2]["mission_id"]


def make_capture(
    *,
    fleet_id: int = 2,
    ship_ids: tuple[int, ...] = SHIPS,
    receipt_status: str = "complete",
    quest_name: str = "警備任務",
    clear_result: int = 1,
) -> dict[str, Any]:
    return {
        "source": {
            "kind": "cdp-network-mission-result",
            "path": "/kcsapi/api_req_mission/result",
            "requestedAt": REQUESTED_AT,
            "responseReceivedAt": RESPONSE_AT,
        },
        "data": {
            "apiDeckId": fleet_id,
            "apiQuestName": quest_name,
            "apiClearResult": clear_result,
            "apiShipId": [-1, *ship_ids],
            "apiRewardReceipt": {
                "status": receipt_status,
                "material": [30, 30, 40, 0],
                "useitemFlags": [0, 0],
                "items": [],
            },
        },
    }


class SequenceObserver:
    """Scripted observer: one capture per child, exactly like the real one."""

    def __init__(self, *captures: dict[str, Any] | None) -> None:
        self.captures = list(captures)
        self.calls = 0
        self.last_status = "not_started"
        self.last_detail: Any = None
        self.cancel_on_next = False

    def capture_cancellable(
        self, cancel_event: threading.Event
    ) -> dict[str, Any] | None:
        self.calls += 1
        if cancel_event.is_set():
            self.last_status = "cancelled"
            return None
        if not self.captures:
            self.last_status = "natural_response_timeout"
            return None
        capture = self.captures.pop(0)
        # Cancelling *after* a capture models the production finally block:
        # the already-recorded witness survives, no further child is armed.
        if self.cancel_on_next:
            cancel_event.set()
        self.last_status = "captured" if capture is not None else "no_capture"
        return capture


class RecordingSink:
    def __init__(self) -> None:
        self.rows: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, event: str, fields: Any) -> None:
        self.rows.append((event, dict(fields)))

    def names(self) -> list[str]:
        return [name for name, _ in self.rows]


def build_provider(
    observer: SequenceObserver,
    sink: RecordingSink,
    *,
    expected_captures: int = 1,
) -> MissionResultWitnessProvider:
    provider = MissionResultWitnessProvider(
        observer=observer,
        event_log=sink,
        event_sink=sink,
        expected_captures=expected_captures,
    )
    provider.arm()
    if not provider.wait(timeout_seconds=2.0):
        raise AssertionError("observer thread did not finish")
    return provider


class MultiCaptureProviderTests(unittest.TestCase):
    def test_two_exchanges_are_recorded_and_selected_by_fleet(self) -> None:
        sink = RecordingSink()
        observer = SequenceObserver(
            make_capture(fleet_id=2),
            make_capture(fleet_id=3, ship_ids=FLEETS[3]["ships"]),
        )
        provider = build_provider(observer, sink, expected_captures=2)

        self.assertEqual(observer.calls, 2)
        self.assertEqual(len(provider.witnesses), 2)
        self.assertEqual(
            [name for name, _ in sink.rows].count(MISSION_RESULT_EVENT), 2
        )
        self.assertEqual(provider.witness_for_fleet(2).fleet_id, 2)
        self.assertEqual(provider.witness_for_fleet(3).fleet_id, 3)
        self.assertIsNone(provider.witness_for_fleet(4))
        witnesses = read_witnesses(
            [{"event": name, "fields": fields} for name, fields in sink.rows]
        )
        self.assertEqual({item.fleet_id for item in witnesses}, {2, 3})

    def test_a_receipt_is_never_borrowed_from_another_fleet(self) -> None:
        sink = RecordingSink()
        provider = build_provider(
            SequenceObserver(make_capture(fleet_id=2)), sink
        )
        self.assertTrue(provider.receipt_actionable_for(2))
        self.assertFalse(provider.receipt_actionable_for(3))
        self.assertFalse(provider.receipt_actionable_for(4))

    def test_a_fleet_without_its_own_exchange_is_refused(self) -> None:
        sink = RecordingSink()
        provider = build_provider(
            SequenceObserver(make_capture(fleet_id=2)), sink
        )
        record = DispatchRecord(
            fleet_id=3, mission_id=FLEETS[3]["mission_id"],
            ship_ids=FLEETS[3]["ships"], observed_at=CONFIRMED_AT,
        )
        verdict = provider.verdict_for(fleet_id=3, dispatch_record=record)
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.reason, "witness_fleet_mismatch")

    def test_a_failed_second_round_keeps_the_first_capture(self) -> None:
        sink = RecordingSink()
        provider = build_provider(
            SequenceObserver(make_capture(fleet_id=2), None),
            sink,
            expected_captures=2,
        )
        self.assertEqual(provider.status, "captured")
        self.assertEqual(len(provider.witnesses), 1)
        self.assertTrue(provider.receipt_actionable_for(2))
        self.assertFalse(provider.wait_for_fleet(3, 0.0))
        self.assertIn("no_capture", [fields.get("observer_status")
                                    for _, fields in sink.rows])

    def test_expected_captures_is_validated(self) -> None:
        sink = RecordingSink()
        for value in (0, -1, True, "2", 1.5):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    MissionResultWitnessProvider(
                        observer=SequenceObserver(),
                        event_log=sink,
                        expected_captures=value,  # type: ignore[arg-type]
                    )

    def test_cancellation_stops_before_the_next_child(self) -> None:
        sink = RecordingSink()
        observer = SequenceObserver(make_capture(), make_capture())
        observer.cancel_on_next = True
        provider = build_provider(observer, sink, expected_captures=3)
        self.assertEqual(observer.calls, 1)
        self.assertEqual(len(provider.witnesses), 1)

    def test_wait_for_fleet_is_bounded(self) -> None:
        sink = RecordingSink()
        provider = build_provider(
            SequenceObserver(make_capture(fleet_id=2)), sink
        )
        self.assertTrue(provider.wait_for_fleet(2, 0.0))
        self.assertFalse(provider.wait_for_fleet(3, 0.0))


class ScriptedProvider:
    """A provider-shaped double for failure injection at close-out."""

    def __init__(self, *, verdict: WitnessVerdict | None = None,
                 actionable: bool = True, raises: bool = False) -> None:
        self.verdict = verdict
        self.actionable = actionable
        self.raises = raises
        self.waits: list[int] = []
        self.verdict_kwargs: dict[str, Any] = {}
        self.receipt_calls: list[int] = []

    def wait_for_fleet(self, fleet_id: int, timeout_seconds: float) -> bool:
        self.waits.append(fleet_id)
        return True

    def verdict_for(self, **kwargs: Any) -> WitnessVerdict:
        self.verdict_kwargs = kwargs
        if self.raises:
            raise RuntimeError("observer unavailable")
        if self.verdict is not None:
            return self.verdict
        return WitnessVerdict(True, "witness_agrees_with_dispatch_record")

    def receipt_actionable_for(self, fleet_id: int) -> bool:
        self.receipt_calls.append(fleet_id)
        return self.actionable


class SettlementRewardReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "dispatch.sqlite3"
        self.registry = SqliteDispatchRegistry(self.path, task_kind="expedition")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def build(
        self, *, provider: Any, persist_rows: bool = True
    ) -> tuple[ContinuousSettlementController, RecordingUi, list[tuple[str, dict]]]:
        if persist_rows:
            persist(self.registry, 2)
            persist(self.registry, 3)
        clock = StepClock()
        reader = TwoFleetReader(clock)
        ui = RecordingUi(screen=None, probe=True)
        scheduler = ContinuousExpeditionScheduler(
            state_store=None, dispatch_fleet_ids=(2, 3)
        )
        prepare_two_returns(scheduler)
        events: list[tuple[str, dict]] = []
        controller = ContinuousSettlementController(
            scheduler=scheduler,
            state_reader=reader,
            ui_driver=ui,
            ui_arbiter=UiOperationArbiter(),
            event_sink=lambda name, fields: events.append((name, dict(fields))),
            allow_ui_actions=True,
            clock=clock,
            dispatch_registry=self.registry,
            witness_provider=provider,
            witness_wait_seconds=0.0,
        )
        return controller, ui, events

    def receipt_event(self, events) -> tuple[str, dict]:
        matches = [
            (name, fields) for name, fields in events
            if name.startswith("settlement_reward_receipt_")
        ]
        self.assertEqual(len(matches), 1)
        return matches[0]

    def test_a_dispatch_bound_complete_receipt_is_verified(self) -> None:
        sink = RecordingSink()
        provider = build_provider(
            SequenceObserver(make_capture(fleet_id=2)), sink
        )
        controller, ui, events = self.build(provider=provider)

        execution = controller.settle_one_returned(2, pages_drained_globally=True)

        name, fields = self.receipt_event(events)
        self.assertEqual(name, "settlement_reward_receipt_verified")
        self.assertTrue(execution.reward_receipt_verified)
        self.assertEqual(
            execution.reward_receipt_reason, "witness_agrees_with_dispatch_record"
        )
        self.assertTrue(fields["dispatch_record_bound"])
        self.assertTrue(fields["receipt_actionable"])
        self.assertIs(fields["game_api_requests"], False)
        # The drain path proves the screens gone; it must not click again.
        self.assertEqual([call for call, _ in ui.calls], ["settlement_screen"])
        # The witness came from the game's own exchange, durably recorded
        # before the controller adopted it.
        self.assertEqual(len(provider.witnesses), 1)
        self.assertEqual(
            [name for name, _ in sink.rows].count(MISSION_RESULT_EVENT), 1
        )

    def test_no_capture_never_claims_a_verified_receipt(self) -> None:
        sink = RecordingSink()
        provider = build_provider(SequenceObserver(None), sink)
        controller, _, events = self.build(provider=provider)

        execution = controller.settle_one_returned(2, pages_drained_globally=True)

        name, fields = self.receipt_event(events)
        self.assertEqual(name, "settlement_reward_receipt_unverified")
        self.assertFalse(execution.reward_receipt_verified)
        self.assertIs(fields["receipt_actionable"], False)
        self.assertEqual(fields["witness_verdict_reason"], "witness_not_yet_observed")

    def test_a_witness_for_another_fleet_is_not_adopted(self) -> None:
        sink = RecordingSink()
        provider = build_provider(
            SequenceObserver(make_capture(
                fleet_id=3, ship_ids=FLEETS[3]["ships"]
            )),
            sink,
        )
        controller, _, events = self.build(provider=provider)

        execution = controller.settle_one_returned(2, pages_drained_globally=True)

        name, fields = self.receipt_event(events)
        self.assertEqual(name, "settlement_reward_receipt_unverified")
        self.assertEqual(fields["witness_verdict_reason"], "witness_fleet_mismatch")
        self.assertFalse(execution.reward_receipt_verified)

    def test_an_incomplete_receipt_is_not_verified(self) -> None:
        sink = RecordingSink()
        provider = build_provider(
            SequenceObserver(make_capture(receipt_status="incomplete")), sink
        )
        controller, _, events = self.build(provider=provider)

        execution = controller.settle_one_returned(2, pages_drained_globally=True)

        name, fields = self.receipt_event(events)
        self.assertEqual(name, "settlement_reward_receipt_unverified")
        self.assertEqual(fields["witness_verdict_reason"], "reward_receipt_not_actionable")
        self.assertFalse(execution.reward_receipt_verified)

    def test_no_dispatch_record_refuses_the_verdict(self) -> None:
        sink = RecordingSink()
        provider = build_provider(
            SequenceObserver(make_capture(fleet_id=2)), sink
        )
        controller, _, events = self.build(provider=provider, persist_rows=False)

        execution = controller.settle_one_returned(2, pages_drained_globally=True)

        _, fields = self.receipt_event(events)
        self.assertFalse(execution.reward_receipt_verified)
        self.assertIs(fields["dispatch_record_bound"], False)
        self.assertEqual(
            fields["witness_verdict_reason"], "witness_without_dispatch_record"
        )

    def test_a_provider_error_fails_closed_without_rewinding_settlement(self) -> None:
        controller, _, events = self.build(
            provider=ScriptedProvider(raises=True)
        )
        execution = controller.settle_one_returned(2, pages_drained_globally=True)
        name, fields = self.receipt_event(events)
        self.assertEqual(name, "settlement_reward_receipt_unverified")
        self.assertTrue(fields["witness_verdict_reason"].startswith(
            "witness_provider_error"
        ))
        self.assertFalse(execution.reward_receipt_verified)
        self.assertIn("settlement_closed_without_reward_screens",
                      [name for name, _ in events])

    def test_unwired_provider_leaves_the_old_path_unchanged(self) -> None:
        controller, ui, events = self.build(provider=None)
        execution = controller.settle_one_returned(2, pages_drained_globally=True)
        self.assertFalse(execution.reward_receipt_verified)
        self.assertIsNone(execution.reward_receipt_reason)
        self.assertEqual(
            [name for name, _ in events if name.startswith("settlement_reward_receipt_")],
            [],
        )
        self.assertEqual([call for call, _ in ui.calls], ["settlement_screen"])

    def test_constructor_rejects_a_broken_provider_and_bad_wait(self) -> None:
        clock = StepClock()
        reader = TwoFleetReader(clock)
        scheduler = ContinuousExpeditionScheduler(
            state_store=None, dispatch_fleet_ids=(2, 3)
        )
        for provider, wait in ((object(), 1.0), (None, -1.0), (None, True)):
            with self.subTest(provider=provider, wait=wait):
                with self.assertRaises(ValueError):
                    ContinuousSettlementController(
                        scheduler=scheduler,
                        state_reader=reader,
                        ui_driver=RecordingUi(),
                        ui_arbiter=UiOperationArbiter(),
                        event_sink=lambda _n, _f: None,
                        allow_ui_actions=True,
                        witness_provider=provider,  # type: ignore[arg-type]
                        witness_wait_seconds=wait,  # type: ignore[arg-type]
                    )


class WitnessCompositionConfigurationTests(unittest.TestCase):
    """The production CLI must make the loop self-contained by default."""

    def _parse(self, *extra: str):
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "loop.jsonl"
            with ExitStack() as stack:
                stack.enter_context(patch.object(entry, "LOG_PATH", entry.LOG_PATH))
                stack.enter_context(patch.object(entry, "RUN_ID", entry.RUN_ID))
                stack.enter_context(
                    patch.object(entry, "HOME_REFERENCE_BASE64", entry.HOME_REFERENCE_BASE64)
                )
                stack.enter_context(patch.object(entry, "_print_event"))
                stack.enter_context(patch.object(entry, "_stop_run_log"))
                run = stack.enter_context(patch.object(entry, "run", return_value=0))
                code = entry._main_impl([
                    "--authorize-ui-actions", "--fleet-id", "3",
                    "--mission-id", "3", "--log-path", str(log), *extra,
                ])
            self.assertEqual(code, 0)
            return run.call_args.args[0]

    def test_in_process_witness_is_on_by_default_and_can_be_disabled(self):
        args = self._parse()
        self.assertIs(args.in_process_witness, True)
        self.assertEqual(args.witness_captures, 0)
        self.assertEqual(
            args.mission_result_observer_script,
            entry.DEFAULT_MISSION_RESULT_OBSERVER,
        )
        args = self._parse("--no-in-process-witness")
        self.assertIs(args.in_process_witness, False)

    def test_witness_captures_default_to_one_per_fleet(self):
        self.assertEqual(
            entry._witness_configuration(SimpleNamespace(), 3),
            (entry.DEFAULT_MISSION_RESULT_TIMEOUT_SECONDS, 3),
        )
        self.assertEqual(
            entry._witness_configuration(SimpleNamespace(), 1),
            (entry.DEFAULT_MISSION_RESULT_TIMEOUT_SECONDS, 1),
        )
        self.assertEqual(
            entry._witness_configuration(
                SimpleNamespace(
                    mission_result_timeout_seconds=42.0, witness_captures=5
                ),
                3,
            ),
            (42.0, 5),
        )

    def test_observer_script_is_the_already_verified_asset(self):
        args = SimpleNamespace()
        self.assertEqual(
            entry._witness_observer_script(args),
            entry.DEFAULT_MISSION_RESULT_OBSERVER,
        )
        self.assertTrue(entry.DEFAULT_MISSION_RESULT_OBSERVER.is_file())
        custom = ROOT / "tools" / "hand_sandbox" / "custom.js"
        self.assertEqual(
            entry._witness_observer_script(
                SimpleNamespace(mission_result_observer_script=custom)
            ),
            custom,
        )


class SingleFrameProbeEventContractTests(unittest.TestCase):
    """Live stage-0 regression: the probe event must not shadow ``state``.

    ``_print_event`` uses its ``state`` parameter for a PoiFleetMissionState and
    reads ``state.fleet_id``. Sending the probe's UI state name as ``state=``
    raised ``AttributeError``; the drain probe then failed closed to UNKNOWN and
    the global drain performed zero actions. No game connection here.
    """

    def make_ranking(self) -> object:
        from kancollebot.adapters.poi.visual_fingerprint import FingerprintRanking

        return FingerprintRanking(
            best_name="HOME_PORT",
            best_score=0.9845,
            second_name="SORTIE_MENU",
            second_score=0.7733,
            scores={"HOME_PORT": 0.9845, "SORTIE_MENU": 0.7733},
            accepted=True,
        )

    def test_ranking_fields_do_not_occupy_the_state_parameter(self):
        fields = entry._single_frame_ranking_fields(self.make_ranking())
        self.assertNotIn("state", fields)
        self.assertEqual(fields["observed"], "HOME_PORT")
        self.assertEqual(fields["best_score"], 0.9845)
        self.assertEqual(fields["second_score"], 0.7733)
        self.assertEqual(fields["minimum_score"], 0.97)
        self.assertEqual(fields["minimum_margin"], 0.04)
        self.assertFalse(fields["authorizes_click"])

    def test_real_event_emitter_accepts_the_probe_fields(self):
        fields = entry._single_frame_ranking_fields(self.make_ranking())
        with patch("builtins.print") as printed, patch.object(
            entry, "_append_log_line"
        ):
            entry._print_event("single_frame_all_state_ranking", **fields)
        printed.assert_called_once()
        row = json.loads(printed.call_args.args[0])
        self.assertEqual(row["observed"], "HOME_PORT")
        self.assertEqual(row["event"], "single_frame_all_state_ranking")
        self.assertNotIn("fleet_id", row)

    def test_no_print_event_call_passes_a_state_keyword(self):
        source = (ROOT / "tools" / "one_expedition_ui_loop.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        offenders = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_print_event"
            and any(keyword.arg == "state" for keyword in node.keywords)
        ]
        self.assertEqual(offenders, [], f"_print_event(state=...) at lines {offenders}")


if __name__ == "__main__":
    unittest.main()
