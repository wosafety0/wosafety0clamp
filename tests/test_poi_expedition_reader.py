"""Offline contract tests for the read-only Poi expedition reader."""
import json
import unittest
from datetime import datetime, timezone

from kancollebot.adapters.poi.expedition_reader import PoiExpeditionReader, _INFO_SHIPS_FIELDS
from kancollebot.adapters.poi.mcp_client import McpTool, PoiMcpError
from kancollebot.adapters.poi.requirement_evidence import SHIP_DETAIL_FIELDS

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


def sample_payloads():
    return {
        "info.fleets": {"kind": "array", "total": 1, "returned": 1, "truncated": False,
            "items": [{"api_id": 1, "api_ship": [101, -1, 102], "api_mission": [0, 0, 0, 0]}]},
        "info.ships": {"kind": "object-map", "total": 2, "returned": 2, "truncated": False,
            "items": {
                "101": {"api_id": 101, "api_ship_id": 11, "api_nowhp": 10, "api_maxhp": 10, "api_lv": 1, "api_fuel": 10, "api_bull": 10},
                "102": {"api_id": 102, "api_ship_id": 11, "api_nowhp": 10, "api_maxhp": 10, "api_lv": 1, "api_fuel": 10, "api_bull": 10},
            }},
        "lookup": {"11": {"api_fuel_max": 10, "api_bull_max": 10, "api_stype": 1}},
    }


class FakeClient:
    def __init__(self, *, store_ready=True, payloads=None, tools=None, lookup=None,
                 sequences=None, projected_ships=None, detail_ships=None,
                 equip_instances=None, detail_sequences=None):
        self.store_ready = store_ready
        self.payloads = payloads or sample_payloads()
        self.tools = tools or ("poi_describe", "poi_get", "poi_lookup")
        self.lookup = lookup
        self.sequences = sequences or {}
        self.projected_ships = projected_ships
        self.detail_ships = detail_ships
        self.equip_instances = equip_instances
        self.detail_sequences = detail_sequences
        self.sequence_counts = {}
        self.calls = []
        self.initialize_calls = 0
        self.health_calls = 0

    def health(self):
        self.health_calls += 1
        return {"name": "poi-nanodesu-mcp", "status": "ok", "storeReady": self.store_ready}

    def initialize(self):
        self.initialize_calls += 1
        return {"name": "fake-poi"}

    def list_tools(self):
        return tuple(McpTool(name) for name in self.tools)

    def call_read_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "poi_describe":
            payload = {"kind": "array", "path": arguments.get("path")}
        elif name == "poi_lookup":
            payload = self.lookup if self.lookup is not None else self.payloads["lookup"]
        else:
            path = arguments["path"]
            select = arguments.get("select") or ()
            is_detail_projection = path == "info.ships" and "api_slot" in select
            if is_detail_projection and self.detail_sequences is not None:
                index = self.sequence_counts.get("detail", 0)
                sequence = self.detail_sequences
                payload = sequence[min(index, len(sequence) - 1)]
                self.sequence_counts["detail"] = index + 1
            elif is_detail_projection and self.detail_ships is not None:
                payload = self.detail_ships
            elif path == "info.equips" and self.equip_instances is not None:
                payload = self.equip_instances
            elif path == "info.ships" and "select" in arguments and self.projected_ships is not None:
                payload = self.projected_ships
            elif path.startswith("info.ships."):
                if path in self.sequences:
                    index = self.sequence_counts.get(path, 0)
                    sequence = self.sequences[path]
                    record = sequence[min(index, len(sequence) - 1)]
                    self.sequence_counts[path] = index + 1
                else:
                    ship_id = path[len("info.ships."):]
                    record = self.payloads["info.ships"]["items"].get(ship_id)
                if record is None:
                    payload = {"kind": "error", "message": f"no key '{path[len('info.ships.'):]}'"}
                else:
                    payload = {"kind": "object", "value": record}
            elif path in self.sequences:
                index = self.sequence_counts.get(path, 0)
                sequence = self.sequences[path]
                payload = sequence[min(index, len(sequence) - 1)]
                self.sequence_counts[path] = index + 1
            else:
                payload = self.payloads[path]
        return {"content": [{"type": "text", "text": json.dumps(payload)}]}


class PoiExpeditionReaderTests(unittest.TestCase):
    def make_reader(self, **kwargs):
        client = FakeClient(**kwargs)
        return client, PoiExpeditionReader(client=client, clock=lambda: NOW)

    def make_port_snapshot(self, payloads=None):
        payloads = payloads or sample_payloads()
        fleets = payloads["info.fleets"]["items"]
        ships = payloads["info.ships"]["items"]
        compact_fleets = [
            {
                field: fleet[field]
                for field in ("api_id", "api_name", "api_mission", "api_ship")
                if field in fleet
            }
            for fleet in fleets
        ]
        ship_fields = (
            "api_id", "api_ship_id", "api_nowhp", "api_maxhp",
            "api_fuel", "api_bull", "api_cond",
        )
        compact_ships = {
            str(key): {
                field: record[field]
                for field in ship_fields
                if field in record
            }
            for key, record in ships.items()
        }
        return {
            "schemaVersion": 1,
            "verificationStatus": "CAPTURED_UNDER_STATICALLY_REVIEWED_PROBE",
            "source": {
                "kind": "cdp-network-response",
                "path": "/kcsapi/api_port/port",
                "host": "w14h.kancolle-server.com",
                "httpStatus": 200,
                "responseReceivedAt": "2026-10-24T11:59:58.000Z",
                "bodyCapturedAt": "2026-10-24T11:59:59.000Z",
                "fromDiskCache": False,
                "fromServiceWorker": False,
                "fromPrefetchCache": False,
                "responseBodySha256": "a" * 64,
                "responseBodyBytes": 1024,
            },
            "data": {
                "api_deck_port": compact_fleets,
                "api_ship": compact_ships,
            },
            "summary": {
                "fleetCount": len(compact_fleets),
                "shipCount": len(compact_ships),
            },
            "safety": {
                "gameInputSent": False,
                "gameApiRequestInitiated": False,
                "runtimeEvaluateUsed": False,
                "fullRawResponseSaved": False,
                "productionFreshnessGateChanged": False,
            },
        }

    def test_matching_live_port_snapshot_marks_source_freshness_verified(self):
        payloads = sample_payloads()
        snapshot = self.make_port_snapshot(payloads)
        client = FakeClient(payloads=payloads)
        reader = PoiExpeditionReader(
            client=client,
            clock=lambda: NOW,
            port_snapshot_provider=lambda: snapshot,
        )
        observed = reader.read_fleets()
        self.assertTrue(observed.source_freshness_verified)

    def test_mismatched_live_port_snapshot_keeps_freshness_unverified(self):
        payloads = sample_payloads()
        snapshot = self.make_port_snapshot(payloads)
        snapshot["data"]["api_ship"]["101"]["api_fuel"] = 9
        client = FakeClient(payloads=payloads)
        reader = PoiExpeditionReader(
            client=client,
            clock=lambda: NOW,
            port_snapshot_provider=lambda: snapshot,
        )
        observed = reader.read_fleets()
        self.assertFalse(observed.source_freshness_verified)

    def test_stale_live_port_snapshot_keeps_freshness_unverified(self):
        payloads = sample_payloads()
        snapshot = self.make_port_snapshot(payloads)
        snapshot["source"]["bodyCapturedAt"] = "2026-10-24T11:58:00.000Z"
        client = FakeClient(payloads=payloads)
        reader = PoiExpeditionReader(
            client=client,
            clock=lambda: NOW,
            port_snapshot_provider=lambda: snapshot,
        )
        observed = reader.read_fleets()
        self.assertFalse(observed.source_freshness_verified)

    def test_reads_fleet_with_targeted_master_lookup(self):
        client, reader = self.make_reader()
        observed = reader.read_fleets()
        self.assertFalse(observed.source_freshness_verified)
        self.assertEqual(observed.observed_at, NOW)
        self.assertEqual(len(observed.payload), 1)
        self.assertEqual(observed.payload[0].fleet_id, 1)
        self.assertTrue(observed.payload[0].fuel_ready)
        self.assertTrue(observed.payload[0].ammo_ready)
        lookup_calls = [args for name, args in client.calls if name == "poi_lookup"]
        self.assertEqual(len(lookup_calls), 1)
        self.assertEqual(lookup_calls[0]["kind"], "ships")
        self.assertEqual(lookup_calls[0]["ids"], [11])
        self.assertEqual(lookup_calls[0]["select"], ["api_fuel_max", "api_bull_max", "api_stype"])
        # Composition metadata resolves once api_stype and api_lv are present.
        self.assertEqual(observed.payload[0].ship_levels, (1, 1))
        self.assertEqual(observed.payload[0].ship_types, (1, 1))
        self.assertFalse(any(args.get("path") == "const.$ships" for _, args in client.calls))

    def test_rejects_state_changed_during_read(self):
        first = sample_payloads()
        second_fleets = json.loads(json.dumps(first["info.fleets"]))
        second_fleets["items"][0]["api_name"] = "changed-between-reads"
        sequences = {
            "info.fleets": [first["info.fleets"], second_fleets],
            "info.ships": [first["info.ships"], first["info.ships"]],
        }
        _, reader = self.make_reader(sequences=sequences)
        with self.assertRaisesRegex(PoiMcpError, "changed during observation"):
            reader.read_fleets()

    def test_reads_a_stable_snapshot_with_confirmation_reads(self):
        client, reader = self.make_reader()
        observed = reader.read_fleets()
        self.assertEqual(len(observed.payload), 1)
        read_paths = [args["path"] for name, args in client.calls if name == "poi_get"]
        # Each of the two consistency rounds now also takes one narrow
        # equipment/stat projection. It is a SEPARATE read: widening the
        # port-witness projection truncates the 239-ship roster on the real
        # bridge and would silently destroy source freshness verification.
        self.assertEqual(
            read_paths,
            [
                "info.fleets", "info.ships", "info.ships",
                "info.fleets", "info.ships", "info.ships",
            ],
        )
        select_sets = [
            set(args["select"])
            for name, args in client.calls
            if name == "poi_get" and args["path"] == "info.ships" and "select" in args
        ]
        self.assertEqual(len(select_sets), 2)
        self.assertEqual(select_sets[0], select_sets[1])
        self.assertIn("api_slot", select_sets[0])
        self.assertIn("api_karyoku", select_sets[0])
        self.assertNotIn("api_nowhp", select_sets[0])
        self.assertNotIn("api_lv", select_sets[0])
        self.assertEqual(select_sets[0], set(SHIP_DETAIL_FIELDS))

    def test_rejects_truncated_fleet_payload(self):
        payloads = sample_payloads()
        payloads["info.fleets"]["truncated"] = True
        _, reader = self.make_reader(payloads=payloads)
        with self.assertRaisesRegex(PoiMcpError, "truncated"):
            reader.read_fleets()

    def test_missing_truncation_marker_fails_closed(self):
        payloads = sample_payloads()
        del payloads["info.fleets"]["truncated"]
        _, reader = self.make_reader(payloads=payloads)
        with self.assertRaisesRegex(PoiMcpError, "truncation marker"):
            reader.read_fleets()

    def test_inconsistent_collection_counts_fail_closed(self):
        payloads = sample_payloads()
        payloads["info.fleets"]["returned"] = 0
        _, reader = self.make_reader(payloads=payloads)
        with self.assertRaisesRegex(PoiMcpError, "counts do not match"):
            reader.read_fleets()

    def test_missing_mission_data_fails_closed(self):
        payloads = sample_payloads()
        del payloads["info.fleets"]["items"][0]["api_mission"]
        _, reader = self.make_reader(payloads=payloads)
        with self.assertRaisesRegex(PoiMcpError, "normalization failed closed"):
            reader.read_fleets()

    def test_missing_master_record_fails_closed(self):
        _, reader = self.make_reader(lookup={})
        with self.assertRaisesRegex(PoiMcpError, "omitted master ship record"):
            reader.read_fleets()

    def test_wrong_lookup_shape_fails_closed(self):
        _, reader = self.make_reader(lookup={"kind": "ships", "items": {}})
        with self.assertRaisesRegex(PoiMcpError, "invalid master-record object map"):
            reader.read_fleets()

    def test_invalid_required_master_field_fails_closed(self):
        _, reader = self.make_reader(lookup={"11": {"api_fuel_max": True, "api_bull_max": 10}})
        with self.assertRaisesRegex(PoiMcpError, "invalid api_fuel_max"):
            reader.read_fleets()

    def test_wrong_bridge_identity_stops_before_initialization(self):
        class WrongIdentityClient(FakeClient):
            def health(self):
                return {"name": "another-service", "status": "ok", "storeReady": True}
        client = WrongIdentityClient()
        reader = PoiExpeditionReader(client=client, clock=lambda: NOW)
        with self.assertRaisesRegex(PoiMcpError, "identity"):
            reader.read_fleets()
        self.assertEqual(client.initialize_calls, 0)
        self.assertEqual(client.calls, [])

    def test_store_not_ready_stops_before_initialization(self):
        client = FakeClient(store_ready=False)
        reader = PoiExpeditionReader(client=client, clock=lambda: NOW)
        with self.assertRaisesRegex(PoiMcpError, "store is not ready"):
            reader.read_fleets()
        self.assertEqual(client.initialize_calls, 0)
        self.assertEqual(client.calls, [])

    def test_missing_lookup_tool_fails_closed(self):
        _, reader = self.make_reader(tools=("poi_describe", "poi_get"))
        with self.assertRaisesRegex(PoiMcpError, "missing required"):
            reader.read_fleets()

    def _whole_roster_with_unreferenced_ship(self, truncated_payloads):
        """A 3-ship roster where ship 999 is in no fleet, as a port witness is."""
        items = dict(truncated_payloads["info.ships"]["items"])
        items["999"] = {
            "api_id": 999, "api_ship_id": 11, "api_nowhp": 10, "api_maxhp": 10,
            "api_lv": 1, "api_fuel": 10, "api_bull": 10,
        }
        return {"kind": "object-map", "total": 3, "returned": 3,
                "truncated": False, "items": items}

    def _witness_for_whole_roster(self, roster):
        payloads = sample_payloads()
        payloads["info.ships"] = roster
        return self.make_port_snapshot(payloads)

    def test_truncated_roster_uses_projected_full_read_and_restores_freshness(self):
        payloads = sample_payloads()
        # The plain read is capped by response size (239 instances, 2 returned).
        payloads["info.ships"]["truncated"] = True
        payloads["info.ships"]["total"] = 3
        payloads["info.ships"]["returned"] = 2
        roster = self._whole_roster_with_unreferenced_ship(payloads)
        snapshot = self._witness_for_whole_roster(roster)
        client = FakeClient(payloads=payloads, projected_ships=roster)
        reader = PoiExpeditionReader(
            client=client, clock=lambda: NOW,
            port_snapshot_provider=lambda: snapshot,
        )
        observed = reader.read_fleets()
        # A whole roster is what lets the witness be compared field by field.
        self.assertTrue(observed.source_freshness_verified)
        projected_calls = [
            args for name, args in client.calls
            if name == "poi_get" and args["path"] == "info.ships" and "select" in args
        ]
        # Two rounds: the original port-witness projection plus the narrow
        # equipment/stat projection. They must never be merged into one read.
        self.assertEqual(len(projected_calls), 4)
        witness_calls = [args for args in projected_calls if "api_lv" in args["select"]]
        detail_calls = [args for args in projected_calls if "api_slot" in args["select"]]
        self.assertEqual(len(witness_calls), 2)
        self.assertEqual(len(detail_calls), 2)
        self.assertEqual(set(witness_calls[0]["select"]), set(_INFO_SHIPS_FIELDS))
        self.assertEqual(set(detail_calls[0]["select"]), set(SHIP_DETAIL_FIELDS))
        self.assertIn("api_cond", witness_calls[0]["select"])
        self.assertGreaterEqual(witness_calls[0]["limit"], 239)
        per_id_calls = [
            args["path"] for name, args in client.calls
            if name == "poi_get" and args["path"].startswith("info.ships.")
        ]
        self.assertEqual(per_id_calls, [])
        # The projection must not silently drop composition metadata.
        self.assertEqual(observed.payload[0].ship_levels, (1, 1))

    def test_per_id_subset_fallback_never_claims_source_freshness(self):
        payloads = sample_payloads()
        payloads["info.ships"]["truncated"] = True
        payloads["info.ships"]["total"] = 3
        payloads["info.ships"]["returned"] = 2
        roster = self._whole_roster_with_unreferenced_ship(payloads)
        snapshot = self._witness_for_whole_roster(roster)
        # No projected_ships: even the projected read is truncated, so the
        # reader falls back to the two referenced instances only.
        client = FakeClient(payloads=payloads)
        reader = PoiExpeditionReader(
            client=client, clock=lambda: NOW,
            port_snapshot_provider=lambda: snapshot,
        )
        observed = reader.read_fleets()
        self.assertEqual(observed.payload[0].fleet_id, 1)
        self.assertFalse(observed.source_freshness_verified)

    def test_truncated_ship_payload_falls_back_to_per_id_fetch(self):
        payloads = sample_payloads()
        # Simulate a bridge that cannot honour the projection either: the whole
        # map is truncated, but the two instances the fleet references are
        # present in the (partial) items the bridge happened to return.
        payloads["info.ships"]["truncated"] = True
        payloads["info.ships"]["total"] = 239
        payloads["info.ships"]["returned"] = 2
        client, reader = self.make_reader(payloads=payloads)
        observed = reader.read_fleets()
        self.assertEqual(len(observed.payload), 1)
        self.assertEqual(observed.payload[0].fleet_id, 1)
        self.assertTrue(observed.payload[0].fuel_ready)
        self.assertTrue(observed.payload[0].ammo_ready)
        per_id_calls = [
            args["path"] for name, args in client.calls
            if name == "poi_get" and args["path"].startswith("info.ships.")
        ]
        # The initial read and the consistency re-read each fetch the two
        # referenced instances by id (4 calls); neither whole-roster read
        # could be completed, so neither is used as the snapshot.
        self.assertEqual(per_id_calls, ["info.ships.101", "info.ships.102"] * 2)

    def test_complete_ship_payload_uses_full_read_without_per_id(self):
        client, reader = self.make_reader()
        reader.read_fleets()
        per_id_calls = [
            args["path"] for name, args in client.calls
            if name == "poi_get" and args["path"].startswith("info.ships.")
        ]
        self.assertEqual(per_id_calls, [])

    def test_missing_referenced_instance_in_fallback_fails_closed(self):
        payloads = sample_payloads()
        payloads["info.ships"]["truncated"] = True
        payloads["info.ships"]["total"] = 239
        payloads["info.ships"]["returned"] = 1
        # 102 is referenced by the fleet but absent from the partial items, so
        # the per-id lookup must fail closed rather than guess.
        del payloads["info.ships"]["items"]["102"]
        _, reader = self.make_reader(payloads=payloads)
        with self.assertRaisesRegex(PoiMcpError, "info.ships.102"):
            reader.read_fleets()

    def test_consistency_check_uses_subset_on_truncated(self):
        payloads = sample_payloads()
        payloads["info.ships"]["truncated"] = True
        payloads["info.ships"]["total"] = 239
        payloads["info.ships"]["returned"] = 2
        first101 = payloads["info.ships"]["items"]["101"]
        changed101 = json.loads(json.dumps(first101))
        changed101["api_nowhp"] = 3
        # Between the two reads a referenced ship instance changed in place.
        sequences = {"info.ships.101": [first101, changed101]}
        _, reader = self.make_reader(payloads=payloads, sequences=sequences)
        with self.assertRaisesRegex(PoiMcpError, "changed during observation"):
            reader.read_fleets()


    def test_static_fleet_description_is_read_once_but_live_state_is_reread(self):
        client, reader = self.make_reader()

        first = reader.read_fleets()
        second = reader.read_fleets()

        describe_calls = [
            call for call in client.calls if call[0] == "poi_describe"
        ]
        fleet_reads = [
            call for call in client.calls
            if call[0] == "poi_get" and call[1].get("path") == "info.fleets"
        ]
        ship_reads = [
            call for call in client.calls
            if call[0] == "poi_get" and call[1].get("path") == "info.ships"
        ]
        self.assertEqual(len(describe_calls), 1)
        self.assertEqual(len(fleet_reads), 4)
        # Two observations x (2 plain + 2 narrow equipment/stat projections).
        self.assertEqual(len(ship_reads), 8)
        plain_ship_reads = [call for call in ship_reads if "select" not in call[1]]
        detail_ship_reads = [call for call in ship_reads if "api_slot" in call[1].get("select", ())]
        self.assertEqual(len(plain_ship_reads), 4)
        self.assertEqual(len(detail_ship_reads), 4)
        self.assertEqual(client.health_calls, 2)
        self.assertEqual(len(first.payload), len(second.payload))


def detail_payload(*, drum_on_101=True):
    def ship(ship_id, slots):
        return {
            "api_id": ship_id, "api_slot": list(slots), "api_slot_ex": 0,
            "api_slotnum": 2,
            "api_karyoku": [20, 30], "api_taiku": [10, 20],
            "api_taisen": [15, 25], "api_sakuteki": [5, 15],
            "api_ndock_time": 0,
        }
    slots_101 = [9001, -1, -1, -1, -1] if drum_on_101 else [-1] * 5
    return {
        "kind": "object-map", "total": 2, "returned": 2, "truncated": False,
        "items": {"101": ship(101, slots_101), "102": ship(102, [-1] * 5)},
    }


def equip_payload(*, include_9001=True):
    items = {"9001": {"api_id": 9001, "api_slotitem_id": 75}} if include_9001 else {}
    return {
        "kind": "object-map", "total": 1, "returned": len(items),
        "truncated": False, "items": items,
    }


class RequirementEvidenceReaderTests(unittest.TestCase):
    """The reader attaches equipment/stat evidence to the SAME observation."""

    def make_reader(self, **kwargs):
        client = FakeClient(**kwargs)
        return client, PoiExpeditionReader(client=client, clock=lambda: NOW)

    def test_equipment_and_stats_are_attached_to_the_fleet_record(self):
        _, reader = self.make_reader(
            detail_ships=detail_payload(), equip_instances=equip_payload()
        )
        observed = reader.read_fleets()
        fleet = observed.payload[0]
        self.assertEqual(fleet.ship_ids, (101, 102))
        self.assertEqual(fleet.equipment_by_ship[101], (75,))
        self.assertEqual(fleet.equipment_by_ship[102], ())
        self.assertEqual(fleet.total_stats["firepower"], 40)
        self.assertEqual(fleet.total_stats["anti_air"], 20)

    def test_a_missing_equipment_collection_degrades_without_breaking_the_read(self):
        # No info.equips response at all: the base observation must still be
        # delivered (missions without equipment rules are unaffected), but the
        # target fleet must carry NO evidence so its conditions fail closed.
        client, reader = self.make_reader(detail_ships=detail_payload())
        observed = reader.read_fleets()
        self.assertEqual(len(observed.payload), 1)
        self.assertIsNone(observed.payload[0].equipment_by_ship)
        self.assertIsNone(observed.payload[0].total_stats)
        self.assertTrue(
            any(call[1].get("path") == "info.equips" for call in client.calls)
        )

    def test_an_unresolvable_equipment_instance_yields_no_evidence(self):
        _, reader = self.make_reader(
            detail_ships=detail_payload(), equip_instances=equip_payload(include_9001=False)
        )
        observed = reader.read_fleets()
        self.assertIsNone(observed.payload[0].equipment_by_ship)

    def test_evidence_that_changes_between_consistency_reads_is_not_merged(self):
        # A drum appears between the first and the confirmation read. Mixing
        # them would be a TOCTOU hole: refuse to attach any evidence at all.
        _, reader = self.make_reader(
            detail_sequences=[detail_payload(drum_on_101=True),
                              detail_payload(drum_on_101=False)],
            equip_instances=equip_payload(),
        )
        observed = reader.read_fleets()
        self.assertIsNone(observed.payload[0].equipment_by_ship)
        self.assertIsNone(observed.payload[0].total_stats)

    def test_evidence_reads_never_widen_the_port_witness_projection(self):
        # Real bridge: adding equipment fields to the 8-field witness read
        # truncates 239 ships to 225 and destroys freshness verification.
        client, _ = self.make_reader(
            detail_ships=detail_payload(), equip_instances=equip_payload()
        )
        reader = PoiExpeditionReader(client=client, clock=lambda: NOW)
        reader.read_fleets()
        witness_selects = [
            set(call[1]["select"])
            for call in client.calls
            if call[0] == "poi_get"
            and call[1].get("path") == "info.ships"
            and "api_lv" in call[1].get("select", ())
        ]
        for select in witness_selects:
            self.assertEqual(select, set(_INFO_SHIPS_FIELDS))
            self.assertNotIn("api_slot", select)


class PoiMissionNameLookupTests(unittest.TestCase):
    def test_mission_name_is_resolved_through_existing_poi_lookup_and_cached(self):
        client = FakeClient(lookup={"3": {"api_name": "????"}})
        reader = PoiExpeditionReader(client=client, clock=lambda: NOW)
        self.assertEqual(reader.lookup_mission_name(3), "????")
        self.assertEqual(reader.lookup_mission_name(3), "????")
        calls = [args for name, args in client.calls if name == "poi_lookup"]
        self.assertEqual(
            calls,
            [{"kind": "missions", "ids": [3], "select": ["api_name"]}],
        )

    def test_missing_mission_name_is_rejected(self):
        client = FakeClient(lookup={"3": {"api_id": 3}})
        reader = PoiExpeditionReader(client=client, clock=lambda: NOW)
        with self.assertRaisesRegex(PoiMcpError, "no valid api_name"):
            reader.lookup_mission_name(3)

    def test_invalid_mission_id_is_rejected_before_mcp_call(self):
        client = FakeClient()
        reader = PoiExpeditionReader(client=client, clock=lambda: NOW)
        with self.assertRaises(PoiMcpError):
            reader.lookup_mission_name(True)
        self.assertEqual(client.calls, [])


class PoiRewardInventoryReaderTests(unittest.TestCase):
    def payloads(self):
        value = sample_payloads()
        value["info.resources"] = {
            "kind": "array", "total": 8, "returned": 8, "truncated": False,
            "items": [100, 200, 300, 400, 5, 6, 7, 8],
        }
        value["info.useitems"] = {
            "kind": "object-map", "total": 2, "returned": 2, "truncated": False,
            "items": {
                "6": {"api_id": 6, "api_count": 2},
                "10": {"api_id": 10, "api_count": 4},
            },
        }
        return value

    def test_reads_complete_resources_and_useitems_through_existing_mcp_client(self):
        client = FakeClient(payloads=self.payloads())
        reader = PoiExpeditionReader(client=client, clock=lambda: NOW)
        snapshot = reader.read_reward_inventory()
        self.assertEqual(snapshot.observed_at, NOW)
        self.assertEqual(snapshot.resources, (100, 200, 300, 400, 5, 6, 7, 8))
        self.assertEqual(snapshot.useitems, ((6, 2), (10, 4)))
        self.assertEqual(
            [args["path"] for name, args in client.calls if name == "poi_get"],
            ["info.resources", "info.useitems"],
        )

    def test_truncated_useitem_inventory_is_rejected(self):
        payloads = self.payloads()
        payloads["info.useitems"]["truncated"] = True
        reader = PoiExpeditionReader(client=FakeClient(payloads=payloads), clock=lambda: NOW)
        with self.assertRaisesRegex(PoiMcpError, "truncated"):
            reader.read_reward_inventory()

    def test_useitem_object_key_must_match_api_id(self):
        payloads = self.payloads()
        payloads["info.useitems"]["items"]["6"]["api_id"] = 7
        reader = PoiExpeditionReader(client=FakeClient(payloads=payloads), clock=lambda: NOW)
        with self.assertRaisesRegex(PoiMcpError, "invalid ID/count"):
            reader.read_reward_inventory()

    def test_resources_must_be_exactly_eight_nonnegative_integers(self):
        payloads = self.payloads()
        payloads["info.resources"]["items"] = [100, 200, 300, 400]
        payloads["info.resources"]["total"] = 4
        payloads["info.resources"]["returned"] = 4
        reader = PoiExpeditionReader(client=FakeClient(payloads=payloads), clock=lambda: NOW)
        with self.assertRaisesRegex(PoiMcpError, "eight non-negative integers"):
            reader.read_reward_inventory()


if __name__ == "__main__":
    unittest.main()
