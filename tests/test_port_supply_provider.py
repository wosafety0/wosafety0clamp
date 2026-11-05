"""Tests for the Redux + port-witness fleet supply provider."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from kancollebot.adapters.poi.cdp_port_supply import PoiPortSupplySnapshot
from kancollebot.expeditions.continuous_resupply_controller import (
    FleetSupplyObservation,
)
from kancollebot.expeditions.port_supply_provider import (
    PoiReduxFleetSupplyProvider,
    PortSupplyProviderError,
)

FIXED_NOW = datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc)


def make_snapshot(*, fuel=6, observed_at=FIXED_NOW) -> PoiPortSupplySnapshot:
    fleets = [
        {"api_id": 1, "api_name": "first", "api_mission": [0, 0, 0, 0], "api_ship": [11, 12]},
        {"api_id": 2, "api_name": "second", "api_mission": [0, 0, 0, 0], "api_ship": [21, 22]},
        {"api_id": 3, "api_name": "third", "api_mission": [0, 0, 0, 0], "api_ship": [31, 32]},
    ]
    ships = {}
    masters = {}
    for index, ship_id in enumerate((11, 12, 21, 22, 31, 32)):
        master_id = 101 + (index % 2)
        ships[str(ship_id)] = {
            "api_id": ship_id,
            "api_ship_id": master_id,
            "api_nowhp": 30,
            "api_maxhp": 30,
            "api_fuel": fuel,
            "api_bull": 20,
            "api_lv": 20,
            "api_cond": 49,
        }
        masters[str(master_id)] = {
            "api_id": master_id,
            "api_fuel_max": 15,
            "api_bull_max": 20,
            "api_stype": 1,
        }
    return PoiPortSupplySnapshot(
        fleets=fleets,
        ships=ships,
        master_ships=masters,
        resources=[5000, 5000, 5000, 5000],
        observed_at=observed_at,
    )


def make_witness(snapshot, *, captured_at=FIXED_NOW - timedelta(seconds=1), fuel=None):
    """A port-response witness in the shape ``port_snapshot_matches_current_state`` accepts."""
    ships = {}
    for key, record in snapshot.ships.items():
        copied = dict(record)
        if fuel is not None:
            copied["api_fuel"] = fuel
        ships[key] = copied
    return {
        "schemaVersion": 1,
        "verificationStatus": "CAPTURED_UNDER_STATICALLY_REVIEWED_PROBE",
        "source": {
            "kind": "cdp-network-response",
            "path": "/kcsapi/api_port/port",
            "httpStatus": 200,
            "host": "kancolle-server.com",
            "fromDiskCache": False,
            "fromServiceWorker": False,
            "fromPrefetchCache": False,
            "responseBodySha256": "a" * 64,
            "responseBodyBytes": 2048,
            "responseReceivedAt": (captured_at - timedelta(milliseconds=20)).isoformat(),
            "bodyCapturedAt": captured_at.isoformat(),
            "gameInputSent": False,
            "gameApiRequestInitiated": False,
            "runtimeEvaluateUsed": False,
            "fullRawResponseSaved": False,
            "productionFreshnessGateChanged": False,
        },
        "safety": {
            "gameInputSent": False,
            "gameApiRequestInitiated": False,
            "runtimeEvaluateUsed": False,
            "fullRawResponseSaved": False,
            "productionFreshnessGateChanged": False,
        },
        "data": {
            "api_deck_port": snapshot.fleets,
            "api_ship": ships,
            "api_material": [
                {"api_id": index + 1, "api_value": value}
                for index, value in enumerate(snapshot.resources)
            ],
        },
        "summary": {
            "fleetCount": len(snapshot.fleets),
            "shipCount": len(snapshot.ships),
        },
    }


def build_provider(snapshot, witness):
    return PoiReduxFleetSupplyProvider(
        snapshot_reader=lambda: snapshot,
        port_witness_provider=lambda: witness,
        max_age=timedelta(seconds=30),
        clock=lambda: FIXED_NOW,
    )
class PoiReduxFleetSupplyProviderTests(unittest.TestCase):
    def test_matching_witness_verifies_freshness_from_poi_redux(self):
        snapshot = make_snapshot()
        provider = build_provider(snapshot, make_witness(snapshot))

        observation = provider()

        self.assertIsInstance(observation, FleetSupplyObservation)
        self.assertIs(observation.source_freshness_verified, True)
        self.assertEqual(observation.source, "poi-redux:port")
        self.assertEqual(observation.observed_at, snapshot.observed_at)
        self.assertEqual(observation.resources, [5000, 5000, 5000, 5000])
        self.assertIs(provider.last_freshness_verified, True)
        self.assertIsNone(provider.last_reject_reason)
        self.assertEqual(provider.last_witness_captured_at, FIXED_NOW - timedelta(seconds=1))

    def test_missing_witness_never_claims_freshness(self):
        snapshot = make_snapshot()
        provider = build_provider(snapshot, None)

        observation = provider()

        self.assertIs(observation.source_freshness_verified, False)
        self.assertIn("no in-memory port witness", provider.last_reject_reason)
        # The data itself is still returned, so the controller's preflight is
        # the single place that refuses to act on it.
        self.assertEqual(len(observation.fleets), 3)

    def test_stale_witness_is_not_freshness_evidence(self):
        snapshot = make_snapshot()
        stale = make_witness(
            snapshot, captured_at=FIXED_NOW - timedelta(minutes=5)
        )

        provider = build_provider(snapshot, stale)
        observation = provider()

        self.assertIs(observation.source_freshness_verified, False)
        self.assertIn("does not match", provider.last_reject_reason)

    def test_future_dated_witness_is_not_freshness_evidence(self):
        snapshot = make_snapshot()
        future = make_witness(snapshot, captured_at=FIXED_NOW + timedelta(minutes=1))

        observation = build_provider(snapshot, future)()

        self.assertIs(observation.source_freshness_verified, False)

    def test_witness_disagreeing_with_redux_fails_freshness(self):
        snapshot = make_snapshot()
        # The witness claims full fuel while Redux says 6: one of them is old.
        witness = make_witness(snapshot, fuel=15)

        provider = build_provider(snapshot, witness)
        observation = provider()

        self.assertIs(observation.source_freshness_verified, False)
        self.assertIn("fleet/ship state", provider.last_reject_reason)

    def test_material_mismatch_fails_freshness(self):
        snapshot = make_snapshot()
        witness = make_witness(snapshot)
        witness["data"]["api_material"][0]["api_value"] = 1

        provider = build_provider(snapshot, witness)

        self.assertIs(provider().source_freshness_verified, False)
        self.assertIn("material inventory", provider.last_reject_reason)

    def test_witness_shape_is_rejected_without_raising(self):
        snapshot = make_snapshot()
        provider = build_provider(snapshot, "not-a-witness")

        self.assertIs(provider().source_freshness_verified, False)
        self.assertIn("invalid shape", provider.last_reject_reason)

    def test_observed_at_is_the_read_time_not_the_witness_time(self):
        snapshot = make_snapshot()
        captured_at = FIXED_NOW - timedelta(seconds=20)
        witness = make_witness(snapshot, captured_at=captured_at)

        provider = build_provider(snapshot, witness)
        observation = provider()

        # The observation time is when Redux was read; the witness time is
        # recorded separately as evidence and never substituted.
        self.assertEqual(observation.observed_at, FIXED_NOW)
        self.assertEqual(provider.last_witness_captured_at, captured_at)
        self.assertNotEqual(observation.observed_at, provider.last_witness_captured_at)

    def test_reader_returning_the_wrong_type_raises(self):
        provider = PoiReduxFleetSupplyProvider(
            snapshot_reader=lambda: {"fleets": []},
            port_witness_provider=None,
        )
        with self.assertRaisesRegex(PortSupplyProviderError, "PoiPortSupplySnapshot"):
            provider()

    def test_constructor_validates_its_inputs(self):
        with self.assertRaisesRegex(ValueError, "snapshot_reader"):
            PoiReduxFleetSupplyProvider(
                snapshot_reader=None, port_witness_provider=None
            )
        with self.assertRaisesRegex(ValueError, "port_witness_provider"):
            PoiReduxFleetSupplyProvider(
                snapshot_reader=make_snapshot, port_witness_provider=object()
            )
        with self.assertRaisesRegex(ValueError, "max_age"):
            PoiReduxFleetSupplyProvider(
                snapshot_reader=make_snapshot,
                port_witness_provider=None,
                max_age=timedelta(seconds=-1),
            )


if __name__ == "__main__":
    unittest.main()
