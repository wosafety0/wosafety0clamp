"""Compose the read-only HTTP adapter, webhook freshness tracker, and page gate."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

from kancollebot.adapters.poi.health_gated_reader import read_state_with_page_health
from kancollebot.adapters.poi.http_read_only import PoiHttpReadOnlyAdapter
from kancollebot.adapters.poi.state_observation_tracker import PoiStateObservationTracker
from kancollebot.adapters.poi.webhook_receiver import PoiWebhookReceiver
from kancollebot.core.expedition_planner import ExpeditionDecision
from kancollebot.core.expedition_safety import plan_fresh_safe_expedition
from kancollebot.core.ship_parser import ShipFieldMap


class PoiHealthGatedHttpReader:
    """The supported facade for page- and source-event-gated POI reads.

    The receiver must already be started and Poi Forwarder must post events to it.
    Every public read method probes the renderer before reading and withholds
    results unless the page and source-event freshness checks pass. The wrapped
    adapter remains a low-level primitive and must not be exposed to consumers.
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
            if self._now_provider is None:
                self._tracker.observe_event(event)
            else:
                self._tracker.observe_event(event, now=self._now_provider())

    def _latest_after_drain(self) -> datetime | None:
        self._drain_events()
        return self._tracker.latest_valid_state_at

    def _read_with_health(
        self,
        read_state: Callable[[], Any],
        *,
        connection_failure_at: datetime | None = None,
    ):
        """Run any read-only adapter method behind the same fail-closed gate."""
        self._drain_events()
        return read_state_with_page_health(
            probe_page=self._probe_page,
            read_state=read_state,
            latest_valid_state_at=self._latest_after_drain,
            connection_failure_at=connection_failure_at,
            now_provider=self._now_provider,
        )

    def get_observed_states(
        self,
        fields: ShipFieldMap,
        *,
        connection_failure_at: datetime | None = None,
    ):
        """Return validated fleet/ship observations and the health evidence."""
        return self._read_with_health(
            lambda: self._adapter.get_observed_states(fields),
            connection_failure_at=connection_failure_at,
        )

    def get_status(self, *, connection_failure_at: datetime | None = None):
        """Return the adapter status snapshot only after the health gate passes."""
        return self._read_with_health(
            self._adapter.get_status,
            connection_failure_at=connection_failure_at,
        )

    def diagnose(self, *, connection_failure_at: datetime | None = None):
        """Return the privacy-preserving diagnostic only after the health gate passes."""
        return self._read_with_health(
            self._adapter.diagnose,
            connection_failure_at=connection_failure_at,
        )

    def read_and_plan(
        self,
        fields: ShipFieldMap,
        *,
        mission_by_fleet: Mapping[int, int],
        max_age: timedelta = timedelta(seconds=10),
        dispatchable_fleet_ids: Sequence[int] = (2, 3, 4),
        connection_failure_at: datetime | None = None,
    ) -> tuple[Any, Any, ExpeditionDecision]:
        """Gate state reads before passing observations to the read-only planner."""
        (fleets_state, ships_state), _health = self.get_observed_states(
            fields,
            connection_failure_at=connection_failure_at,
        )
        now = self._now_provider() if self._now_provider is not None else datetime.now(timezone.utc)
        decision = plan_fresh_safe_expedition(
            fleets_state,
            ships_state,
            now=now,
            max_age=max_age,
            mission_by_fleet=mission_by_fleet,
            dispatchable_fleet_ids=dispatchable_fleet_ids,
        )
        return fleets_state, ships_state, decision
