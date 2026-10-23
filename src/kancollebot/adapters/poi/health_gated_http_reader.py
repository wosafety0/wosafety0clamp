"""Compose the read-only HTTP adapter, webhook freshness tracker, and page gate."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from kancollebot.adapters.poi.health_gated_reader import read_state_with_page_health
from kancollebot.adapters.poi.http_read_only import PoiHttpReadOnlyAdapter
from kancollebot.adapters.poi.state_observation_tracker import PoiStateObservationTracker
from kancollebot.adapters.poi.webhook_receiver import PoiWebhookReceiver
from kancollebot.core.ship_parser import ShipFieldMap


class PoiHealthGatedHttpReader:
    """Expose fleet/ship reads only after page and source-event checks pass.

    The receiver must already be started and Poi Forwarder must post events to it.
    No event or no verified page evidence means fail-closed.
    """

    def __init__(
        self,
        adapter: PoiHttpReadOnlyAdapter,
        receiver: PoiWebhookReceiver,
        tracker: PoiStateObservationTracker,
        *,
        probe_page: Callable[[], Any],
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        if not callable(probe_page):
            raise TypeError("probe_page must be callable")
        if now_provider is not None and not callable(now_provider):
            raise TypeError("now_provider must be callable")
        self._adapter = adapter
        self._receiver = receiver
        self._tracker = tracker
        self._probe_page = probe_page
        self._now_provider = now_provider

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
        self._drain_events()
        return read_state_with_page_health(
            probe_page=self._probe_page,
            read_state=lambda: self._adapter.get_observed_states(fields),
            latest_valid_state_at=lambda: self._latest_after_drain(),
            connection_failure_at=connection_failure_at,
            now_provider=self._now_provider,
        )

    def _latest_after_drain(self) -> datetime | None:
        self._drain_events()
        return self._tracker.latest_valid_state_at