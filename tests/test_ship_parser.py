import unittest

from kancollebot.core.ship_parser import ShipFieldMap, parse_ship_payload


FIELDS = ShipFieldMap(name="api_name", hp="api_nowhp", max_hp="api_maxhp")


class ShipParserTests(unittest.TestCase):
    def test_parses_explicit_schema_and_preserves_japanese(self):
        parsed = parse_ship_payload(
            {"73": {"api_name": "赤城", "api_nowhp": 45, "api_maxhp": 69}},
            fields=FIELDS,
        )
        self.assertEqual(parsed[73].name, "赤城")
        self.assertEqual((parsed[73].hp, parsed[73].max_hp), (45, 69))

    def test_empty_object_is_valid(self):
        self.assertEqual(parse_ship_payload({}, fields=FIELDS), {})

    def test_non_object_payload_rejected(self):
        with self.assertRaisesRegex(ValueError, "must be an object"):
            parse_ship_payload([], fields=FIELDS)

    def test_non_decimal_or_zero_ship_key_rejected(self):
        for key in ("abc", "0", "-1"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                parse_ship_payload(
                    {key: {"api_name": "Test", "api_nowhp": 1, "api_maxhp": 1}},
                    fields=FIELDS,
                )

    def test_non_object_record_rejected(self):
        with self.assertRaisesRegex(ValueError, "must be an object"):
            parse_ship_payload({"1": None}, fields=FIELDS)

    def test_missing_field_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing required fields"):
            parse_ship_payload({"1": {"api_name": "Test", "api_nowhp": 1}}, fields=FIELDS)

    def test_boolean_is_not_accepted_as_integer_hp(self):
        with self.assertRaisesRegex(ValueError, "must be integers"):
            parse_ship_payload(
                {"1": {"api_name": "Test", "api_nowhp": True, "api_maxhp": 10}},
                fields=FIELDS,
            )

    def test_invalid_hp_range_rejected(self):
        cases = ((11, 10), (-1, 10), (0, 0))
        for hp, max_hp in cases:
            with self.subTest(hp=hp, max_hp=max_hp), self.assertRaises(ValueError):
                parse_ship_payload(
                    {"1": {"api_name": "Test", "api_nowhp": hp, "api_maxhp": max_hp}},
                    fields=FIELDS,
                )

    def test_invalid_field_maps_rejected(self):
        for values in (("", "hp", "max"), ("name", "hp", "hp")):
            with self.subTest(values=values), self.assertRaises(ValueError):
                ShipFieldMap(name=values[0], hp=values[1], max_hp=values[2])

    def test_blank_name_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid name"):
            parse_ship_payload(
                {"1": {"api_name": "  ", "api_nowhp": 1, "api_maxhp": 10}},
                fields=FIELDS,
            )


if __name__ == "__main__":
    unittest.main()
