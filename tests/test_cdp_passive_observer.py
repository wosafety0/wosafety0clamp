from __future__ import annotations

import unittest

from kancollebot.adapters.poi.cdp_passive_observer import (
    CdpPassiveNetworkObserver,
    PassiveObserverError,
)


class FakeTransport:
    def __init__(self, events=None, *, fail_methods=()):
        self.events = list(events or [])
        self.calls = []
        self.fail_methods = set(fail_methods)

    def send(self, method, params):
        self.calls.append((method, params))
        if method in self.fail_methods:
            raise RuntimeError("simulated transport failure")
        return {}

    def drain_events(self, limit=None):
        count = len(self.events) if limit is None else min(limit, len(self.events))
        batch, self.events = self.events[:count], self.events[count:]
        return batch


def request_event(url, method="GET"):
    return {
        "method": "Network.requestWillBeSent",
        "params": {
            "requestId": "r1",
            "request": {"url": url, "method": method},
        },
    }


def response_event(url, status=200):
    return {
        "method": "Network.responseReceived",
        "params": {
            "requestId": "r1",
            "response": {"url": url, "status": status},
        },
    }


class PassiveNetworkObserverTests(unittest.TestCase):
    def test_collects_only_exact_allowed_path_and_supported_events(self):
        transport = FakeTransport([
            request_event("https://game.example/kcsapi/api_port/port?api_token=[REDACTED]"),
            request_event("https://game.example/kcsapi/api_port/port/extra"),
            request_event("https://game.example/other/api_port/port"),
            {"method": "Network.loadingFinished", "params": {"requestId": "r1"}},
            response_event("https://game.example/kcsapi/api_port/port", 200),
        ])
        observer = CdpPassiveNetworkObserver(
            transport, allowed_paths={"/kcsapi/api_port/port"}
        )
        observer.start()
        result = observer.collect(timeout=0.05, max_events=5)
        observer.close()

        self.assertEqual([item["method"] for item in result], [
            "Network.requestWillBeSent",
            "Network.responseReceived",
        ])
        self.assertEqual(transport.calls, [
            ("Network.enable", {}),
            ("Network.disable", {}),
        ])

    def test_no_input_or_game_mutation_methods_are_exposed(self):
        observer = CdpPassiveNetworkObserver(
            FakeTransport(), allowed_paths={"/kcsapi/api_port/port"}
        )
        self.assertFalse(hasattr(observer, "click"))
        self.assertFalse(hasattr(observer, "reload"))
        self.assertFalse(hasattr(observer, "evaluate"))
        self.assertFalse(hasattr(observer, "intercept"))
        self.assertFalse(hasattr(observer, "replay"))

    def test_collect_requires_started_observer(self):
        observer = CdpPassiveNetworkObserver(
            FakeTransport(), allowed_paths={"/kcsapi/api_port/port"}
        )
        with self.assertRaises(PassiveObserverError):
            observer.collect(timeout=0.01)
        observer.close()
        with self.assertRaises(PassiveObserverError):
            observer.start()

    def test_start_failure_does_not_mark_observer_started(self):
        transport = FakeTransport(fail_methods={"Network.enable"})
        observer = CdpPassiveNetworkObserver(
            transport, allowed_paths={"/kcsapi/api_port/port"}
        )
        with self.assertRaises(RuntimeError):
            observer.start()
        with self.assertRaises(PassiveObserverError):
            observer.collect(timeout=0.01)
        observer.close()
        self.assertEqual([method for method, _ in transport.calls], ["Network.enable"])

    def test_disable_failure_does_not_reopen_or_retry(self):
        transport = FakeTransport(fail_methods={"Network.disable"})
        observer = CdpPassiveNetworkObserver(
            transport, allowed_paths={"/kcsapi/api_port/port"}
        )
        observer.start()
        observer.close()
        observer.close()
        self.assertEqual([method for method, _ in transport.calls], [
            "Network.enable", "Network.disable"
        ])

    def test_configuration_bounds_are_enforced(self):
        transport = FakeTransport()
        with self.assertRaises(ValueError):
            CdpPassiveNetworkObserver(transport, allowed_paths=set())
        with self.assertRaises(ValueError):
            CdpPassiveNetworkObserver(transport, allowed_paths={"relative/path"})
        with self.assertRaises(ValueError):
            CdpPassiveNetworkObserver(transport, allowed_paths={"/ok"}, max_batch_size=101)
        observer = CdpPassiveNetworkObserver(transport, allowed_paths={"/ok"})
        observer.start()
        with self.assertRaises(ValueError):
            observer.collect(timeout=31)
        with self.assertRaises(ValueError):
            observer.collect(timeout=0.1, max_events=101)
        observer.close()


if __name__ == "__main__":
    unittest.main()
