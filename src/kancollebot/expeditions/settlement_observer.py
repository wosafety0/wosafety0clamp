"""Read-only, fail-closed observation of returned expedition settlement screens.

The verifier is responsible for obtaining two consecutive in-memory frame
matches. This module validates that structured evidence and advances no UI.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


class SettlementObservationError(RuntimeError):
    """Settlement UI state cannot be proven from the current visible screen."""


@dataclass(frozen=True, slots=True)
class SettlementObservation:
    fleet_id: int
    state: str
    consecutive_states: tuple[str, ...]
    evidence: str

    def __post_init__(self) -> None:
        if isinstance(self.fleet_id, bool) or not isinstance(self.fleet_id, int) or self.fleet_id < 1:
            raise ValueError("fleet_id must be a positive integer")
        if self.state not in {"RETURN_RESULT", "BONUS_ITEMS"}:
            raise ValueError("unsupported settlement state")
        if (
            not isinstance(self.consecutive_states, tuple)
            or len(self.consecutive_states) < 2
            or any(item != self.state for item in self.consecutive_states)
        ):
            raise ValueError("settlement evidence must contain two consecutive matching frames")
        if not isinstance(self.evidence, str) or not self.evidence.strip():
            raise ValueError("evidence must be a non-empty string")


class SettlementStateObserver:
    """Validate a two-frame UI checkpoint without sending any game input."""

    EXPECTED_STATES = frozenset({"RETURN_RESULT", "BONUS_ITEMS"})

    def __init__(self, verify_state: Callable[[str], Any]) -> None:
        if not callable(verify_state):
            raise ValueError("verify_state must be callable")
        self._verify_state = verify_state

    def observe(self, fleet_id: int, expected_state: str) -> SettlementObservation:
        if isinstance(fleet_id, bool) or not isinstance(fleet_id, int) or fleet_id < 1:
            raise ValueError("fleet_id must be a positive integer")
        if expected_state not in self.EXPECTED_STATES:
            raise ValueError("expected_state must be RETURN_RESULT or BONUS_ITEMS")

        # A mismatch or capture failure propagates; callers must stop the shared UI queue.
        evidence = self._verify_state(expected_state)
        state = getattr(evidence, "state", None)
        frames = getattr(evidence, "consecutive_states", None)
        source = getattr(evidence, "evidence", None)
        if state != expected_state:
            raise SettlementObservationError("verifier returned evidence for a different UI state")
        if (
            not isinstance(frames, tuple)
            or len(frames) < 2
            or any(frame != expected_state for frame in frames)
        ):
            raise SettlementObservationError("two consecutive matching settlement frames are required")
        if not isinstance(source, str) or not source.strip():
            raise SettlementObservationError("settlement evidence source is missing")
        return SettlementObservation(
            fleet_id=fleet_id,
            state=expected_state,
            consecutive_states=frames,
            evidence=source,
        )
