"""Privacy-preserving shape inspection of POI ship records.

Only field names and coarse JSON types are reported. No ship names, IDs,
values, tokens, or raw response bodies are included in the report.
"""
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ShipSchemaReport:
    record_count: int
    sampled_records: int
    fields: tuple[tuple[str, str, int], ...]


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unsupported"


def inspect_ship_schema(
    payload: Any, *, max_records: int = 20, max_fields: int = 100
) -> ShipSchemaReport:
    """Inspect a bounded sample while validating every record's shape.

    Validation covers the complete mapping, not just the sample, so malformed
    records outside the reporting window cannot silently pass. Only sampled
    field/type frequencies are returned. The caller must decide whether field
    names themselves are safe to share. This function performs no I/O.
    """
    if isinstance(max_records, bool) or not isinstance(max_records, int) or not 1 <= max_records <= 100:
        raise ValueError("max_records must be an integer between 1 and 100")
    if isinstance(max_fields, bool) or not isinstance(max_fields, int) or not 1 <= max_fields <= 500:
        raise ValueError("max_fields must be an integer between 1 and 500")
    if not isinstance(payload, Mapping):
        raise ValueError("ships payload must be a mapping")
    if len(payload) > 100000:
        raise ValueError("ships payload exceeds the supported record count")

    counts: Counter[tuple[str, str]] = Counter()
    sampled = 0
    for key, record in payload.items():
        if not isinstance(key, str) or not key.isdecimal() or int(key) <= 0:
            raise ValueError("ship keys must be positive decimal IDs")
        if not isinstance(record, dict):
            raise ValueError("ship records must be objects")
        if len(record) > max_fields:
            raise ValueError("ship record exceeds field limit")
        for field, value in record.items():
            if not isinstance(field, str) or not field or len(field) > 128:
                raise ValueError("ship field names must be non-empty short strings")
        if sampled < max_records:
            for field, value in record.items():
                counts[(field, _json_type(value))] += 1
            sampled += 1

    return ShipSchemaReport(
        record_count=len(payload),
        sampled_records=sampled,
        fields=tuple(sorted((name, kind, count) for (name, kind), count in counts.items())),
    )
