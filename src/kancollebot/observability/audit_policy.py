"""Which events are safety-critical, and what happens when they cannot be written.

The run log is not a debugging convenience. Some of its rows are the only
evidence that an irreversible action was taken deliberately - a dispatch, a
supply click, a settlement step, a deliberate stop. If one of those rows cannot
be durably written, the automation must not proceed: an unlogged dispatch is
indistinguishable from an accidental one during recovery.

Two classes, two behaviours:

* **ordinary diagnostics** (a fleet phase transition, an observation summary):
  a write failure is recorded on the sink, reported through ``on_error`` and the
  run continues. Losing a diagnostic must not stop a run.
* **critical audit events** (the set below): the write is flushed to disk and a
  failure raises :class:`AuditLogUnavailable`. Because every call site emits its
  critical event *before* the input it describes, the exception lands before the
  click, the dispatch or the settlement step - fail-closed by ordering, not by a
  second check that could be forgotten.

Nothing here retries a write. A retry that succeeded after a failure would make
"the log said it worked" and "the action happened" two different facts, which is
exactly the ambiguity this module exists to prevent.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

__all__ = [
    "CRITICAL_AUDIT_EVENTS",
    "AuditLogUnavailable",
    "audit_gate",
    "is_critical_event",
]

#: Every row that must be durable before the action it describes may happen.
#:
#: Run lifecycle rows (``run_started`` / ``run_stopped`` / ``run_crashed``) are
#: deliberately NOT here. They are emitted from ``finally`` blocks where raising
#: would replace the exception that actually stopped the run - the log would then
#: hide the very failure it is supposed to explain. They are still written and
#: still reported on failure; they just cannot be the reason a run aborts.
CRITICAL_AUDIT_EVENTS: frozenset[str] = frozenset(
    {
        # dispatch: the irreversible transaction
        "dispatch_intent_persisted",
        "dispatch_confirmed_by_poi",
        "dispatch_home_port_verified",
        "dispatch_blocked",
        "dispatch_blocked_large_damage",
        "dispatch_blocked_requirement_check",
        # UI input: every click attempt and its outcome
        "continuous_ui_click_attempted",
        "continuous_ui_click_sent",
        "continuous_ui_click_ambiguous",
        "continuous_ui_supply_tab_click_sent",
        "continuous_ui_supply_click_sent",
        "continuous_ui_dispatch_input_completed",
        "continuous_ui_dispatch_blocked",
        "continuous_ui_settlement_step_started",
        "continuous_ui_settlement_step_completed",
        # Other visible UI workflows: the audit row must precede every click.
        "ui_refresh_click_attempted",
        "ui_refresh_click_sent",
        "expedition_list_click_attempted",
        "expedition_list_click_sent",
        "home_port_recovery_click_attempted",
        "home_port_recovery_click_sent",
        # settlement drain (P0.1)
        "settlement_drain_action",
        "settlement_drain_finished",
        # settlement / resupply outcomes that gate the next action
        "settlement_blocked",
        "startup_settlement_refused",
        "resupply_confirmed",
        "resupply_blocked",
        "blocked_fleet_recovered",
        # deliberate stop
        "supervisor_paused",
    }
)


class AuditLogUnavailable(RuntimeError):
    """A critical audit event could not be written; the caller must stop."""


def is_critical_event(event: object) -> bool:
    """True when ``event`` must be durable before the action it describes."""
    return isinstance(event, str) and event in CRITICAL_AUDIT_EVENTS


def audit_gate(sink: Any) -> Callable[[], None] | None:
    """Return a zero-argument guard that raises when the sink is unusable.

    Returns ``None`` when the sink offers no health probe at all (for example a
    plain SQLite sink or a test double). That is deliberate: a sink without a
    probe is not silently treated as healthy, it is simply not the run log, and
    the caller's own durability contract applies instead.
    """
    probe = getattr(sink, "ensure_writable", None)
    return probe if callable(probe) else None
