import unittest
from unittest.mock import patch

from kancollebot.adapters.poi.cdp_targets import (
    CdpDiscoveryError,
    _RejectRedirects,
    _get_json,
    _validate_loopback_http_url,
    _validate_websocket_url,
    list_page_targets,
)


class CdpTargetLoopbackTests(unittest.TestCase):
    def test_localhost_http_endpoint_is_normalized_to_ipv4_loopback(self):
        self.assertEqual(
            _validate_loopback_http_url("http://localhost:9222"),
            "http://127.0.0.1:9222",
        )

    def test_localhost_websocket_endpoint_is_normalized_to_ipv4_loopback(self):
        self.assertEqual(
            _validate_websocket_url(
                "ws://localhost:9222/devtools/page/abc"
            ),
            "ws://127.0.0.1:9222/devtools/page/abc",
        )

    def test_non_loopback_hosts_are_rejected(self):
        for url in (
            "http://192.0.2.1:9222",
            "ws://192.0.2.1:9222/devtools/page/abc",
            "http://localhost.example:9222",
            "ws://localhost.example:9222/devtools/page/abc",
        ):
            with self.subTest(url=url):
                validator = (
                    _validate_loopback_http_url
                    if url.startswith("http:")
                    else _validate_websocket_url
                )
                error = ValueError if url.startswith("http:") else CdpDiscoveryError
                with self.assertRaises(error):
                    validator(url)

    def test_http_json_fetch_disables_environment_proxies(self):
        with patch(
            "kancollebot.adapters.poi.cdp_targets.urllib.request.build_opener"
        ) as build_opener:
            opener = build_opener.return_value
            response = opener.open.return_value.__enter__.return_value
            response.status = 200
            response.read.return_value = b"[]"

            self.assertEqual(_get_json("http://127.0.0.1:9222/json/list", 0.5), [])

        build_opener.assert_called_once()
        proxy_handler = build_opener.call_args.args[0]
        self.assertIsInstance(proxy_handler, __import__("urllib.request").request.ProxyHandler)
        self.assertEqual(proxy_handler.proxies, {})
        opener.open.assert_called_once()

    def test_http_json_fetch_installs_redirect_rejection_handler(self):
        with patch(
            "kancollebot.adapters.poi.cdp_targets.urllib.request.build_opener"
        ) as build_opener:
            response = build_opener.return_value.open.return_value.__enter__.return_value
            response.status = 200
            response.read.return_value = b"[]"
            self.assertEqual(_get_json("http://127.0.0.1:9222/json/list", 0.5), [])
        handlers = build_opener.call_args.args
        self.assertTrue(any(isinstance(handler, _RejectRedirects) for handler in handlers))

    def test_redirect_handler_refuses_external_and_loopback_redirects(self):
        import urllib.request

        handler = _RejectRedirects()
        request = urllib.request.Request("http://127.0.0.1:9222/json/list")
        for location in ("http://example.com/", "http://127.0.0.1:9333/"):
            with self.subTest(location=location):
                self.assertIsNone(
                    handler.redirect_request(request, None, 302, "Found", {}, location)
                )

    def test_http_discovery_uses_normalized_loopback_literal(self):
        with patch(
            "kancollebot.adapters.poi.cdp_targets._get_json",
            return_value=[],
        ) as get_json:
            self.assertEqual(
                list_page_targets("http://localhost:9222"),
                (),
            )
        get_json.assert_called_once_with(
            "http://127.0.0.1:9222/json/list", 2.0
        )


if __name__ == "__main__":
    unittest.main()
