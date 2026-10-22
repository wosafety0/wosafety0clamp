"""Read-only HTTP adapter for a local POI state endpoint.

The adapter is deliberately restricted to loopback HTTP and GET requests.
It never follows an arbitrary caller-supplied host and never sends gameplay
commands. Network access is injectable so tests can run entirely offline.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from kancollebot.core.fleet_parser import FleetObservation, parse_fleet_payload


_DEFAULT_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}


class PoiHttpReadOnlyAdapter:
    """Read and cross-check POI fleet/ship IDs using local GET endpoints."""

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
        # Explicitly disable environment proxy inheritance for loopback traffic.
        self._opener = opener if opener is not None else build_opener(ProxyHandler({}))

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
