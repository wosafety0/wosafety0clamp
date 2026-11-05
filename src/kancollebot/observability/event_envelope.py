"""Unified event envelope and correlation identifiers for the run log.

One event is one JSON object. Its *identity* fields are owned by the runtime,
not by the code that happens to emit a line:

* ``timestamp_utc``, ``event``, ``run_id``, ``task_kind`` and ``event_id`` are
  written by the sink and are **never** taken from an emitter. An emitter that
  passes one of those keys has it dropped, so a shared log cannot be forged.
* ``dispatch_id``, ``action_id``, ``battle_id``, ``fleet_id``, ``correlation_id``,
  ``source`` and ``observed_at`` are owned by the *correlation context*. When the
  context has a value it wins. When it has none, an emitter-supplied value is
  promoted into the envelope slot instead of being discarded - the historical
  payloads of this repository already carry ``fleet_id``/``observed_at`` in the
  body, and silently dropping them would be a data loss, not a safety win.

The distinction matters for the safety argument: a caller can never claim *which
run* or *which event* a line belongs to, and can never overwrite the identity a
composition root injected. It may still describe the object it acted on.

Nothing here reads configuration, opens files or imports adapters, so the
package stays importable from ``runtime_config`` and from ``core``.
"""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

__all__ = [
    "CONTEXT_FIELDS",
    "DESCRIBED_FIELDS",
    "ENVELOPE_FIELDS",
    "IDENTITY_ONLY_FIELDS",
    "RESERVED_FIELDS",
    "SINK_OWNED_FIELDS",
    "TRUSTED_ONLY_FIELDS",
    "CorrelationContext",
    "build_event_record",
    "new_action_id",
    "new_battle_id",
    "new_correlation_id",
    "new_dispatch_id",
    "new_event_id",
    "new_run_id",
    "validate_event_name",
]

#: Envelope order as it appears on disk. Readers may rely on the names, never
#: on the order - but a stable order keeps a human-scanned log readable and
#: makes diffing two runs of the same code straightforward.
ENVELOPE_FIELDS: tuple[str, ...] = (
    "timestamp_utc",
    "event",
    "run_id",
    "task_kind",
    "event_id",
    "sqlite_event_id",
    "dispatch_id",
    "action_id",
    "battle_id",
    "fleet_id",
    "source",
    "observed_at",
    "correlation_id",
)

#: Fields the sink alone writes: they identify the row itself.
SINK_OWNED_FIELDS: frozenset[str] = frozenset(
    {"timestamp_utc", "event", "run_id", "task_kind", "event_id", "sqlite_event_id"}
)

#: Correlation identity: only the runtime may mint these. An emitter-supplied
#: value is dropped, not promoted. No production emitter passes any of them
#: (verified by search), so refusing them costs no compatibility and removes the
#: one way a caller could claim "this row belongs to dispatch X" without the
#: composition root having said so.
IDENTITY_ONLY_FIELDS: frozenset[str] = frozenset(
    {"dispatch_id", "action_id", "battle_id", "correlation_id"}
)

#: Fields an emitter may never supply. Kept as one name for readers that ask
#: "what can I not forge"; the two subsets above explain *why* each is refused.
TRUSTED_ONLY_FIELDS: frozenset[str] = SINK_OWNED_FIELDS | IDENTITY_ONLY_FIELDS

#: Envelope slots the correlation context may fill (everything except the five
#: the sink writes itself).
CONTEXT_FIELDS: tuple[str, ...] = tuple(
    field for field in ENVELOPE_FIELDS if field not in SINK_OWNED_FIELDS
)

#: Slots an emitter may still *describe* when the context has none. Existing
#: payloads already carry ``fleet_id`` and ``observed_at``, so dropping these
#: would be data loss rather than a safety win.
DESCRIBED_FIELDS: tuple[str, ...] = tuple(
    field for field in CONTEXT_FIELDS if field not in IDENTITY_ONLY_FIELDS
)

RESERVED_FIELDS: frozenset[str] = frozenset(ENVELOPE_FIELDS)

#: Additional envelope slot written next to ``observed_at`` so a reader can see
#: whether the timestamp came from the trusted context or from the emitter.
OBSERVED_AT_ORIGIN_FIELD = "observed_at_origin"

_MAX_EVENT_NAME_LENGTH = 128
_ID_PREFIXES = frozenset(
    {"run", "dispatch", "action", "battle", "event", "corr"}
)


def validate_event_name(event: object) -> str:
    """Return ``event`` if it is a safe single-token name, else raise.

    A newline or a control character would let one event break the
    one-line-per-event contract that makes a JSONL file survivable when a
    single line is truncated; that is a correctness requirement, not a style
    preference.
    """
    if not isinstance(event, str):
        raise ValueError(f"event must be a str, got {type(event).__name__}")
    name = event.strip()
    if not name:
        raise ValueError("event must not be empty")
    if len(name) > _MAX_EVENT_NAME_LENGTH:
        raise ValueError(f"event must be at most {_MAX_EVENT_NAME_LENGTH} characters")
    if name != event:
        raise ValueError("event must not have leading or trailing whitespace")
    if any(character.isspace() or ord(character) < 0x20 for character in name):
        raise ValueError("event must not contain whitespace or control characters")
    return name


def _validate_id(name: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    if any(character.isspace() or ord(character) < 0x20 for character in value):
        raise ValueError(f"{name} must not contain whitespace or control characters")
    return value


def _new_id(prefix: str, *, now: datetime | None = None) -> str:
    if prefix not in _ID_PREFIXES:
        raise ValueError(f"unsupported identifier prefix: {prefix!r}")
    moment = now if now is not None else datetime.now(timezone.utc)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    stamp = moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:12]}"


def new_run_id(*, now: datetime | None = None) -> str:
    """A new identifier for one program run instance."""
    return _new_id("run", now=now)


def new_dispatch_id(*, now: datetime | None = None) -> str:
    """A new identifier for one dispatch attempt (never derived from its data)."""
    return _new_id("dispatch", now=now)


def new_action_id(*, now: datetime | None = None) -> str:
    """A new identifier for one UI or business action."""
    return _new_id("action", now=now)


def new_battle_id(*, now: datetime | None = None) -> str:
    """Reserved for the future combat log; unused by expedition code today."""
    return _new_id("battle", now=now)


def new_event_id(*, now: datetime | None = None) -> str:
    """A new identifier for one emitted event row."""
    return _new_id("event", now=now)


def new_correlation_id(*, now: datetime | None = None) -> str:
    """A new cross-event correlation identifier (not a dedupe key)."""
    return _new_id("corr", now=now)


def _isoformat(value: object) -> object:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        return value.astimezone(timezone.utc).isoformat()
    return value


#: Returned by :func:`_coerce_fleet_id` when a value must not enter the
#: envelope. A distinct object (not ``None``) because ``None`` is a legal
#: "this fleet is unknown" value elsewhere in the code base.
_ABSENT = object()


def _coerce_fleet_id(value: object) -> object:
    """Return ``value`` when it is a positive int, else :data:`_ABSENT`.

    ``bool`` is an ``int`` subclass and ``"4"`` is not ``4``; both would make
    the envelope's ``fleet_id`` column disagree with itself across rows.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return _ABSENT
    return value


@dataclass(frozen=True, slots=True)
class CorrelationContext:
    """Identity that every event emitted while this context is active inherits.

    ``run_id`` and ``task_kind`` are mandatory: an event that cannot say which
    run and which task produced it cannot be used for recovery. The remaining
    slots are optional because a startup log line legitimately has no dispatch
    and a monitor tick legitimately has no action.
    """

    run_id: str
    task_kind: str
    dispatch_id: str | None = None
    action_id: str | None = None
    battle_id: str | None = None
    fleet_id: int | None = None
    correlation_id: str | None = None
    source: str | None = None
    observed_at: object | None = None

    def __post_init__(self) -> None:
        _validate_id("run_id", self.run_id)
        _validate_id("task_kind", self.task_kind)
        for name in ("dispatch_id", "action_id", "battle_id", "correlation_id"):
            value = getattr(self, name)
            if value is not None:
                _validate_id(name, value)
        if self.fleet_id is not None:
            if isinstance(self.fleet_id, bool) or not isinstance(self.fleet_id, int):
                raise ValueError("fleet_id must be an int")
            if self.fleet_id < 1:
                raise ValueError("fleet_id must be positive")
        if self.source is not None:
            _validate_id("source", self.source)
        if self.observed_at is not None:
            _isoformat(self.observed_at)

    @property
    def observed_at_iso(self) -> str | None:
        if self.observed_at is None:
            return None
        return str(_isoformat(self.observed_at))

    def with_(self, **updates: object) -> "CorrelationContext":
        """Return a copy with the given slots replaced.

        ``None`` is a real value here: passing ``dispatch_id=None`` *clears* the
        slot, which is how a caller leaves a dispatch scope without inventing a
        new run identity.
        """
        unknown = set(updates) - set(self.__dataclass_fields__)
        if unknown:
            raise ValueError(f"unknown correlation field(s): {sorted(unknown)}")
        return replace(self, **updates)  # type: ignore[arg-type]


def build_event_record(
    *,
    event: str,
    fields: Mapping[str, Any],
    context: CorrelationContext,
    event_id: str,
    timestamp_utc: str,
    sqlite_event_id: int | None = None,
) -> dict[str, Any]:
    """Build one flat event record: envelope first, diagnostics after.

    The returned dict is JSON-ready for every value that was already
    JSON-ready; the sink is responsible for the final serialisation policy.
    """
    name = validate_event_name(event)
    _validate_id("timestamp_utc", timestamp_utc)
    _validate_id("event_id", event_id)
    if sqlite_event_id is not None and (
        type(sqlite_event_id) is not int or sqlite_event_id < 1
    ):
        raise ValueError("sqlite_event_id must be a positive int or None")
    if not isinstance(fields, Mapping):
        raise TypeError("event fields must be a mapping")

    record: dict[str, Any] = {
        "timestamp_utc": timestamp_utc,
        "event": name,
        "run_id": context.run_id,
        "task_kind": context.task_kind,
        "event_id": event_id,
        "sqlite_event_id": sqlite_event_id,
    }

    origin = {"value": None}
    for field in CONTEXT_FIELDS:
        context_value = getattr(context, field)
        if field == "observed_at":
            context_value = context.observed_at_iso
        if context_value is not None:
            if field == "observed_at":
                origin["value"] = "context"
            record[field] = context_value
            continue
        # Identity slots are context-only: an emitter that passes one has it
        # dropped here, so the envelope can never claim a correlation the
        # runtime did not mint.
        if field not in DESCRIBED_FIELDS or field not in fields:
            continue
        promoted = fields[field]
        if field == "observed_at":
            promoted = _isoformat(promoted)
            origin["value"] = "emitter"
        elif field == "fleet_id":
            promoted = _coerce_fleet_id(promoted)
            if promoted is _ABSENT:
                # A mixed-type identifier column is a silent analysis bug; a
                # malformed fleet id is therefore dropped rather than stored.
                continue
        record[field] = promoted

    if "observed_at" in record:
        record[OBSERVED_AT_ORIGIN_FIELD] = origin["value"]

    for key, value in fields.items():
        if not isinstance(key, str):
            key = str(key)
        if key in RESERVED_FIELDS or key == OBSERVED_AT_ORIGIN_FIELD:
            # Reserved keys were already decided above (trusted value, context
            # value or promotion). A raw copy would reintroduce the forgery.
            continue
        record[key] = _isoformat(value) if key.endswith("_at") else value
    return record
