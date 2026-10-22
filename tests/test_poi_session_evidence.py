"""Offline contract tests for explicit, time-bounded Poi session evidence."""

import unittest
from datetime import datetime, timedelta, timezone

from kancollebot.adapters.poi.session_evidence import (
    PoiSessionEvidenceError,
    validate_session_evidence,
)


NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


def evidence(**overrides):
    value = {
        "bridgeInstanceId": "test-instance",
        "responsePath": "api_port/port",
        "responseOk": True,
        "observedAt": "2026-10-22T11:59:30Z",
        "invalidated": False,
    }
    value.update(overrides)
    return value


class PoiSessionEvidenceTests(unittest.TestCase):
    def test_accepts_recent_successful_allowlisted_response(self):
        observed = validate_session_evidence(evidence(), now=NOW)
        self.assertEqual(observed, datetime(2026, 10, 22, 11, 59, 30, tzinfo=timezone.utc))

    def test_accepts_require_info_response(self):
        observed = validate_session_evidence(
            evidence(responsePath="api_get_member/require_info"), now=NOW
        )
        self.assertEqual(observed, datetime(2026, 10, 22, 11, 59, 30, tzinfo=timezone.utc))

    def test_missing_evidence_fails_closed(self):
        with self.assertRaisesRegex(PoiSessionEvidenceError, "missing"):
            validate_session_evidence(None, now=NOW)

    def test_persisted_state_or_unknown_response_is_not_evidence(self):
        with self.assertRaisesRegex(PoiSessionEvidenceError, "not an approved"):
            validate_session_evidence(evidence(responsePath="info.fleets"), now=NOW)

    def test_unsuccessful_response_fails_closed(self):
        with self.assertRaisesRegex(PoiSessionEvidenceError, "not explicitly successful"):
            validate_session_evidence(evidence(responseOk=False), now=NOW)

    def test_invalidation_fails_closed(self):
        with self.assertRaisesRegex(PoiSessionEvidenceError, "invalidated"):
            validate_session_evidence(evidence(invalidated=True), now=NOW)

    def test_reason_bearing_unavailable_evidence_fails_closed(self):
        with self.assertRaisesRegex(PoiSessionEvidenceError, "unavailable reason"):
            validate_session_evidence(evidence(reason="plugin_unloading"), now=NOW)

    def test_unexpected_fields_fail_closed(self):
        with self.assertRaisesRegex(PoiSessionEvidenceError, "unexpected fields"):
            validate_session_evidence(evidence(fleetData={"id": 1}), now=NOW)

    def test_missing_bridge_instance_fails_closed(self):
        with self.assertRaisesRegex(PoiSessionEvidenceError, "identity is missing"):
            validate_session_evidence(evidence(bridgeInstanceId=" "), now=NOW)

    def test_stale_evidence_fails_closed(self):
        with self.assertRaisesRegex(PoiSessionEvidenceError, "stale"):
            validate_session_evidence(
                evidence(observedAt="2026-10-22T11:57:59Z"), now=NOW
            )

    def test_future_evidence_fails_closed(self):
        with self.assertRaisesRegex(PoiSessionEvidenceError, "future"):
            validate_session_evidence(
                evidence(observedAt="2026-10-22T12:00:01Z"), now=NOW
            )


    def test_numeric_truthy_success_is_not_accepted(self):
        with self.assertRaisesRegex(PoiSessionEvidenceError, "not explicitly successful"):
            validate_session_evidence(evidence(responseOk=1), now=NOW)

    def test_evidence_at_exact_age_limit_is_accepted(self):
        observed = validate_session_evidence(
            evidence(observedAt="2026-10-22T11:58:00Z"),
            now=NOW,
            max_age=timedelta(seconds=120),
        )
        self.assertEqual(observed, datetime(2026, 10, 22, 11, 58, tzinfo=timezone.utc))

    def test_naive_timestamp_fails_closed(self):
        with self.assertRaisesRegex(PoiSessionEvidenceError, "include a timezone"):
            validate_session_evidence(
                evidence(observedAt="2026-10-22T11:59:30"), now=NOW
            )

    def test_naive_clock_fails_closed(self):
        with self.assertRaisesRegex(PoiSessionEvidenceError, "clock must be timezone-aware"):
            validate_session_evidence(
                evidence(), now=datetime(2026, 10, 22, 12, 0)
            )

    def test_negative_age_limit_fails_closed(self):
        with self.assertRaisesRegex(PoiSessionEvidenceError, "max_age must not be negative"):
            validate_session_evidence(evidence(), now=NOW, max_age=timedelta(seconds=-1))


if __name__ == "__main__":
    unittest.main()
