"""Unified, bounded, three-day rolling run log for KancolleBot.

Layers, outermost first:

* :mod:`~kancollebot.observability.sink` - ``RunLogSink`` (a drop-in
  ``event_sink``), ``TeeEventSink`` (SQLite ledger + run log, unchanged
  behaviour for every existing emitter) and ``correlation_scope`` (how the
  composition root stamps one dispatch attempt onto every row it causes).
* :mod:`~kancollebot.observability.run_log` - JSONL storage, day rotation, file
  and directory caps, three-day retention, atomic current-state snapshot.
* :mod:`~kancollebot.observability.event_envelope` - the one event format and
  the correlation identifiers (``run_id`` / ``dispatch_id`` / ``action_id`` /
  ``battle_id`` / ``event_id`` / ``correlation_id``).
* :mod:`~kancollebot.observability.audit_policy` - which events are
  safety-critical, and the fail-closed behaviour when one cannot be written.

Importing this package is cheap and adapter-free; ``sink`` is what pulls in the
shared CDP redaction rules, and only when it is actually used.
"""
from __future__ import annotations

from kancollebot.observability.audit_policy import (
    CRITICAL_AUDIT_EVENTS,
    AuditLogUnavailable,
    audit_gate,
    is_critical_event,
)
from kancollebot.observability.event_envelope import (
    CONTEXT_FIELDS,
    DESCRIBED_FIELDS,
    ENVELOPE_FIELDS,
    IDENTITY_ONLY_FIELDS,
    RESERVED_FIELDS,
    SINK_OWNED_FIELDS,
    TRUSTED_ONLY_FIELDS,
    CorrelationContext,
    build_event_record,
    new_action_id,
    new_battle_id,
    new_correlation_id,
    new_dispatch_id,
    new_event_id,
    new_run_id,
    validate_event_name,
)
from kancollebot.observability.sink import (  # noqa: F401 - re-exported API
    CorrelationSnapshot,
    RunLogSink,
    TeeEventSink,
    capture_correlation,
    correlation_capability,
    correlation_scope,
)
from kancollebot.observability.run_log import (
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_MAX_TOTAL_BYTES,
    DEFAULT_RETENTION_DAYS,
    PruneReport,
    RunLogError,
    RunLogFile,
    RunLogPathError,
    RunLogPolicy,
    RunLogStore,
    SnapshotWriteError,
    summarise_events,
)

__all__ = [
    "CONTEXT_FIELDS",
    "CRITICAL_AUDIT_EVENTS",
    "DESCRIBED_FIELDS",
    "IDENTITY_ONLY_FIELDS",
    "SINK_OWNED_FIELDS",
    "RunLogSink",
    "TeeEventSink",
    "CorrelationSnapshot",
    "capture_correlation",
    "correlation_capability",
    "correlation_scope",
    "DEFAULT_MAX_FILE_BYTES",
    "DEFAULT_MAX_TOTAL_BYTES",
    "DEFAULT_RETENTION_DAYS",
    "ENVELOPE_FIELDS",
    "RESERVED_FIELDS",
    "TRUSTED_ONLY_FIELDS",
    "AuditLogUnavailable",
    "CorrelationContext",
    "PruneReport",
    "RunLogError",
    "RunLogFile",
    "RunLogPathError",
    "RunLogPolicy",
    "RunLogStore",
    "SnapshotWriteError",
    "audit_gate",
    "build_event_record",
    "is_critical_event",
    "new_action_id",
    "new_battle_id",
    "new_correlation_id",
    "new_dispatch_id",
    "new_event_id",
    "new_run_id",
    "summarise_events",
    "validate_event_name",
]
