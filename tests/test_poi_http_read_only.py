import json
import unittest

from kancollebot.adapters.poi.http_read_only import PoiHttpReadOnlyAdapter


class FakeResponse:
    def __init__(self, body, status=200):
        self._body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, limit=-1):
        return self._body[:limit]


class FakeOpener:
    def __init__(self, payloads):
        self.payloads = payloads
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        endpoint = request.full_url.rsplit("/", 1)[-1]
        payload = self.payloads[endpoint]
        if isinstance(payload, tuple):
            body, status = payload
            return FakeResponse(body, status)
        return FakeResponse(payload)


def encoded(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


class PoiReadOnlyAdapterTests(unittest.TestCase):
    def setUp(self):
        self.payloads = {
            "fleets": encoded([
                {"api_id": 1, "api_name": "第1艦隊", "api_ship": [13, 73, 70, 10, 40, 62],
                 "api_mission": [0, 0, 0, 0]},
                {"api_id": 2, "api_name": "第2艦隊", "api_ship": [1, 3, -1, -1, -1, -1],
                 "api_mission": [1, 2, 123456, 0]},
            ]),
            "ships": encoded({"1": {}, "3": {}, "10": {}, "13": {}, "40": {}, "62": {}, "70": {}, "73": {}}),
        }
        self.opener = FakeOpener(self.payloads)
        self.adapter = PoiHttpReadOnlyAdapter(opener=self.opener)

    def test_reads_only_fleets_and_ships_with_get(self):
        status = self.adapter.get_status()
        self.assertEqual(status["source"], "poi-http-read-only")
        self.assertEqual(status["fleet_count"], 2)
        self.assertEqual(status["ship_record_count"], 8)
        self.assertEqual([request.get_method() for request, _ in self.opener.requests], ["GET", "GET"])
        self.assertEqual([request.full_url.rsplit("/", 1)[-1] for request, _ in self.opener.requests],
                         ["fleets", "ships"])

    def test_preserves_japanese_fleet_name(self):
        status = self.adapter.get_status()
        self.assertEqual(status["fleets"][0].fleet_name, "第1艦隊")

    def test_rejects_non_loopback_url(self):
        with self.assertRaisesRegex(ValueError, "loopback"):
            PoiHttpReadOnlyAdapter("http://example.com:17777")

    def test_rejects_https_and_url_credentials(self):
        with self.assertRaises(ValueError):
            PoiHttpReadOnlyAdapter("https://127.0.0.1:17777")
        with self.assertRaises(ValueError):
            PoiHttpReadOnlyAdapter("http://user:pass@127.0.0.1:17777")

    def test_rejects_missing_ship_record(self):
        self.payloads["ships"] = encoded({"1": {}, "3": {}})
        with self.assertRaisesRegex(ValueError, "missing ship records"):
            self.adapter.get_status()

    def test_rejects_invalid_utf8(self):
        self.payloads["fleets"] = b"\xff"
        with self.assertRaisesRegex(ValueError, "valid UTF-8"):
            self.adapter.get_status()

    def test_rejects_oversized_response(self):
        adapter = PoiHttpReadOnlyAdapter(opener=self.opener, max_response_bytes=4)
        with self.assertRaisesRegex(ValueError, "size limit"):
            adapter.get_status()

    def test_rejects_redirect_response(self):
        self.payloads["fleets"] = (encoded([]), 302)
        with self.assertRaisesRegex(OSError, "HTTP 302"):
            self.adapter.get_status()


if __name__ == "__main__":
    unittest.main()
