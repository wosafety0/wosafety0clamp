"""Trusted task attribution for append-only event logs.

An event's task identity is a fact owned by the composition root (the process
that opened the log), NOT by the code that happens to emit one line. A caller
may add arbitrary diagnostic fields, but it must never be able to claim,
replace or forge the attribution of a row in a shared log.

Two rules, enforced by pure functions so both the SQLite sink and the jsonl
printer share exactly one contract:

1. ``apply_trusted_task_attribution`` strips every caller-supplied attribution
   key and writes the trusted ``task_kind`` supplied at construction time.
2. ``merge_trusted_event_fields`` merges caller extras FIRST and the trusted
   envelope LAST, so ``**extra`` can never overwrite ``event``, ``run_id``,
   ``timestamp_utc`` or any attribution key.

Backward compatibility: when ``task_kind is None`` (a legacy sink with no
trusted identity) the payload is returned unchanged, byte for byte. Composition
roots for real runs must always supply a task_kind. See
docs/DESIGN_task_event_attribution.md for the schema evaluation.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# The three structured fields produced by task_scope.task_event_fields(). They
# are reserved: callers cannot set them through ``fields`` / ``**extra``.
TASK_ATTRIBUTION_KEYS: frozenset[str] = frozenset(
    {"task_kind", "task_implemented", "task_ui_click_capable"}
)

# Envelope keys that identify one emitted event. Also reserved.
EVENT_ENVELOPE_KEYS: frozenset[str] = frozenset(
    {"timestamp_utc", "run_id", "event"}
)

TRUSTED_EVENT_KEYS: frozenset[str] = TASK_ATTRIBUTION_KEYS | EVENT_ENVELOPE_KEYS


def apply_trusted_task_attribution(
    fields: Mapping[str, Any], *, task_kind: str | None
) -> dict[str, Any]:
    """Return a payload dict whose attribution can only come from the sink.

    With ``task_kind=None`` the mapping is returned unchanged (legacy payloads
    stay byte-for-byte identical). Otherwise every caller-supplied attribution
    key is discarded and the trusted ``task_kind`` is written explicitly.
    """
    if task_kind is None:
        return dict(fields)
    cleaned = {
        key: value
        for key, value in fields.items()
        if key not in TASK_ATTRIBUTION_KEYS
    }
    cleaned["task_kind"] = task_kind
    return cleaned


def merge_trusted_event_fields(
    extra: Mapping[str, Any], *, trusted: Mapping[str, Any]
) -> dict[str, Any]:
    """Build one event payload with trusted fields winning unconditionally.

    Caller extras are copied first; any key that collides with
    ``TRUSTED_EVENT_KEYS`` is dropped, then the trusted envelope is written.
    The result is independent of dict insertion order at the call site.
    """
    cleaned = {
        key: value
        for key, value in extra.items()
        if key not in TRUSTED_EVENT_KEYS
    }
    for key, value in trusted.items():
        if key in TASK_ATTRIBUTION_KEYS or key in EVENT_ENVELOPE_KEYS:
            cleaned[key] = value
        else:
            raise ValueError(f"trusted envelope contains an unreserved key: {key!r}")
    return cleaned


__all__ = [
    "TASK_ATTRIBUTION_KEYS",
    "EVENT_ENVELOPE_KEYS",
    "TRUSTED_EVENT_KEYS",
    "apply_trusted_task_attribution",
    "merge_trusted_event_fields",
]
