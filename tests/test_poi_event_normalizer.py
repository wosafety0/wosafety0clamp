import unittest
from datetime import datetime, timezone

from kancollebot.adapters.poi.event_normalizer import normalize_forwarder_event


class PoiEventNormalizerTests(unittest.TestCase):
    def event(self, **updates):
        payload = {
            "path": "/kcsapi/api_port/port",
            "stage": "response",
            "timestamp": 1792670000,
            "request": None,
            "response": {"api_result": 1, "api_data": {"api_material": []}},
        }
        payload.update(updates)
        return payload

    def test_accepts_explicit_success_response_with_timestamp(self):
        result = normalize_forwarder_event(self.event())
        self.assertTrue(result.state_eligible)
        self.assertEqual(result.reason, "validated_response_envelope")
        self.assertEqual(result.path, "/kcsapi/api_port/port")
        self.assertEqual(result.observed_at, datetime.fromtimestamp(1792670000, timezone.utc))

    def test_accepts_actual_forwarder_response_received_stage(self):
        result = normalize_forwarder_event(self.event(stage="response_received"))
        self.assertTrue(result.state_eligible)
        self.assertEqual(result.stage, "response_received")

    def test_accepts_poi_forwarder_millisecond_timestamp(self):
        seconds = 1792670000
        result = normalize_forwarder_event(self.event(timestamp=seconds * 1000))
        self.assertTrue(result.state_eligible)
        self.assertEqual(result.observed_at, datetime.fromtimestamp(seconds, timezone.utc))

    def test_rejects_non_finite_and_unrepresentable_numeric_timestamps(self):
        for timestamp in (float("nan"), float("inf"), 10 ** 1000, 0, -1):
            with self.subTest(timestamp=repr(timestamp)):
                result = normalize_forwarder_event(self.event(timestamp=timestamp))
                self.assertFalse(result.state_eligible)
                self.assertEqual(result.reason, "invalid_or_missing_timestamp")

    def test_map_resource_is_not_state_eligible(self):
        result = normalize_forwarder_event(self.event(path="kcs2/resources/map/1.json"))
        self.assertFalse(result.state_eligible)
        self.assertEqual(result.reason, "unsupported_resource_path")

    def test_unrelated_absolute_path_is_not_state_eligible(self):
        result = normalize_forwarder_event(self.event(path="/unrelated/resource"))
        self.assertFalse(result.state_eligible)
        self.assertEqual(result.reason, "unsupported_resource_path")

    def test_request_event_is_not_state_eligible(self):
        result = normalize_forwarder_event(self.event(stage="request"))
        self.assertFalse(result.state_eligible)
        self.assertEqual(result.reason, "not_a_response_stage")

    def test_api_failure_is_not_state_eligible(self):
        result = normalize_forwarder_event(self.event(response={"api_result": 0, "api_data": {}}))
        self.assertFalse(result.state_eligible)
        self.assertEqual(result.reason, "api_success_not_confirmed")

    def test_missing_response_is_not_state_eligible(self):
        result = normalize_forwarder_event(self.event(response=None))
        self.assertFalse(result.state_eligible)
        self.assertEqual(result.reason, "missing_or_invalid_response")

    def test_invalid_timestamp_fails_closed(self):
        result = normalize_forwarder_event(self.event(timestamp="not-a-time"))
        self.assertFalse(result.state_eligible)
        self.assertEqual(result.reason, "invalid_or_missing_timestamp")

    def test_naive_iso_timestamp_fails_closed(self):
        result = normalize_forwarder_event(self.event(timestamp="2026-10-22T12:00:00"))
        self.assertFalse(result.state_eligible)

    def test_out_of_range_timezone_aware_iso_timestamp_fails_closed(self):
        result = normalize_forwarder_event(
            self.event(timestamp="0001-01-01T00:00:00+14:00")
        )
        self.assertFalse(result.state_eligible)
        self.assertIsNone(result.observed_at)

    def test_url_query_and_fragment_are_removed_from_path(self):
        result = normalize_forwarder_event(self.event(path="https://example.test/kcsapi/api_port/port?token=secret#fragment"))
        self.assertEqual(result.path, "/kcsapi/api_port/port")

    def test_invalid_path_fails_closed(self):
        result = normalize_forwarder_event(self.event(path="../api_port/port"))
        self.assertFalse(result.state_eligible)
        self.assertEqual(result.reason, "invalid_path")

    def test_malformed_absolute_url_fails_closed_without_raising(self):
        for path in ("http://[", "https://[::1"):
            with self.subTest(path=path):
                result = normalize_forwarder_event(self.event(path=path))
                self.assertFalse(result.state_eligible)
                self.assertEqual(result.reason, "invalid_path")

    def test_boolean_api_result_is_not_accepted_as_integer_success(self):
        result = normalize_forwarder_event(self.event(response={"api_result": True}))
        self.assertFalse(result.state_eligible)

    def test_non_mapping_input_is_rejected(self):
        with self.assertRaises(TypeError):
            normalize_forwarder_event([])


if __name__ == "__main__":
    unittest.main()
