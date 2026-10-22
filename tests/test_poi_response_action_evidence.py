"""Tests for minimal evidence extraction from Poi Redux response actions."""

import unittest
from datetime import datetime, timezone

from kancollebot.adapters.poi.response_action_evidence import (
    evidence_from_poi_response_action,
)


def action(*, path="api_port/port", result=1, time=1792670370000):
    return {
        "type": "@@Response/kcsapi/" + path,
        "payload": {
            "body": {"api_result": result, "api_data": {"private": "must-not-leak"}},
            "time": time,
            "postBody": {"secret": "must-not-leak"},
        },
    }


class PoiResponseActionEvidenceTests(unittest.TestCase):
    def test_extracts_minimal_port_evidence(self):
        evidence = evidence_from_poi_response_action(
            action(), bridge_instance_id="instance-a"
        )
        self.assertEqual(evidence["responsePath"], "api_port/port")
        self.assertIs(evidence["responseOk"], True)
        self.assertEqual(
            evidence["observedAt"],
            datetime.fromtimestamp(1792670370000 / 1000, tz=timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
        )
        self.assertEqual(
            set(evidence),
            {"bridgeInstanceId", "responsePath", "responseOk", "observedAt", "invalidated"},
        )
        self.assertNotIn("private", repr(evidence))
        self.assertNotIn("secret", repr(evidence))

    def test_accepts_require_info(self):
        evidence = evidence_from_poi_response_action(
            action(path="api_get_member/require_info"),
            bridge_instance_id="instance-a",
        )
        self.assertEqual(evidence["responsePath"], "api_get_member/require_info")

    def test_rejects_other_actions(self):
        self.assertIsNone(
            evidence_from_poi_response_action(
                action(path="api_get_member/ship2"), bridge_instance_id="instance-a"
            )
        )

    def test_rejects_unsuccessful_response(self):
        for result in (0, 2, True, "1", None):
            with self.subTest(result=result):
                self.assertIsNone(
                    evidence_from_poi_response_action(
                        action(result=result), bridge_instance_id="instance-a"
                    )
                )

    def test_rejects_missing_or_invalid_timestamps(self):
        for value in (None, True, 0, -1, float("inf"), float("nan"), "1792670370000"):
            with self.subTest(value=value):
                self.assertIsNone(
                    evidence_from_poi_response_action(
                        action(time=value), bridge_instance_id="instance-a"
                    )
                )

    def test_rejects_missing_bridge_identity(self):
        self.assertIsNone(
            evidence_from_poi_response_action(action(), bridge_instance_id=" ")
        )

    def test_rejects_malformed_actions(self):
        for value in (None, {}, {"type": "@@Response/kcsapi/api_port/port"}):
            with self.subTest(value=value):
                self.assertIsNone(
                    evidence_from_poi_response_action(
                        value, bridge_instance_id="instance-a"
                    )
                )


if __name__ == "__main__":
    unittest.main()
