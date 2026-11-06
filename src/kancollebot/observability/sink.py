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
from dataclasses import dataclass
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
    "CorrelationCapability",
    "CorrelationSnapshot",
    "RunLogSink",
    "TeeEventSink",
    "build_run_log_sink",
    "capture_correlation",
    "correlation_capability",
    "correlation_scope",
]

DEFAULT_PRUNE_INTERVAL_SECONDS = 3600.0


@dataclass(frozen=True)
class CorrelationCapability:
    """Whether a sink can actually bind correlation identity, and why.

    Presence of an attribute is not the question. ``correlation`` may exist and
    be ``None``, and a tee can offer the method while none of its members can
    bind anything - both would make every ``dispatch_id`` null while a reader
    has no way to tell degradation from a dropped scope. This record is the
    answer, and it is written into the run's first row so every later null has
    a stated premise.
    """

    capable: bool
    sources: tuple[str, ...]
    reason: str

    def as_fields(self) -> dict[str, object]:
        return {
            "correlation_capable": self.capable,
            "correlation_sources": list(self.sources),
        }


def correlation_capability(sink: Any) -> CorrelationCapability:
    """Report whether ``sink`` can bind correlation, by real capability.

    * a tee is capable when at least one member sink is, because it fans the
      scope out to every member that accepts it;
    * an object is capable when its ``correlation`` attribute is *callable* -
      an attribute that exists and is ``None`` is not;
    * a plain function (the shape most tools and tests use) is not capable.
    """
    if isinstance(sink, TeeEventSink):
        members = tuple(correlation_capability(item) for item in sink.sinks)
        capable = [member for member in members if member.capable]
        if not capable:
            return CorrelationCapability(
                False, (), "no member sink of this tee can bind correlation"
            )
        sources = tuple(
            source for member in capable for source in member.sources
        )
        return CorrelationCapability(True, sources, "at least one tee member can bind correlation")
    scope = getattr(sink, "correlation", None)
    if not callable(scope):
        return CorrelationCapability(
            False, (), "sink exposes no callable correlation scope"
        )
    return CorrelationCapability(
        True, (type(sink).__name__,), "sink exposes a callable correlation scope"
    )


@dataclass(frozen=True)
class CorrelationSnapshot:
    """Identity captured on one thread, replayable on another - by hand only.

    ``contextvars`` do not cross a thread boundary. A scope entered on the
    supervisor's thread is invisible to a thread that thread starts: the worker
    sees the sink's base context (``run_id`` / ``task_kind``) and null in every
    slot the scope had filled. That is a property of the runtime, and this
    repository leans on it rather than fighting it - the background threads
    (CDP reader, port snapshot provider, the observers) never share the
    ``event_sink``, so there is no identity for them to inherit. Nothing below
    is wired into them, and nothing should be.

    For the narrow case where the *same* sink is deliberately handed to work on
    another thread, the identity has to be carried explicitly: capture it with
    :func:`capture_correlation` on the thread that owns it, then ``rebind()`` it
    inside the thread that needs it. A silent global propagation would attach
    one thread's ``dispatch_id`` to another thread's rows, which is a false
    correlation claim - worse than a null.
    """

    context: CorrelationContext | None
    capable: bool
    reason: str

    def updates(self) -> dict[str, object]:
        """The slots worth replaying: only the ones the context actually has."""
        context = self.context
        if context is None:
            return {}
        return {
            name: getattr(context, name)
            for name in (
                "dispatch_id",
                "action_id",
                "battle_id",
                "fleet_id",
                "correlation_id",
                "source",
                "observed_at",
            )
            if getattr(context, name) is not None
        }

    @contextmanager
    def rebind(self, sink: Any) -> Iterator[CorrelationContext | None]:
        """Re-enter the captured identity on ``sink`` for this block only.

        Explicit by construction: the caller of the new thread asks for it, and
        the scope closes when the block does. A snapshot with no filled slot
        (nothing was in force) rebinds nothing and yields ``None`` rather than
        pretending a scope exists.
        """
        updates = self.updates()
        if not updates:
            yield None
            return
        with correlation_scope(sink, **updates) as effective:
            yield effective


def capture_correlation(sink: Any) -> CorrelationSnapshot:
    """Read the identity currently in force on ``sink``, without changing it.

    Capability and readability are two different questions, and both are
    answered here instead of being guessed at a call site:

    * a sink that binds correlation *and* publishes the value (``RunLogSink``,
      and a tee whose member does) yields it;
    * a sink that binds correlation but publishes nothing yields ``None`` with
      ``capable=True`` and a reason saying so - the caller must capture the
      identity where it is minted, not assume the absence means "no scope";
    * a sink that cannot bind anything yields ``None`` with ``capable=False``.
    """
    capability = correlation_capability(sink)
    context = getattr(sink, "context", None)
    if isinstance(context, CorrelationContext):
        return CorrelationSnapshot(context, capability.capable, capability.reason)
    if capability.capable:
        return CorrelationSnapshot(
            None,
            True,
            "sink binds correlation but publishes no readable context; "
            "capture the identity where it is minted instead",
        )
    return CorrelationSnapshot(None, False, capability.reason)



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
        if critical and self._degraded_reason is not None:
            # A later successful append cannot repair an earlier missing row.
            # Refuse every subsequent critical event in this run.
            raise AuditLogUnavailable(
                f"run log is degraded: {self._degraded_reason}"
            )
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
            if self._degraded_reason is None:
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
        # Deliberately do not clear _degraded_reason after a successful write:
        # the missing row remains a permanent gap in this run's audit trail.
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
        """Record that this process started, and with which identity.

        ``correlation_capable`` is written here because it is the one fact that
        makes every later ``dispatch_id: null`` interpretable: with it, a null
        means "this event had no dispatch" or "this scope was dropped"; without
        it, a null could equally mean "this run cannot correlate at all". The
        value defaults to this sink's real capability and may be overridden by
        the composition root, which knows *which* object emits the events (a tee
        whose members differ, for instance).
        """
        payload: dict[str, Any] = {
            "run_id": self.run_id,
            "pid": os.getpid(),
        }
        for key, value in (fields or {}).items():
            payload.setdefault(str(key), value)
        # This sink's own capability is the fallback. An explicit value from the
        # composition root wins because it describes the object the components
        # actually emit through (a tee, typically) - and it is produced by the
        # same function, never typed by hand.
        payload = {**correlation_capability(self).as_fields(), **payload}
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
        if is_critical_event(event):
            # Check every capable sink before the primary write. A known-degraded
            # secondary run log must not leave a misleading SQLite-only row.
            self.ensure_writable()
        self._primary(event, fields)
        for sink in self._others:
            sink(event, fields)

    @property
    def sinks(self) -> tuple[Callable[[str, Mapping[str, Any]], None], ...]:
        """Every member, primary first. Read-only; used for capability probing."""
        return (self._primary, *self._others)

    @property
    def run_id(self) -> str | None:
        return self._first_attribute("run_id")

    @property
    def context(self) -> CorrelationContext | None:
        """The identity in force, read back from a member that publishes one.

        A tee owns no identity: it fans a scope out to the members that can
        bind one. Reporting the *first* member's value is exact for the
        production composition (one run-log sink behind the ledger) and honest
        for any other - the value comes from a real sink and is never
        synthesised here. ``None`` means no member publishes one, which is a
        different fact from "no member could bind it"; ask
        :func:`correlation_capability` for the latter.
        """
        for sink in (self._primary, *self._others):
            context = getattr(sink, "context", None)
            if isinstance(context, CorrelationContext):
                return context
        return None

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
    def correlation(self, **updates: object) -> Iterator[CorrelationContext | None]:
        """Enter every sub-sink's correlation scope that supports one.

        Yields the identity that actually took effect, taken from the first
        member that reports one. Handing it back is not decoration: a caller
        that gets nothing cannot tell "every member dropped this scope" from
        "the scope was applied to a member that does not publish it", and those
        two cases call for different recovery. ``None`` here means "no member
        published an effective context" - use :func:`correlation_capability`
        when the question is whether any member could bind at all.
        """
        with ExitStack() as stack:
            effective: CorrelationContext | None = None
            for sink in (self._primary, *self._others):
                scope = getattr(sink, "correlation", None)
                if not callable(scope):
                    continue
                bound = stack.enter_context(scope(**updates))
                if effective is None and isinstance(bound, CorrelationContext):
                    effective = bound
            yield effective


@contextmanager
def correlation_scope(
    sink: Any, **updates: object
) -> Iterator[CorrelationContext | None]:
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

    Yields the effective :class:`CorrelationContext`, or ``None`` when no scope
    was entered. A caller that needs the identity of the block (to log it, to
    hand it to another thread, to assert it took) reads it from here instead of
    re-deriving it - and a ``None`` result is the signal that this sink cannot
    carry one.

    The scope lives in a ``ContextVar``, so it does **not** reach a thread
    started inside the block. That is intended: see
    :class:`CorrelationSnapshot` for the explicit, opt-in way to carry it.
    """
    scope = getattr(sink, "correlation", None)
    if not callable(scope):
        yield None
        return
    with scope(**updates) as effective:
        yield effective if isinstance(effective, CorrelationContext) else None


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
