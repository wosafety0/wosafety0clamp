"""Background owner for the passive expedition-result observer.

Production used to *read* ``mission_result_observed`` rows that an external
tool had recorded, but never armed the observer itself. This module makes the
observer part of the production process: it must be armed before the first
result UI click, captures one natural ``api_req_mission/result`` exchange per
expected return, records each durably, and offers them to the settlement
controller for identity and receipt checks.

It sends nothing, clicks nothing, and never guesses. A missing, malformed,
unrecorded or incomplete witness fails closed: the caller gets a refusal and
must not treat any of those as a confirmed reward.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol

from kancollebot.adapters.poi.mission_result_observer import (
    PassiveMissionResultObserver,
)
from kancollebot.expeditions.mission_result_witness import (
    MISSION_RESULT_EVENT,
    DispatchRecord,
    MissionResultWitness,
    MissionResultWitnessError,
    WitnessVerdict,
    cross_check_with_dispatch,
    evaluate_witness,
    parse_witness,
    witness_fields,
)

EventSink = Callable[[str, Mapping[str, object]], None]


class _Observer(Protocol):
    """The part of ``PassiveMissionResultObserver`` this owner uses."""

    last_status: str
    last_detail: Any

    def capture_cancellable(
        self, cancel_event: threading.Event
    ) -> dict[str, Any] | None: ...


def capture_to_fields(capture: Mapping[str, Any]) -> dict[str, object]:
    """Translate one normalized observer capture into ``parse_witness`` fields.

    The observer deliberately keeps a narrow allowlist and camelCase names
    (``apiRewardReceipt`` / ``useitemFlags`` / ``useitemId``). The durable
    witness format uses snake_case. Only this translation happens here; all
    validation stays in ``parse_witness``.
    """
    data = capture.get("data")
    source = capture.get("source")
    if not isinstance(data, Mapping) or not isinstance(source, Mapping):
        raise ValueError("capture must contain data and source mappings")

    receipt: dict[str, object] | None = None
    raw_receipt = data.get("apiRewardReceipt")
    if isinstance(raw_receipt, Mapping):
        items: list[dict[str, object]] = []
        raw_items = raw_receipt.get("items")
        if isinstance(raw_items, (list, tuple)):
            for item in raw_items:
                if not isinstance(item, Mapping):
                    continue
                items.append({
                    "slot": item.get("slot"),
                    "useitem_id": item.get("useitemId"),
                    "count": item.get("useitemCount"),
                })
        receipt = {
            "status": raw_receipt.get("status"),
            "material": raw_receipt.get("material"),
            "useitem_flags": raw_receipt.get("useitemFlags"),
            "items": items,
        }
    return {
        "fleet_id": data.get("apiDeckId"),
        "quest_name": data.get("apiQuestName"),
        "clear_result": data.get("apiClearResult"),
        "ship_ids": data.get("apiShipId"),
        "reward_receipt": receipt,
        "requested_at": source.get("requestedAt"),
        "response_received_at": source.get("responseReceivedAt"),
    }


def receipt_is_actionable(witness: MissionResultWitness | None) -> bool:
    """Whether a witness may be used to claim a verified reward receipt.

    An unknown result code, a missing receipt or anything short of
    ``status="complete"`` is not actionable. This is a *receipt* check, not an
    identity check: callers must also have an allowed ``WitnessVerdict``.
    """
    if witness is None or witness.result_kind == "unknown":
        return False
    receipt = witness.reward_receipt
    return receipt is not None and receipt.status == "complete"


class MissionResultWitnessProvider:
    """Cancellable background owner for a passive result observer.

    One observer child captures one natural result exchange and then exits,
    which is why ``expected_captures`` defaults to **1**: the historical
    single-return contract is unchanged. A multi-fleet run expects one result
    exchange per returning fleet, so the owner starts a fresh child after each
    durable capture. Every captured witness is recorded before it is offered;
    a missing, malformed or unrecorded exchange ends that child fail-closed and
    never authorises a reward claim.
    """

    def __init__(
        self,
        *,
        observer: PassiveMissionResultObserver | _Observer,
        event_log: EventSink,
        event_sink: EventSink | None = None,
        clock: Callable[[], datetime] | None = None,
        expected_captures: int = 1,
    ) -> None:
        if not callable(event_log):
            raise ValueError("a durable event_log is required")
        if event_sink is not None and not callable(event_sink):
            raise ValueError("event_sink must be callable when given")
        if (
            isinstance(expected_captures, bool)
            or not isinstance(expected_captures, int)
            or expected_captures < 1
        ):
            raise ValueError("expected_captures must be a positive integer")
        self.observer = observer
        self.event_log = event_log
        self.event_sink = event_sink
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.expected_captures = expected_captures
        self._cancel = threading.Event()
        self._done = threading.Event()
        self._lock = threading.Lock()
        self._armed = False
        self._status = "not_armed"
        self._witness: MissionResultWitness | None = None
        self._witnesses: list[MissionResultWitness] = []
        self._thread: threading.Thread | None = None

    @property
    def armed(self) -> bool:
        with self._lock:
            return self._armed

    @property
    def status(self) -> str:
        with self._lock:
            return self._status

    @property
    def witness(self) -> MissionResultWitness | None:
        """The most recently captured witness (single-capture compatibility)."""
        with self._lock:
            return self._witness

    @property
    def witnesses(self) -> tuple[MissionResultWitness, ...]:
        """Every witness that was captured *and durably recorded*, in order."""
        with self._lock:
            return tuple(self._witnesses)

    @property
    def receipt_actionable(self) -> bool:
        """Single-capture compatibility: is the latest receipt actionable?"""
        with self._lock:
            return self._status == "captured" and receipt_is_actionable(self._witness)

    def witness_for_fleet(self, fleet_id: int) -> MissionResultWitness | None:
        """The newest recorded witness the game itself attributed to one fleet."""
        with self._lock:
            if self._status != "captured":
                return None
            for witness in reversed(self._witnesses):
                if witness.fleet_id == fleet_id:
                    return witness
            return None

    def receipt_actionable_for(self, fleet_id: int) -> bool:
        """Receipt gate for one fleet - never borrowed from another fleet.

        A captured witness for Fleet 2 says nothing about Fleet 3's reward, so a
        fleet without its own recorded witness is never actionable.
        """
        witness = self.witness_for_fleet(fleet_id)
        return witness is not None and receipt_is_actionable(witness)

    def wait_for_fleet(self, fleet_id: int, timeout_seconds: float) -> bool:
        """Wait briefly for one fleet's exchange to be recorded at close-out.

        The drain's clicks and the observer's recorder run in different threads;
        settlement must not declare "no witness" before the recorder has had a
        bounded chance to persist what the game already sent.
        """
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        while True:
            if self.witness_for_fleet(fleet_id) is not None:
                return True
            if self._done.is_set():
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            self._done.wait(min(0.05, remaining))

    def arm(self) -> None:
        """Start the observer now, strictly before any result UI click."""
        with self._lock:
            if self._armed:
                raise RuntimeError("mission result observer can only be armed once")
            self._armed = True
            self._status = "armed"
        self._emit("mission_result_witness_armed", {
            "observer_status": getattr(self.observer, "last_status", "not_started"),
            "expected_captures": self.expected_captures,
            "game_api_requests": False,
            "game_input_sent": False,
        })
        thread = threading.Thread(
            target=self.run_once,
            name="mission-result-witness-observer",
            daemon=True,
        )
        with self._lock:
            self._thread = thread
        thread.start()

    def run_once(self) -> None:
        """The background thread body; also directly callable in offline tests.

        Each round launches a fresh observer child, because one child exits as
        soon as it has captured one natural exchange. A witness is appended to
        the in-memory list only *after* its durable row is written, so the
        settlement close-out can never read an unrecorded witness.
        """
        try:
            for index in range(self.expected_captures):
                if self._cancel.is_set():
                    break
                try:
                    capture = self.observer.capture_cancellable(self._cancel)
                except Exception as exc:  # a thread cannot re-raise to its owner
                    self._round_failure(index, "observer_error", {
                        "error": type(exc).__name__,
                    })
                    break
                if capture is None:
                    self._round_failure(index, "no_capture", {
                        "observer_status": getattr(self.observer, "last_status", "unknown"),
                        "observer_detail": getattr(self.observer, "last_detail", None),
                    })
                    break
                try:
                    witness = parse_witness(capture_to_fields(capture))
                except (MissionResultWitnessError, ValueError, TypeError) as exc:
                    self._round_failure(index, "invalid_witness", {
                        "error": type(exc).__name__,
                    })
                    break
                # A witness that cannot be recorded is not evidence.
                try:
                    self.event_log(MISSION_RESULT_EVENT, witness_fields(witness))
                except Exception as exc:
                    self._round_failure(index, "record_failed", {
                        "error": type(exc).__name__,
                    })
                    break
                with self._lock:
                    self._witnesses.append(witness)
                    self._status = "captured"
                    self._witness = witness
                self._emit("mission_result_witness_finished", {
                    "observer_status": "captured",
                    "captured": True,
                    "capture_index": index,
                    "fleet_id": witness.fleet_id,
                    "quest_name": witness.quest_name,
                    "clear_result": witness.clear_result,
                    "result_kind": witness.result_kind,
                    "reward_receipt_status": (
                        witness.reward_receipt.status
                        if witness.reward_receipt is not None else None
                    ),
                    "game_api_requests": False,
                    "game_input_sent": False,
                })
        finally:
            self._done.set()

    def _round_failure(
        self, index: int, status: str, detail: Mapping[str, object]
    ) -> None:
        """End one observer child fail-closed without erasing earlier captures.

        A second exchange that never arrived must not invalidate a first one
        that was already recorded; the affected fleet simply has no witness.
        """
        with self._lock:
            if not self._witnesses:
                self._status = status
                self._witness = None
        self._emit("mission_result_witness_finished", {
            "observer_status": status,
            "captured": False,
            "capture_index": index,
            **detail,
        })

    def _emit(self, event: str, fields: Mapping[str, object]) -> None:
        if self.event_sink is None:
            return
        try:
            self.event_sink(event, fields)
        except Exception:
            # Diagnostics must never turn into a claimed capture.
            pass

    def wait(self, timeout_seconds: float | None = None) -> bool:
        """Whether the observer has finished (used at settlement close-out)."""
        return self._done.wait(timeout_seconds)

    def cancel(self) -> None:
        """Stop waiting for a natural exchange; no capture is claimed."""
        self._cancel.set()

    def verdict_for(
        self,
        *,
        fleet_id: int,
        expected_ship_ids: tuple[int, ...] | None = None,
        expected_quest_name: str | None = None,
        dispatch_record: DispatchRecord | None = None,
        observed_at: datetime | None = None,
    ) -> WitnessVerdict:
        """Identity verdict for one fleet, or a refusal when nothing is proven.

        Only a captured *and durably recorded* witness can be adopted. The
        verdict never advances a state machine and never asserts a reward:
        ``receipt_actionable`` is a separate, additional gate for that claim.
        """
        if self.status != "captured":
            witness = None
        else:
            # Prefer the exchange the game itself attributed to this fleet. If
            # no such exchange exists, fall back to the latest one so that
            # ``evaluate_witness`` reports the fleet mismatch instead of a
            # silent "not observed" - a refusal either way, never a guess.
            witness = self.witness_for_fleet(fleet_id) or self.witness
        verdict = evaluate_witness(
            witness=witness,
            expected_fleet_id=fleet_id,
            expected_ship_ids=expected_ship_ids,
            expected_quest_name=expected_quest_name,
            observed_at=observed_at or self.clock(),
        )
        if not verdict.allowed or verdict.witness is None:
            return verdict
        if dispatch_record is None:
            # ``cross_check_with_dispatch`` deliberately allows a witness with
            # no dispatch record as a *plain observation*. That is too weak for
            # a closed loop: without the durable dispatch identity this
            # provider cannot prove which dispatch the return closes, so it
            # refuses instead of letting an unattributed witness be adopted.
            return WitnessVerdict(
                allowed=False,
                reason="witness_without_dispatch_record",
                witness=verdict.witness,
            )
        return cross_check_with_dispatch(
            witness=verdict.witness, record=dispatch_record
        )
