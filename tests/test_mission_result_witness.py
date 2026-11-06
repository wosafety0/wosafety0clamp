"""The expedition-result witness: what it may prove and what it may not.

The witness states which fleet completed which expedition. It cannot state
that a dispatch was accepted at the moment the button was pressed, and these
tests pin that boundary down.
"""
import unittest
from datetime import datetime, timedelta, timezone

from kancollebot.expeditions.mission_result_witness import (
    MISSION_RESULT_EVENT,
    DispatchRecord,
    MissionResultWitness,
    MissionResultWitnessError,
    cross_check_with_dispatch,
    evaluate_witness,
    parse_witness,
    read_witnesses,
    witness_fields,
)

FLEET = 3
OTHER = 2
SHIPS = (16374, 13350, 6, 5747)
OTHER_SHIPS = (111, 222, 333, 444)
NOW = datetime(2026, 11, 5, 8, 51, 59, tzinfo=timezone.utc)


def witness(
    *,
    fleet_id=FLEET,
    quest_name="練習航海",
    clear_result=2,
    ship_ids=SHIPS,
    response_received_at=NOW,
):
    return MissionResultWitness(
        fleet_id=fleet_id,
        quest_name=quest_name,
        clear_result=clear_result,
        ship_ids=tuple(ship_ids),
        requested_at=(response_received_at - timedelta(seconds=1))
        if response_received_at
        else None,
        response_received_at=response_received_at,
    )


def fields(**overrides):
    base = {
        "fleet_id": FLEET,
        "quest_name": "練習航海",
        "clear_result": 2,
        "ship_ids": list(SHIPS),
        "requested_at": NOW.isoformat(),
        "response_received_at": NOW.isoformat(),
    }
    base.update(overrides)
    return base


class WitnessParsingTests(unittest.TestCase):
    def test_a_complete_witness_is_read(self):
        parsed = parse_witness(fields())
        self.assertEqual(parsed.fleet_id, FLEET)
        self.assertEqual(parsed.quest_name, "練習航海")
        self.assertEqual(parsed.clear_result, 2)
        self.assertEqual(parsed.ship_ids, SHIPS)
        self.assertEqual(parsed.response_received_at, NOW)

    def test_the_placeholder_ship_is_not_a_ship(self):
        # The game puts -1 in front of the expedition's ships. It must never
        # reach a fleet comparison.
        parsed = parse_witness(fields(ship_ids=[-1, 16374, 13350, 6, 5747]))
        self.assertEqual(parsed.ship_ids, SHIPS)
        self.assertNotIn(-1, parsed.ship_ids)

    def test_a_witness_without_a_fleet_is_refused(self):
        with self.assertRaises(MissionResultWitnessError):
            parse_witness(fields(fleet_id=0))

    def test_a_witness_without_a_name_is_refused(self):
        with self.assertRaises(MissionResultWitnessError):
            parse_witness(fields(quest_name="  "))

    def test_a_witness_without_ships_is_refused(self):
        with self.assertRaises(MissionResultWitnessError):
            parse_witness(fields(ship_ids=[]))

    def test_a_witness_that_is_not_a_mapping_is_refused(self):
        with self.assertRaises(MissionResultWitnessError):
            parse_witness(["not", "a", "mapping"])


class WitnessReadingTests(unittest.TestCase):
    def test_witnesses_are_rebuilt_from_the_durable_log(self):
        events = [
            {"event": "dispatch_confirmed_by_poi", "fields": {"fleet_id": FLEET}},
            {
                "event": MISSION_RESULT_EVENT,
                "fields": fields(),
            },
        ]
        witnesses = read_witnesses(events)
        self.assertEqual(len(witnesses), 1)
        self.assertEqual(witnesses[0].fleet_id, FLEET)

    def test_a_duplicate_response_is_recorded_once(self):
        events = [
            {"event": MISSION_RESULT_EVENT, "fields": fields()},
            {"event": MISSION_RESULT_EVENT, "fields": fields()},
            {"event": MISSION_RESULT_EVENT, "fields": fields()},
        ]
        self.assertEqual(len(read_witnesses(events)), 1)

    def test_two_different_expeditions_are_two_witnesses(self):
        events = [
            {"event": MISSION_RESULT_EVENT, "fields": fields()},
            {
                "event": MISSION_RESULT_EVENT,
                "fields": fields(quest_name="警備任務"),
            },
        ]
        self.assertEqual(len(read_witnesses(events)), 2)

    def test_a_malformed_row_is_skipped_not_fatal(self):
        events = [
            {"event": MISSION_RESULT_EVENT, "fields": {"fleet_id": FLEET}},
            {"event": MISSION_RESULT_EVENT, "fields": fields()},
        ]
        witnesses = read_witnesses(events)
        self.assertEqual(len(witnesses), 1)
        self.assertEqual(witnesses[0].quest_name, "練習航海")

    def test_other_events_are_ignored(self):
        events = [
            {"event": "settlement_reward_claimed", "fields": fields()},
        ]
        self.assertEqual(read_witnesses(events), ())


class WitnessEvaluationTests(unittest.TestCase):
    def test_a_witness_that_agrees_is_adopted(self):
        verdict = evaluate_witness(
            witness=witness(),
            expected_fleet_id=FLEET,
            expected_ship_ids=SHIPS,
            expected_quest_name="練習航海",
        )
        self.assertTrue(verdict.allowed)
        self.assertEqual(verdict.reason, "witness_agrees_with_fleet_identity")
        self.assertEqual(verdict.fleet_id, FLEET)

    def test_no_witness_is_not_evidence(self):
        verdict = evaluate_witness(
            witness=None,
            expected_fleet_id=FLEET,
            expected_ship_ids=SHIPS,
        )
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.reason, "witness_not_yet_observed")

    def test_a_witness_naming_another_fleet_is_refused(self):
        verdict = evaluate_witness(
            witness=witness(fleet_id=OTHER),
            expected_fleet_id=FLEET,
            expected_ship_ids=SHIPS,
        )
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.reason, "witness_fleet_mismatch")

    def test_a_witness_with_other_ships_is_refused(self):
        verdict = evaluate_witness(
            witness=witness(ship_ids=OTHER_SHIPS),
            expected_fleet_id=FLEET,
            expected_ship_ids=SHIPS,
        )
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.reason, "witness_ship_set_mismatch")

    def test_a_witness_with_another_expedition_name_is_refused(self):
        verdict = evaluate_witness(
            witness=witness(quest_name="警備任務"),
            expected_fleet_id=FLEET,
            expected_ship_ids=SHIPS,
            expected_quest_name="練習航海",
        )
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.reason, "witness_quest_name_mismatch")

    def test_a_witness_from_the_future_is_refused(self):
        verdict = evaluate_witness(
            witness=witness(response_received_at=NOW + timedelta(minutes=5)),
            expected_fleet_id=FLEET,
            expected_ship_ids=SHIPS,
            observed_at=NOW,
        )
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.reason, "witness_not_yet_observed")

    def test_an_unknown_expected_ship_set_does_not_block_the_witness(self):
        verdict = evaluate_witness(
            witness=witness(),
            expected_fleet_id=FLEET,
            expected_ship_ids=None,
        )
        self.assertTrue(verdict.allowed)


class DispatchCrossCheckTests(unittest.TestCase):
    def test_a_witness_matching_the_dispatch_is_accepted(self):
        record = DispatchRecord(
            fleet_id=FLEET,
            mission_id=1,
            ship_ids=SHIPS,
            quest_name="練習航海",
            observed_at=NOW,
        )
        verdict = cross_check_with_dispatch(witness=witness(), record=record)
        self.assertTrue(verdict.allowed)
        self.assertEqual(verdict.reason, "witness_agrees_with_dispatch_record")

    def test_a_witness_naming_another_fleet_conflicts(self):
        record = DispatchRecord(fleet_id=OTHER, mission_id=1, ship_ids=SHIPS)
        verdict = cross_check_with_dispatch(witness=witness(), record=record)
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.reason, "witness_conflicts_with_dispatch_record")

    def test_a_witness_with_other_ships_conflicts(self):
        record = DispatchRecord(
            fleet_id=FLEET, mission_id=1, ship_ids=OTHER_SHIPS
        )
        verdict = cross_check_with_dispatch(witness=witness(), record=record)
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.reason, "witness_conflicts_with_dispatch_record")

    def test_a_witness_of_another_expedition_conflicts(self):
        record = DispatchRecord(
            fleet_id=FLEET,
            mission_id=3,
            ship_ids=SHIPS,
            quest_name="警備任務",
        )
        verdict = cross_check_with_dispatch(witness=witness(), record=record)
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.reason, "witness_conflicts_with_dispatch_record")

    def test_without_a_dispatch_record_the_witness_is_only_observed(self):
        verdict = cross_check_with_dispatch(witness=witness(), record=None)
        self.assertTrue(verdict.allowed)
        self.assertEqual(verdict.reason, "witness_observed_without_dispatch_record")

    def test_a_dispatch_record_without_a_name_is_not_a_conflict(self):
        record = DispatchRecord(
            fleet_id=FLEET, mission_id=1, ship_ids=SHIPS, quest_name=None
        )
        verdict = cross_check_with_dispatch(witness=witness(), record=record)
        self.assertTrue(verdict.allowed)


class WitnessPayloadTests(unittest.TestCase):
    def test_the_durable_payload_carries_no_game_actions(self):
        payload = witness_fields(witness())
        self.assertEqual(payload["fleet_id"], FLEET)
        self.assertEqual(payload["quest_name"], "練習航海")
        self.assertEqual(payload["ship_ids"], list(SHIPS))
        self.assertIs(payload["game_api_requests"], False)
        self.assertIs(payload["game_input_sent"], False)

    def test_the_payload_round_trips(self):
        payload = witness_fields(witness())
        parsed = parse_witness(payload)
        self.assertEqual(parsed.identity_key(), witness().identity_key())


class ResultReceiptAndCycleIdentityTests(unittest.TestCase):
    def test_result_codes_are_classified_strictly(self):
        self.assertEqual(witness(clear_result=0).result_kind, "failure")
        self.assertEqual(witness(clear_result=1).result_kind, "success")
        self.assertEqual(witness(clear_result=2).result_kind, "great_success")
        self.assertEqual(witness(clear_result=9).result_kind, "unknown")

    def test_a_complete_reward_receipt_is_parsed_and_persisted(self):
        receipt = {
            "status": "complete",
            "material": [10, 20, 30, 40],
            "useitem_flags": [1, 0],
            "items": [{"slot": 1, "useitem_id": 6, "count": 2}],
        }
        parsed = parse_witness(fields(reward_receipt=receipt))
        self.assertEqual(parsed.reward_receipt.status, "complete")
        self.assertEqual(parsed.reward_receipt.material, (10, 20, 30, 40))
        self.assertEqual(parsed.reward_receipt.useitem_flags, (1, 0))
        self.assertEqual(parsed.reward_receipt.items, ((1, 6, 2),))
        persisted = witness_fields(parsed)
        rebuilt = parse_witness(persisted)
        self.assertEqual(rebuilt.reward_receipt, parsed.reward_receipt)

    def test_incomplete_success_receipt_cannot_be_promoted_to_complete(self):
        parsed = parse_witness(fields(reward_receipt={
            "status": "complete",
            "material": None,
            "useitem_flags": [1, 0],
            "items": [{"slot": 1, "useitem_id": 6, "count": 2}],
        }))
        self.assertEqual(parsed.reward_receipt.status, "incomplete")

    def test_failed_result_requires_no_reward_receipt_status(self):
        parsed = parse_witness(fields(
            clear_result=0,
            reward_receipt={
                "status": "no_reward",
                "material": None,
                "useitem_flags": None,
                "items": [],
            },
        ))
        self.assertEqual(parsed.result_kind, "failure")
        self.assertEqual(parsed.reward_receipt.status, "no_reward")

    def test_legacy_witness_without_receipt_remains_readable(self):
        parsed = parse_witness(fields())
        self.assertIsNone(parsed.reward_receipt)

    def test_same_fleet_mission_and_ships_on_distinct_cycles_are_not_deduplicated(self):
        later = NOW + timedelta(seconds=30)
        events = [
            {
                "event": MISSION_RESULT_EVENT,
                "fields": fields(
                    requested_at=(NOW - timedelta(seconds=1)).isoformat(),
                    response_received_at=NOW.isoformat(),
                ),
            },
            {
                "event": MISSION_RESULT_EVENT,
                "fields": fields(
                    requested_at=(later - timedelta(seconds=1)).isoformat(),
                    response_received_at=later.isoformat(),
                ),
            },
        ]
        observed = read_witnesses(events)
        self.assertEqual(len(observed), 2)
        latest = latest_witness_for_fleet(events, FLEET)
        self.assertEqual(latest.response_received_at, later)

    def test_dispatch_cross_check_rejects_a_witness_from_a_previous_cycle(self):
        record = DispatchRecord(
            fleet_id=FLEET,
            mission_id=1,
            ship_ids=SHIPS,
            quest_name="練習航海",
            observed_at=NOW + timedelta(seconds=10),
        )
        verdict = cross_check_with_dispatch(
            witness=witness(response_received_at=NOW),
            record=record,
        )
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.reason, "witness_predates_dispatch_record")

    def test_dispatch_cross_check_rejects_missing_witness_time(self):
        record = DispatchRecord(
            fleet_id=FLEET,
            mission_id=1,
            ship_ids=SHIPS,
            quest_name="練習航海",
            observed_at=NOW,
        )
        verdict = cross_check_with_dispatch(
            witness=witness(response_received_at=None),
            record=record,
        )
        self.assertFalse(verdict.allowed)
        self.assertEqual(
            verdict.reason, "witness_time_missing_for_dispatch_cross_check"
        )


    def test_a_non_string_receipt_status_is_treated_as_incomplete(self):
        parsed = parse_witness(fields(reward_receipt={
            "status": ["complete"],
            "material": [1, 2, 3, 4],
            "useitem_flags": [0, 0],
            "items": [],
        }))
        self.assertEqual(parsed.reward_receipt.status, "incomplete")


if __name__ == "__main__":
    unittest.main()
