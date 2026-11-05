"""The run-log sink: one callable that every existing ``event_sink`` can use.

Composition roots already inject ``event_sink: Callable[[str, Mapping], None]``
into the monitor, the dispatch controller, the settlement controller, the
resupply controller, the visible-UI refresh driver and the UI driver. This module
supplies an object with exactly that signature, so unified logging needs **no
change to any existing emitter**: the same twenty-odd ``continuous_ui_*`` and
``dispatch_*`` events that used to land only in SQLite now also land in the
three-day rolling JSONL with a correlation envelope.

Failure semantics live here, not in the emitters:

* an ordinary event that cannot be written sets ``degraded_reason``, calls
  ``on_error`` and returns - the run continues without that diagnostic;
* a critical event (see :mod:`kancollebot.observability.audit_policy`) is
  fsynced and raises :class:`AuditLogUnavailable` on failure. Because call sites
  emit their critical event before the input they describe, the exception stops
  the click / dispatch / settlement step.

``TeeEventSink`` keeps the SQLite append-only ledger exactly where it is and
adds the run log next to it; the supervisor's "one shared sink" identity check
still passes because there is still exactly one object.
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kancollebot.observability.audit_policy import (
    AuditLogUnavailable,
    is_critical_event,
)
from kancollebot.observability.event_envelope import (
    RESERVED_FIELDS,
    CorrelationContext,
    build_event_record,
    new_event_id,
    new_run_id,
)
from kancollebot.observability.redaction import redact_event_record
from kancollebot.observability.run_log import (
    RunLogPolicy,
    RunLogStore,
    SnapshotWriteError,
)

__all__ = [
    "RunLogSink",
    "TeeEventSink",
    "build_run_log_sink",
    "correlation_scope",
]

DEFAULT_PRUNE_INTERVAL_SECONDS = 3600.0


class RunLogSink:
    """Emit unified events into one rolling JSONL run log."""

    def __init__(
        self,
        store: RunLogStore,
        *,
        context: CorrelationContext,
        on_error: Callable[[str, BaseException], None] | None = None,
        redact: bool = True,
        prune_interval_seconds: float = DEFAULT_PRUNE_INTERVAL_SECONDS,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        if not isinstance(store, RunLogStore):
            raise ValueError("store must be a RunLogStore")
        if not isinstance(context, CorrelationContext):
            raise ValueError("context must be a CorrelationContext")
        if on_error is not None and not callable(on_error):
            raise ValueError("on_error must be callable or None")
        if isinstance(prune_interval_seconds, bool) or not isinstance(
            prune_interval_seconds, (int, float)
        ):
            raise ValueError("prune_interval_seconds must be a number")
        if prune_interval_seconds < 0:
            raise ValueError("prune_interval_seconds must not be negative")
        self._store = store
        self._base_context = context
        self._on_error = on_error
        self._redact = bool(redact)
        self._prune_interval = float(prune_interval_seconds)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic or time.monotonic
        # One context variable per sink instance: two sinks in one process (a
        # test double and the real log) must not share a correlation scope.
        self._context_var: ContextVar[CorrelationContext | None] = ContextVar(
            f"kancollebot_run_log_context_{id(self)}", default=None
        )
        self._degraded_reason: str | None = None
        self._last_prune_at: float | None = None
        self._emitting_prune = False
        self._last_snapshot_error: str | None = None
        self._writes = 0

    # -- identity ----------------------------------------------------------

    @property
    def run_id(self) -> str:
        return self._base_context.run_id

    @property
    def directory(self) -> Path:
        return self._store.directory

    @property
    def policy(self) -> RunLogPolicy:
        return self._store.policy

    @property
    def task_kind(self) -> str:
        return self._base_context.task_kind

    @property
    def context(self) -> CorrelationContext:
        return self._context_var.get() or self._base_context

    @property
    def degraded_reason(self) -> str | None:
        """Why the last write failed, or ``None`` when the log is healthy."""
        return self._degraded_reason

    @property
    def last_snapshot_error(self) -> str | None:
        return self._last_snapshot_error

    @property
    def writes(self) -> int:
        return self._writes

    # -- correlation -------------------------------------------------------

    @contextmanager
    def correlation(self, **updates: object) -> Iterator[CorrelationContext]:
        """Scope every event emitted inside the block to the given identity.

        Used to bind one dispatch or one action: ``with sink.correlation(
        dispatch_id=..., fleet_id=4): ...`` makes every nested click and
        settlement row carry the same ``dispatch_id`` without any emitter
        knowing that correlation exists.
        """
        current = self.context
        scoped = current.with_(**updates)
        token = self._context_var.set(scoped)
        try:
            yield scoped
        finally:
            self._context_var.reset(token)

    # -- emit --------------------------------------------------------------

    def __call__(self, event: str, fields: Mapping[str, Any]) -> None:
        critical = is_critical_event(event)
        try:
            if not isinstance(fields, Mapping):
                raise TypeError("event fields must be a mapping")
            record = build_event_record(
                event=event,
                fields=fields,
                context=self.context,
                event_id=new_event_id(now=self._clock()),
                timestamp_utc=self._clock().isoformat(),
            )
            if self._redact:
                record = redact_event_record(record, envelope_fields=RESERVED_FIELDS)
            self._store.append(record, durable=critical)
        except Exception as exc:
            self._degraded_reason = f"{type(exc).__name__}: {exc}"
            self._notify(event, exc)
            if critical:
                # Fail closed: the caller emits this event before the click it
                # describes, so the exception is what stops the action.
                raise AuditLogUnavailable(
                    f"critical audit event {event!r} could not be written: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            return
        self._writes += 1
        self._degraded_reason = None
        self._maybe_prune()

    def _notify(self, event: str, exc: BaseException) -> None:
        if self._on_error is None:
            return
        try:
            self._on_error(event, exc)
        except Exception:  # pragma: no cover - a reporter must not mask the cause
            pass

    # -- retention ---------------------------------------------------------

    def _maybe_prune(self) -> None:
        if self._prune_interval <= 0 or self._emitting_prune:
            return
        now = self._monotonic()
        if self._last_prune_at is not None and now - self._last_prune_at < self._prune_interval:
            return
        self._last_prune_at = now
        self._emitting_prune = True
        try:
            report = self._store.prune()
        except Exception as exc:  # pragma: no cover - prune() is already total
            self._notify("_prune_failed", exc)
            return
        finally:
            self._emitting_prune = False
        # The cleanup reports on itself; a failed cleanup is an explicit
        # warning row, never a silent success.
        self._emit_prune_report(report)

    def _emit_prune_report(self, report: Any) -> None:
        try:
            record = build_event_record(
                event="run_log_prune_completed" if report.ok else "run_log_prune_failed",
                fields={
                    "removed": list(report.removed),
                    "removed_count": len(report.removed),
                    "kept_files": report.kept,
                    "total_bytes": report.total_bytes,
                    "errors": list(report.errors),
                    "retention_days": self._store.policy.retention_days,
                    "max_total_bytes": self._store.policy.max_total_bytes,
                    "max_file_bytes": self._store.policy.max_file_bytes,
                },
                context=self.context,
                event_id=new_event_id(now=self._clock()),
                timestamp_utc=self._clock().isoformat(),
            )
            self._store.append(record)
        except Exception as exc:  # pragma: no cover - reported, never raised
            self._degraded_reason = f"{type(exc).__name__}: {exc}"
            self._notify("run_log_prune_completed", exc)

    # -- health ------------------------------------------------------------

    def ensure_writable(self) -> None:
        """Raise unless the log can be written right now.

        Called by a caller that is about to perform an irreversible action. Two
        independent conditions refuse it:

        * the sink already failed to write something (``degraded_reason``). That
          state is NOT auto-cleared by a later successful probe: a log that lost
          one row has an unprovable gap, and the safe response is an operator
          restart with a new ``run_id``, not silently continuing;
        * the log cannot be opened for append right now.

        There is deliberately no retry: a retry that eventually succeeded would
        make "the log says it worked" and "the action happened" two different
        claims about the same moment.
        """
        if self._degraded_reason is not None:
            raise AuditLogUnavailable(
                f"run log is degraded: {self._degraded_reason}"
            )
        try:
            self._store.probe()
        except Exception as exc:
            self._degraded_reason = f"{type(exc).__name__}: {exc}"
            self._notify("_probe_failed", exc)
            raise AuditLogUnavailable(
                f"run log is not writable: {type(exc).__name__}: {exc}"
            ) from exc

    # -- lifecycle ---------------------------------------------------------

    def start(self, fields: Mapping[str, Any] | None = None) -> None:
        """Record that this process started, and with which identity."""
        payload: dict[str, Any] = {
            "run_id": self.run_id,
            "pid": os.getpid(),
        }
        for key, value in (fields or {}).items():
            payload.setdefault(str(key), value)
        self("run_started", payload)

    def stop(self, reason: str, fields: Mapping[str, Any] | None = None) -> None:
        """Record why this process stopped. Never raises (``finally``-safe)."""
        payload: dict[str, Any] = {"reason": reason}
        for key, value in (fields or {}).items():
            if key in payload:
                continue
            payload[str(key)] = value
        try:
            self("run_stopped", payload)
        except Exception as exc:  # pragma: no cover - stop must not mask a crash
            self._notify("run_stopped", exc)

    # -- snapshot ----------------------------------------------------------

    def write_snapshot(self, payload: Mapping[str, Any]) -> None:
        """Replace the current-state snapshot atomically, or raise."""
        if not isinstance(payload, Mapping):
            raise TypeError("snapshot payload must be a mapping")
        document: dict[str, Any] = {
            "run_id": self.run_id,
            "task_kind": self.task_kind,
            "snapshot_updated_at_utc": self._clock().isoformat(),
        }
        for key, value in payload.items():
            if key in document:
                continue
            document[str(key)] = value
        try:
            self._store.write_snapshot(document)
        except SnapshotWriteError as exc:
            self._last_snapshot_error = str(exc)
            self._notify("_snapshot_failed", exc)
            raise
        self._last_snapshot_error = None


class TeeEventSink:
    """Fan one event out to several sinks, primary first.

    The first sink is the primary: its exceptions propagate exactly as before, so
    the existing SQLite behaviour is unchanged. The run log is added after it.
    A critical-event failure in *either* sink aborts the action, because both are
    emitted before the input.
    """

    def __init__(self, primary: Callable[[str, Mapping[str, Any]], None], *others: Callable[[str, Mapping[str, Any]], None]) -> None:
        for sink in (primary, *others):
            if not callable(sink):
                raise ValueError("every sink must be callable")
        self._primary = primary
        self._others = tuple(others)

    def __call__(self, event: str, fields: Mapping[str, Any]) -> None:
        self._primary(event, fields)
        for sink in self._others:
            sink(event, fields)

    @property
    def run_id(self) -> str | None:
        return self._first_attribute("run_id")

    def _first_attribute(self, name: str) -> Any:
        for sink in (self._primary, *self._others):
            value = getattr(sink, name, None)
            if value is not None:
                return value
        return None

    def ensure_writable(self) -> None:
        """Probe every sink that publishes a probe; raise on the first failure."""
        problems: list[str] = []
        for sink in (self._primary, *self._others):
            probe = getattr(sink, "ensure_writable", None)
            if not callable(probe):
                continue
            try:
                probe()
            except Exception as exc:
                problems.append(f"{type(sink).__name__}: {exc}")
        if problems:
            raise AuditLogUnavailable("; ".join(problems))

    @contextmanager
    def correlation(self, **updates: object) -> Iterator[None]:
        """Enter every sub-sink's correlation scope that supports one."""
        with ExitStack() as stack:
            for sink in (self._primary, *self._others):
                scope = getattr(sink, "correlation", None)
                if callable(scope):
                    stack.enter_context(scope(**updates))
            yield


@contextmanager
def correlation_scope(
    sink: Any, **updates: object
) -> Iterator[None]:
    """Bind correlation identity on ``sink`` for the duration of the block.

    The composition root - not the emitter and not the business object - owns
    the identity of *one attempt*, so this helper exists to be called from
    exactly one place per attempt: the runtime that starts it.

    A sink that cannot express correlation (a plain function, a test double, the
    SQLite ledger) is left alone rather than rejected. That keeps every existing
    call site and every offline test working unchanged, and it makes the
    degraded case explicit in the code rather than hidden in a ``getattr`` at
    each call site: no scope means the envelope's ``dispatch_id`` stays null,
    which a reader can see.
    """
    scope = getattr(sink, "correlation", None)
    if not callable(scope):
        yield
        return
    with scope(**updates):
        yield


def build_run_log_sink(
    *,
    directory: str | Path | None = None,
    task_kind: str,
    run_id: str | None = None,
    policy: RunLogPolicy | None = None,
    context: CorrelationContext | None = None,
    on_error: Callable[[str, BaseException], None] | None = None,
    redact: bool = True,
    prune_interval_seconds: float = DEFAULT_PRUNE_INTERVAL_SECONDS,
) -> RunLogSink:
    """Compose a sink for one run.

    ``run_id`` defaults to a fresh identifier, so two processes never share one
    run identity even when they write into the same directory.
    """
    if policy is None:
        if directory is None:
            raise ValueError("either policy or directory is required")
        policy = RunLogPolicy(directory=directory)
    if context is None:
        context = CorrelationContext(run_id=run_id or new_run_id(), task_kind=task_kind)
    store = RunLogStore(policy)
    return RunLogSink(
        store,
        context=context,
        on_error=on_error,
        redact=redact,
        prune_interval_seconds=prune_interval_seconds,
    )
