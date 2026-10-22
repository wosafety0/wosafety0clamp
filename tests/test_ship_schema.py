import unittest

from kancollebot.core.ship_schema import inspect_ship_schema


class ShipSchemaTests(unittest.TestCase):
    def test_only_field_names_types_and_counts_are_exposed(self):
        data = {
            "73": {"api_name": "PRIVATE_SHIP_NAME", "api_nowhp": 40, "api_locked": True},
            "74": {"api_name": "SECOND_PRIVATE_NAME", "api_nowhp": 20, "api_locked": False},
        }
        report = inspect_ship_schema(data)
        self.assertEqual(report.record_count, 2)
        self.assertEqual(report.sampled_records, 2)
        self.assertIn(("api_nowhp", "number", 2), report.fields)
        self.assertNotIn("PRIVATE_SHIP_NAME", str(report))
        self.assertNotIn("73", str(report))

    def test_sample_is_bounded(self):
        data = {str(i): {"hp": i} for i in range(1, 6)}
        report = inspect_ship_schema(data, max_records=2)
        self.assertEqual(report.sampled_records, 2)
        self.assertIn(("hp", "number", 2), report.fields)

    def test_invalid_payload_is_rejected(self):
        for value in (None, [], {"1": []}, {"x": {}}, {"0": {}}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                inspect_ship_schema(value)

    def test_field_limit_is_enforced(self):
        with self.assertRaises(ValueError):
            inspect_ship_schema({"1": {"a": 1, "b": 2}}, max_fields=1)

    def test_invalid_limits_are_rejected(self):
        for limit in (True, 0, 101):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                inspect_ship_schema({"1": {}}, max_records=limit)

    def test_mixed_field_types_are_preserved(self):
        report = inspect_ship_schema({"1": {"hp": 5}, "2": {"hp": None}})
        self.assertIn(("hp", "number", 1), report.fields)
        self.assertIn(("hp", "null", 1), report.fields)

    def test_malformed_record_outside_sample_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "must be objects"):
            inspect_ship_schema({"1": {"hp": 5}, "2": None}, max_records=1)

    def test_invalid_key_outside_sample_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "positive decimal IDs"):
            inspect_ship_schema({"1": {"hp": 5}, "invalid": {"hp": 6}}, max_records=1)

    def test_invalid_field_name_outside_sample_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "field names"):
            inspect_ship_schema({"1": {"hp": 5}, "2": {"": 6}}, max_records=1)


if __name__ == "__main__":
    unittest.main()
