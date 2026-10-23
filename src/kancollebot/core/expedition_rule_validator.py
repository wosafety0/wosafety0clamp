"""Validate imported expedition reference rules without authorizing gameplay."""

import re
from dataclasses import dataclass
from typing import Any, Mapping

_COMPOSITION = re.compile(r"^(?:[1-6](?:NA|DD|CL|CLT|CA|CAV|BB|BBV|CV|CVL|SS|SSV|AV|AO|AS|AR|CT|DE|LHA|CVB))(?:,[1-6](?:NA|DD|CL|CLT|CA|CAV|BB|BBV|CV|CVL|SS|SSV|AV|AO|AS|AR|CT|DE|LHA|CVB))*$")


@dataclass(frozen=True)
class RuleValidation:
    valid: bool
    dispatch_authorized: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


def _integer(value: Any, minimum: int) -> bool:
    return type(value) is int and value >= minimum


def validate_expedition_rules(document: Any) -> RuleValidation:
    """Check structure and consistency. Imported reference rules never authorize dispatch."""
    errors = []
    warnings = []
    if not isinstance(document, dict):
        return RuleValidation(False, False, ("Document must be an object.",), ())
    if document.get("schema_version") != 1:
        errors.append("Unsupported schema_version.")
    records = document.get("expeditions")
    if not isinstance(records, list):
        return RuleValidation(False, False, tuple(errors + ["expeditions must be a list."]), ())
    if not _integer(document.get("record_count"), 0) or document["record_count"] != len(records):
        errors.append("record_count mismatch.")
    seen = set()
    for index, item in enumerate(records):
        label = f"expeditions[{index}]"
        if not isinstance(item, Mapping):
            errors.append(f"{label}: record must be an object.")
            continue
        identifier = item.get("id")
        if not _integer(identifier, 1):
            errors.append(f"{label}: invalid id.")
        elif identifier in seen:
            errors.append(f"{label}: duplicate id {identifier}.")
        else:
            seen.add(identifier)
        if not _integer(item.get("duration_minutes"), 1):
            errors.append(f"{label}: invalid duration_minutes.")
        req = item.get("requirements")
        if not isinstance(req, Mapping):
            errors.append(f"{label}: requirements missing.")
            continue
        composition = req.get("composition_raw")
        if not isinstance(composition, str) or not _COMPOSITION.fullmatch(composition):
            errors.append(f"{label}: unsupported composition syntax.")
        else:
            tokens = composition.split(",")
            if sum(int(t[0]) for t in tokens) > 6:
                errors.append(f"{label}: composition exceeds six ships.")
        for field in ("flagship_level_min", "total_level_min", "drum_count_min", "drum_carriers_min"):
            if not _integer(req.get(field), 0):
                errors.append(f"{label}: invalid {field}.")
        drums = req.get("drum_count_min")
        carriers = req.get("drum_carriers_min")
        if _integer(drums, 0) and _integer(carriers, 0) and carriers > drums:
            errors.append(f"{label}: drum carriers exceed drum count.")
        if req.get("additional_stats") == "UNKNOWN" or req.get("additional_equipment") == "UNKNOWN":
            warnings.append(f"{label}: additional requirements unverified.")
        great = item.get("great_success")
        if not isinstance(great, Mapping) or "conditions" not in great or "probability" not in great:
            errors.append(f"{label}: great_success metadata missing.")
        elif great["conditions"] == "UNKNOWN" or great["probability"] == "UNKNOWN":
            warnings.append(f"{label}: great success rules unverified.")
        if item.get("verification") != "VERIFIED":
            warnings.append(f"{label}: source rules not independently verified.")
        source = item.get("source")
        if not isinstance(source, Mapping) or not all(
            isinstance(source.get(key), str) and source[key]
            for key in ("repository", "path", "blob_sha")
        ):
            errors.append(f"{label}: source provenance missing.")
    return RuleValidation(not errors, False, tuple(errors), tuple(warnings))
