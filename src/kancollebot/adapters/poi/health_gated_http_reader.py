"""Compose the read-only HTTP adapter, webhook freshness tracker, and page gate."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from kancollebot.adapters.poi.health_gated_reader import read_state_with_page_health
from kancollebot.adapters.poi.http_read_only import PoiHttpReadOnlyAdapter
from kancollebot.adapters.poi.state_observation_tracker import PoiStateObservationTracker
from kancollebot.adapters.poi.webhook_receiver import PoiWebhookReceiver
from kancollebot.core.ship_parser import ShipFieldMap


class PoiHealthGatedHttpReader:
    """Expose validated fleet/ship reads only after page and source-event checks pass.

    The receiver must already be started and the Poi Forwarder must be configured
    to post events to it. No event or no verified page evidence means fail-closed.
    """

    def __init__(
        self,
        adapter: PoiHttpReadOnlyAdapter,
        receiver: PoiWebhookReceiver,
        tracker: PoiStateObservationTracker,
        *,
        probe_page: Any,
    ) -> None:
        if not callable(probe_page):
            raise TypeError("probe_page must be callable")
        self._adapter = adapter
        self._receiver = receiver
        self._tracker = tracker
        self._probe_page = probe_page

    def _drain_events(self) -> None:
        """Consume queued webhook events and retain only validated state timestamps."""
        while True:
            event = self._receiver.get_event(timeout=0)
            if event is None:
                return
            self._tracker.observe_event(event)

    def get_observed_states(
        self,
        fields: ShipFieldMap,
        *,
        connection_failure_at: datetime | None = None,
    ):
        """Probe first, read and validate state, then require recent source event evidence."""
        def read_state():
            return self._adapter.get_observed_states(fields)

        def latest_valid_state_at():
            self._drain_events()
            return self._tracker.latest_valid_state_at

        # Drain any queued event evidence before the probe; the health-gated reader
        # still probes the renderer before it calls read_state().
        self._drain_events()
        return read_state_with_page_health(
            probe_page=self._probe_page,
            read_state=read_state,
            latest_valid_state_at=latest_valid_state_at,
            connection_failure_at=connection_failure_at,
        )