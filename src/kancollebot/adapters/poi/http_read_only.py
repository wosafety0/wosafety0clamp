"""Read-only HTTP adapter for a local POI state endpoint.

The adapter is restricted to loopback HTTP and GET requests. Redirects are
disabled so a local endpoint cannot redirect requests to a remote host.
Network access is injectable so tests run entirely offline.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from kancollebot.core.expedition_planner import ExpeditionDecision
from kancollebot.core.expedition_safety import plan_fresh_safe_expedition
from kancollebot.core.fleet_parser import FleetObservation, parse_fleet_payload
from kancollebot.core.ship_parser import ShipFieldMap, parse_ship_payload
from kancollebot.core.models import ShipState
from kancollebot.core.observed_state import ObservedState
from kancollebot.core.ship_schema import inspect_ship_schema


_DEFAULT_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


class PoiHttpReadOnlyAdapter:
    """Read and cross-check POI fleet/ship data using local GET endpoints."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:17777",
        *,
        timeout: float = 2.0,
        max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
        opener: Any | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in _ALLOWED_HOSTS
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("base_url must be plain HTTP on a loopback host without credentials or path")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")

        self._base_url = f"http://{parsed.netloc}"
        self._timeout = timeout
        self._max_response_bytes = max_response_bytes
        # Avoid environment proxy inheritance and reject all HTTP redirects.
        self._opener = opener if opener is not None else build_opener(
            ProxyHandler({}),
            _NoRedirectHandler(),
        )

    def _get_json(self, endpoint: str) -> Any:
        if endpoint not in {"fleets", "ships"}:
            raise ValueError("unsupported read-only endpoint")
        request = Request(
            f"{self._base_url}/{endpoint}",
            headers={"Accept": "application/json"},
            method="GET",
        )
        with self._opener.open(request, timeout=self._timeout) as response:
            status = getattr(response, "status", 200)
            if status < 200 or status >= 300:
                raise OSError(f"POI endpoint returned HTTP {status}")
            body = response.read(self._max_response_bytes + 1)
        if len(body) > self._max_response_bytes:
            raise ValueError(f"POI {endpoint} response exceeds configured size limit")
        try:
            text = body.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError(f"POI {endpoint} response is not valid UTF-8") from exc
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"POI {endpoint} response is not valid JSON") from exc

    @staticmethod
    def _parse_ship_ids(payload: Any) -> set[int]:
        if not isinstance(payload, dict):
            raise ValueError("POI ships payload must be an object keyed by ship ID")
        ship_ids: set[int] = set()
        for raw_id, record in payload.items():
            if not isinstance(raw_id, str) or not raw_id.isdecimal():
                raise ValueError("POI ships keys must be decimal ship IDs")
            ship_id = int(raw_id)
            if ship_id <= 0:
                raise ValueError("POI ship IDs must be positive")
            if not isinstance(record, dict):
                raise ValueError(f"POI ship record {raw_id} must be an object")
            if ship_id in ship_ids:
                raise ValueError(f"duplicate POI ship ID {ship_id}")
            ship_ids.add(ship_id)
        return ship_ids

    def get_ship_states(self, fields: ShipFieldMap) -> dict[int, ShipState]:
        """Parse /ships using a caller-supplied, explicit POI field mapping.

        Field names are never guessed. Until the mapping is backed by an
        authoritative runtime sample, callers must supply it explicitly.
        """
        return parse_ship_payload(self._get_json("ships"), fields=fields)

    def get_observed_states(
        self,
        fields: ShipFieldMap,
    ) -> tuple[
        ObservedState[tuple[FleetObservation, ...]],
        ObservedState[dict[int, ShipState]],
    ]:
        """Read, validate, and timestamp fleet/ship observations for planning.

        The timestamp is captured before network I/O so latency makes the
        observations conservatively older, never artificially fresher. Ship
        field names remain explicit and are never inferred by this method.
        """
        observed_at = datetime.now(timezone.utc)
        fleet_payload = self._get_json("fleets")
        ship_payload = self._get_json("ships")
        fleets = parse_fleet_payload(fleet_payload)
        known_ship_ids = self._parse_ship_ids(ship_payload)
        ships = parse_ship_payload(ship_payload, fields=fields)

        missing = {
            ship_id
            for fleet in fleets
            for ship_id in fleet.occupied_ship_ids
            if ship_id not in known_ship_ids
        }
        if missing:
            raise ValueError("fleet payload references ship records absent from /ships")
        missing_states = {
            ship_id
            for fleet in fleets
            for ship_id in fleet.occupied_ship_ids
            if ship_id not in ships
        }
        if missing_states:
            raise ValueError("fleet payload references ship records that failed state parsing")

        source = "poi-http-read-only"
        return (
            ObservedState(payload=fleets, observed_at=observed_at, source=source),
            ObservedState(payload=ships, observed_at=observed_at, source=source),
        )

    def read_and_plan(
        self,
        fields: ShipFieldMap,
        *,
        mission_by_fleet: Mapping[int, int],
        max_age: timedelta = timedelta(seconds=10),
        dispatchable_fleet_ids: Sequence[int] = (2, 3, 4),
    ) -> tuple[
        ObservedState[tuple[FleetObservation, ...]],
        ObservedState[dict[int, ShipState]],
        ExpeditionDecision,
    ]:
        """Connect live read-only POI observations to the expedition planner.

        This method cannot execute game actions. Readiness evidence for supply,
        repair/docking, mission composition, and fleet rotation is deliberately
        not accepted from callers here, so dispatch remains blocked until those
        checks are backed by observed data in a separate integration.
        """
        fleets_state, ships_state = self.get_observed_states(fields)
        decision = plan_fresh_safe_expedition(
            fleets_state,
            ships_state,
            now=datetime.now(timezone.utc),
            max_age=max_age,
            mission_by_fleet=mission_by_fleet,
            dispatchable_fleet_ids=dispatchable_fleet_ids,
        )
        return fleets_state, ships_state, decision

    def diagnose(self) -> dict[str, object]:
        """Read fleet and ship endpoints and return a privacy-preserving report.

        The report never includes ship names, ship IDs, or raw record values.
        Field-name candidates are reported as evidence only; they are not
        automatically applied to the ship parser or dispatch safety gates.
        """
        fleet_payload = self._get_json("fleets")
        ship_payload = self._get_json("ships")
        fleets = parse_fleet_payload(fleet_payload)
        known_ship_ids = self._parse_ship_ids(ship_payload)
        missing = {
            ship_id
            for fleet in fleets
            for ship_id in fleet.occupied_ship_ids
            if ship_id not in known_ship_ids
        }
        if missing:
            raise ValueError("fleet payload references ship records absent from /ships")

        schema = inspect_ship_schema(ship_payload)
        field_names = {name for name, _, _ in schema.fields}
        records = list(ship_payload.values())

        def field_is_consistent(name: str, expected_type: type, *, non_empty: bool = False) -> bool:
            return bool(records) and all(
                name in record
                and type(record[name]) is expected_type
                and (not non_empty or bool(record[name].strip()))
                for record in records
            )

        candidate_fields = {
            "name": "api_name",
            "hp": "api_nowhp",
            "max_hp": "api_maxhp",
        }
        candidate_valid = (
            candidate_fields["name"] in field_names
            and candidate_fields["hp"] in field_names
            and candidate_fields["max_hp"] in field_names
            and field_is_consistent("api_name", str, non_empty=True)
            and field_is_consistent("api_nowhp", int)
            and field_is_consistent("api_maxhp", int)
            and all(
                0 <= record["api_nowhp"] <= record["api_maxhp"]
                and record["api_maxhp"] > 0
                for record in records
            )
        )
        mission_states = Counter(
            fleet.mission_state_code if fleet.mission_state_code is not None else "unknown"
            for fleet in fleets
        )
        return {
            "ok": True,
            "source": "poi-http-read-only",
            "endpoints": {"fleets": "read", "ships": "read"},
            "fleet_count": len(fleets),
            "ship_record_count": len(known_ship_ids),
            "fleet_ship_references_valid": True,
            "mission_state_counts": dict(mission_states),
            "ship_schema": {
                "sampled_records": schema.sampled_records,
                "field_type_counts": [
                    {"field": name, "type": kind, "count": count}
                    for name, kind, count in schema.fields
                ],
            },
            "known_field_map_candidate": {
                "fields": candidate_fields,
                "all_records_type_and_range_valid": candidate_valid,
                "auto_applied": False,
                "note": "Review the live schema before enabling ship-state parsing.",
            },
        }

    def get_status(self) -> dict[str, object]:
        """Return fleet observations after validating references against /ships."""
        fleet_payload = self._get_json("fleets")
        ship_payload = self._get_json("ships")
        fleets: tuple[FleetObservation, ...] = parse_fleet_payload(fleet_payload)
        known_ship_ids = self._parse_ship_ids(ship_payload)

        missing = sorted({
            ship_id
            for fleet in fleets
            for ship_id in fleet.occupied_ship_ids
            if ship_id not in known_ship_ids
        })
        if missing:
            raise ValueError(f"fleet payload references missing ship records: {missing}")

        return {
            "source": "poi-http-read-only",
            "fleet_count": len(fleets),
            "fleets": fleets,
            "ship_record_count": len(known_ship_ids),
            "ship_ids": tuple(sorted(known_ship_ids)),
        }


def main() -> int:
    """Run a read-only, privacy-preserving local POI connection diagnostic."""
    import json
    import sys

    base_url = os.environ.get("KANCOLLE_POI_HTTP_URL", "http://127.0.0.1:17777")
    try:
        report = PoiHttpReadOnlyAdapter(base_url=base_url).diagnose()
    except Exception as exc:
        # Do not print raw response data or exception details that may include
        # local identifiers. The exception type is enough to guide diagnosis.
        report = {
            "ok": False,
            "source": "poi-http-read-only",
            "error_type": type(exc).__name__,
            "error": "Read-only POI diagnostic failed; verify endpoint availability and response schema.",
        }
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\\n")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
