"""Fail-closed, read-only expedition readiness result types."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReadinessGate:
    name: str
    passed: bool | None
    detail: str


@dataclass(frozen=True, slots=True)
class ExpeditionReadinessResult:
    expedition_id: int
    candidate_ship_ids: tuple[int, ...]
    gates: tuple[ReadinessGate, ...]
    ready_for_review: bool
    dispatch_authorized: bool = False

    @classmethod
    def evaluate(cls, expedition_id: int, candidate_ship_ids: tuple[int, ...], gates: tuple[ReadinessGate, ...]):
        if type(expedition_id) is not int or expedition_id <= 0:
            raise ValueError("expedition_id must be a positive integer")
        ready = bool(gates) and all(gate.passed is True for gate in gates)
        return cls(expedition_id, candidate_ship_ids, gates, ready, False)
