"""Background owner for the passive expedition-result observer.

Production used to *read* ``mission_result_observed`` rows that an external
tool had recorded, but never armed the observer itself. This module makes the
observer part of the production process: it must be armed before the first
result UI click, captures one natural ``api_req_mission/result`` exchange,
records it durably, and offers it to the settlement controller for identity
and receipt checks.

It sends nothing, clicks nothing, and never guesses. A missing, malformed,
unrecorded or incomplete witness fails closed: the caller gets a refusal and
must not treat any of those as a confirmed reward.
"""
from __future__ import annotations

import threading
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
    """One-shot, cancellable background owner for one passive observer.

    One observer captures one natural result exchange. Multi-fleet cycles need
    one owner per expected exchange; this class does not pretend otherwise.
    """

    def __init__(
        self,
        *,
        observer: PassiveMissionResultObserver | _Observer,
        event_log: EventSink,
        event_sink: EventSink | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not callable(event_log):
            raise ValueError("a durable event_log is required")
        if event_sink is not None and not callable(event_sink):
            raise ValueError("event_sink must be callable when given")
        self.observer = observer
        self.event_log = event_log
        self.event_sink = event_sink
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._cancel = threading.Event()
        self._done = threading.Event()
        self._lock = threading.Lock()
        self._armed = False
        self._status = "not_armed"
        self._witness: MissionResultWitness | None = None
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
        with self._lock:
            return self._witness

    @property
    def receipt_actionable(self) -> bool:
        with self._lock:
            return self._status == "captured" and receipt_is_actionable(self._witness)

    def arm(self) -> None:
        """Start the observer now, strictly before any result UI click."""
        with self._lock:
            if self._armed:
                raise RuntimeError("mission result observer can only be armed once")
            self._armed = True
            self._status = "armed"
        self._emit("mission_result_witness_armed", {
            "observer_status": getattr(self.observer, "last_status", "not_started"),
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
        """The background thread body; also directly callable in offline tests."""
        try:
            capture = self.observer.capture_cancellable(self._cancel)
        except Exception as exc:  # a background thread cannot re-raise to its owner
            self._finish("observer_error", None, {"error": type(exc).__name__})
            return
        if capture is None:
            self._finish(
                "no_capture",
                None,
                {
                    "observer_status": getattr(self.observer, "last_status", "unknown"),
                    "observer_detail": getattr(self.observer, "last_detail", None),
                },
            )
            return
        try:
            witness = parse_witness(capture_to_fields(capture))
        except (MissionResultWitnessError, ValueError, TypeError) as exc:
            self._finish("invalid_witness", None, {"error": type(exc).__name__})
            return

        # A witness that cannot be recorded is not evidence: the audit trail
        # must exist before any downstream claim is allowed.
        try:
            self.event_log(MISSION_RESULT_EVENT, witness_fields(witness))
        except Exception as exc:
            self._finish("record_failed", None, {"error": type(exc).__name__})
            return
        self._finish("captured", witness, {
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

    def _finish(
        self,
        status: str,
        witness: MissionResultWitness | None,
        detail: Mapping[str, object],
    ) -> None:
        with self._lock:
            self._status = status
            self._witness = witness
        self._emit("mission_result_witness_finished", {
            "observer_status": status,
            "captured": witness is not None,
            **detail,
        })
        self._done.set()

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
        witness = self.witness if self.status == "captured" else None
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
