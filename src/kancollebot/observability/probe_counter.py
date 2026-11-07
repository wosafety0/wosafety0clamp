"""Process-wide counters for *external observation probes*.

The expedition timer sleep must be a real blocking sleep: while it waits, no Poi
read, CDP command, screenshot or image classification may happen.  A log line
claiming "nothing happened" is not evidence, so the low-level probe entry points
themselves increment these counters.  The sleeper snapshots the counters before
and after its sleep and refuses to continue if any of them moved.

Counters are deliberately dumb: they never change control flow and never raise
into the calling probe.  Only the sleeper interprets a delta as a violation.
"""
from __future__ import annotations

import threading
from typing import Mapping

PROBE_KINDS: tuple[str, ...] = (
    "poi",
    "cdp",
    "screenshot",
    "classification",
    "fleet_state",
)


class ProbeCounter:
    """Thread-safe, monotonic counters for the five probe categories."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[str, int] = {kind: 0 for kind in PROBE_KINDS}

    def mark(self, kind: str) -> None:
        if kind not in self._counts:
            return
        with self._lock:
            self._counts[kind] += 1

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)

    def delta(self, before: Mapping[str, int]) -> dict[str, int]:
        current = self.snapshot()
        result = {
            kind: max(0, current.get(kind, 0) - int(before.get(kind, 0)))
            for kind in PROBE_KINDS
        }
        # Never hide an unknown category that appeared in either snapshot.
        for kind in set(current) | set(before):
            if kind not in result:
                result[kind] = max(0, current.get(kind, 0) - int(before.get(kind, 0)))
        return result

    def reset(self) -> None:
        with self._lock:
            self._counts = {kind: 0 for kind in PROBE_KINDS}


PROBE_COUNTER = ProbeCounter()


def mark_probe(kind: str) -> None:
    """One-line hook for a probe entry point; never propagates an error."""
    try:
        PROBE_COUNTER.mark(kind)
    except Exception:
        pass


def total(delta: Mapping[str, int]) -> int:
    return sum(int(value) for value in delta.values())
