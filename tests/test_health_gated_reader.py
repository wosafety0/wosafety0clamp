import unittest
from datetime import datetime, timedelta, timezone

from kancollebot.adapters.poi.cdp_probe import CdpPageProbe
from kancollebot.adapters.poi.health_gated_reader import (
    PageHealthGateError,
    read_state_with_page_health,
)


class HealthGatedReaderTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
        self.probe = CdpPageProbe(
            checked_at=self.now,
            target_found=True,
            title="Poi - KanColle",
            url="app://poi",
            document_ready_state="complete",
            error_text="KanColle",
        )

    def call(self, probe=None, state_at=None, events=None, failure_at=None):
        events = events if events is not None else []
        return read_state_with_page_health(
            probe_page=lambda: (events.append("probe") or (probe or self.probe)),
            read_state=lambda: (events.append("read_state") or {"fleets": []}),
            latest_valid_state_at=lambda: (events.append("state_timestamp") or state_at),
            connection_failure_at=failure_at,
            now_provider=lambda: self.now,
        )

    def test_probe_runs_before_read_and_fresh_state_is_returned(self):
        events = []
        failure_at = self.now - timedelta(seconds=3)
        state, observation = self.call(
            state_at=self.now - timedelta(seconds=1), events=events, failure_at=failure_at
        )
        self.assertEqual(events, ["probe", "state_timestamp", "read_state", "probe", "state_timestamp"])
        self.assertEqual(state, {"fleets": []})
        self.assertEqual(observation.latest_valid_state_at, self.now - timedelta(seconds=1))

    def test_renderer_change_during_read_blocks_payload(self):
        events = []
        changed = CdpPageProbe(
            checked_at=self.now,
            target_found=True,
            title="Different renderer",
            url="app://different",
            document_ready_state="complete",
            error_text="",
        )
        probes = iter((self.probe, changed))
        with self.assertRaisesRegex(PageHealthGateError, "renderer_identity_changed"):
            read_state_with_page_health(
                probe_page=lambda: (events.append("probe") or next(probes)),
                read_state=lambda: (events.append("read_state") or {"fleets": []}),
                latest_valid_state_at=lambda: self.now - timedelta(seconds=1),
                now_provider=lambda: self.now,
            )
        self.assertEqual(events, ["probe", "read_state", "probe"])

    def test_post_read_connection_error_withholds_payload(self):
        events = []
        failed = CdpPageProbe(
            checked_at=self.now,
            target_found=True,
            title=self.probe.title,
            url=self.probe.url,
            document_ready_state="complete",
            error_text="net::ERR_CONNECTION_CLOSED",
        )
        probes = iter((self.probe, failed))
        with self.assertRaises(PageHealthGateError):
            read_state_with_page_health(
                probe_page=lambda: (events.append("probe") or next(probes)),
                read_state=lambda: (events.append("read_state") or {"secret": "fixture"}),
                latest_valid_state_at=lambda: self.now - timedelta(seconds=1),
                now_provider=lambda: self.now,
            )
        self.assertEqual(events, ["probe", "read_state", "probe"])

    def test_post_read_document_not_ready_withholds_payload(self):
        events = []
        loading = CdpPageProbe(
            checked_at=self.now,
            target_found=True,
            title=self.probe.title,
            url=self.probe.url,
            document_ready_state="loading",
            error_text=self.probe.error_text,
        )
        probes = iter((self.probe, loading))
        with self.assertRaises(PageHealthGateError):
            read_state_with_page_health(
                probe_page=lambda: (events.append("probe") or next(probes)),
                read_state=lambda: (events.append("read_state") or {"secret": "fixture"}),
                latest_valid_state_at=lambda: self.now - timedelta(seconds=1),
                now_provider=lambda: self.now,
            )
        self.assertEqual(events, ["probe", "read_state", "probe"])

    def test_post_read_target_missing_withholds_payload(self):
        events = []
        missing = CdpPageProbe(
            checked_at=self.now,
            target_found=False,
            title="",
            url="",
            document_ready_state=None,
            error_text="",
            probe_error="CdpDiscoveryError",
        )
        probes = iter((self.probe, missing))
        with self.assertRaises(PageHealthGateError):
            read_state_with_page_health(
                probe_page=lambda: (events.append("probe") or next(probes)),
                read_state=lambda: (events.append("read_state") or {"secret": "fixture"}),
                latest_valid_state_at=lambda: self.now - timedelta(seconds=1),
                now_provider=lambda: self.now,
            )
        self.assertEqual(events, ["probe", "read_state", "probe"])

    def test_connection_error_blocks_state_read(self):
        events = []
        bad_probe = CdpPageProbe(
            checked_at=self.now, target_found=True, title="Error", url="app://error",
            document_ready_state="complete", error_text="net::ERR_CONNECTION_CLOSED"
        )
        with self.assertRaises(RuntimeError):
            self.call(probe=bad_probe, state_at=self.now - timedelta(seconds=1), events=events)
        self.assertEqual(events, ["probe"])

    def test_stale_state_blocks_returning_read_payload(self):
        events = []
        with self.assertRaises(PageHealthGateError):
            self.call(state_at=self.now - timedelta(seconds=30), events=events)
        self.assertEqual(events, ["probe", "state_timestamp"])

    def test_state_not_observed_after_failure_is_blocked(self):
        failure_at = self.now - timedelta(seconds=2)
        events = []
        with self.assertRaises(PageHealthGateError):
            self.call(state_at=failure_at, failure_at=failure_at, events=events)
        self.assertEqual(events, ["probe", "state_timestamp"])

    def test_missing_target_blocks_state_read(self):
        events = []
        missing = CdpPageProbe(
            checked_at=self.now, target_found=False, title="", url="",
            document_ready_state=None, error_text="", probe_error="CdpDiscoveryError"
        )
        with self.assertRaises(RuntimeError):
            self.call(probe=missing, state_at=self.now - timedelta(seconds=1), events=events)
        self.assertEqual(events, ["probe"])


if __name__ == "__main__":
    unittest.main()
