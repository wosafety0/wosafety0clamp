"""Redaction for run-log event fields.

The rules are the ones this repository already applies to passive CDP network
events (``kancollebot.adapters.poi.cdp_network_events``), reused rather than
re-implemented: a field named like a credential never carries its value, and a
string that mentions ``token=...`` is masked before it is written.

Two independent gates, because they catch different mistakes:

* **field name** - ``api_key``, ``cookie``, ``authorization``, ``session`` ...
  are replaced wholesale, whatever their value type.
* **value text** - a long string, a URL with a credential in the query, or a
  header-like ``token=...`` fragment is masked by the shared redactor.

Long values are truncated. A run log is a decision record, not a payload dump:
keeping a 4 MiB HTML fragment out of it is part of the capacity guarantee.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

__all__ = [
    "REDACTED",
    "redact_event_record",
    "redact_value",
]

REDACTED = "[REDACTED]"

_MAX_TEXT_LENGTH = 512
_MAX_DEPTH = 3
_MAX_ITEMS = 64

_redact_text: Callable[..., str] | None = None
_is_sensitive_name: Callable[[str], bool] | None = None


def _loader() -> tuple[Callable[..., str], Callable[[str], bool]]:
    """Resolve the shared CDP redactors once, lazily.

    Lazy on purpose: this module is imported from the composition-root side of
    the world, and it must not drag ``kancollebot.adapters`` into every import of
    ``kancollebot.observability`` (``runtime_config`` and ``core`` import that
    package).
    """
    global _redact_text, _is_sensitive_name
    if _redact_text is None or _is_sensitive_name is None:
        from kancollebot.adapters.poi.cdp_network_events import (
            is_sensitive_field_name,
            redact_sensitive_text,
        )

        _redact_text = redact_sensitive_text
        _is_sensitive_name = is_sensitive_field_name
    return _redact_text, _is_sensitive_name


def _fallback_text(value: Any, *, limit: int = _MAX_TEXT_LENGTH) -> str:
    return str(value)[:limit]


def redact_value(value: Any, *, depth: int = 0) -> Any:
    """Return a JSON-safe, size-bounded copy of ``value``."""
    try:
        text_redactor, _ = _loader()
    except Exception:
        # If the shared redactor is unavailable, never fall back to writing
        # unredacted diagnostic values. Preserve the event envelope; redact
        # every diagnostic value until the shared policy can be loaded.
        return REDACTED

    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return text_redactor(value, limit=_MAX_TEXT_LENGTH)
    if depth >= _MAX_DEPTH:
        return text_redactor(value, limit=_MAX_TEXT_LENGTH)
    if isinstance(value, Mapping):
        items = list(value.items())[:_MAX_ITEMS]
        return {
            str(key): (
                REDACTED
                if _name_is_sensitive(str(key))
                else redact_value(item, depth=depth + 1)
            )
            for key, item in items
        }
    if isinstance(value, (list, tuple)):
        return [redact_value(item, depth=depth + 1) for item in value[:_MAX_ITEMS]]
    if isinstance(value, (set, frozenset)):
        return [redact_value(item, depth=depth + 1) for item in list(value)[:_MAX_ITEMS]]
    return text_redactor(value, limit=_MAX_TEXT_LENGTH)


def _name_is_sensitive(name: str) -> bool:
    try:
        _, name_predicate = _loader()
        return bool(name_predicate(name))
    except Exception:
        # Unknown sensitivity is treated as sensitive.
        return True


def redact_event_record(
    record: Mapping[str, Any], *, envelope_fields: frozenset[str] | set[str]
) -> dict[str, Any]:
    """Redact a record's diagnostics while leaving the envelope untouched.

    The envelope is machine identity (run/event/dispatch ids, timestamps). It is
    never redacted, or correlation would stop working; it is also never derived
    from untrusted text.
    """
    result: dict[str, Any] = {}
    for key, value in record.items():
        if key in envelope_fields:
            result[key] = value
            continue
        result[key] = REDACTED if _name_is_sensitive(key) else redact_value(value)
    return result
