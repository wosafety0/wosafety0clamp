"""Orchestrate a fail-closed page check around a read-only state read.

The caller must provide a timestamp from a trusted state-observation source
(e.g. a validated post-response event), not the local time when a cached payload
was merely fetched. This module never performs game actions.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable, TypeVar

from kancollebot.adapters.poi.cdp_probe import CdpPageProbe
from kancollebot.adapters.poi.page_health import (
    PageHealthObservation,
    PageHealthStatus,
    evaluate_page_health,
    require_healthy_page,
)

T = TypeVar("T")


class PageHealthGateError(RuntimeError):
    """Raised when page or state evidence does not permit a read result."""


def read_state_with_page_health(
    *,
    probe_page: Callable[[], CdpPageProbe],
    read_state: Callable[[], T],
    latest_valid_state_at: Callable[[], datetime | None],
    connection_failure_at: datetime | None = None,
    now_provider: Callable[[], datetime] | None = None,
) -> tuple[T, PageHealthObservation]:
    """Probe first, read state only if the page surface is healthy, then verify freshness.

    latest_valid_state_at must return the timestamp of the latest validated
    game-state observation, not a timestamp assigned to a cached read.
    State is never returned to the caller unless both checks pass.
    """
    clock = now_provider or (lambda: datetime.now(timezone.utc))
    probe = probe_page()
    observation = PageHealthObservation(
        checked_at=probe.checked_at,
        target_found=probe.target_found,
        document_ready_state=probe.document_ready_state,
        title=probe.title,
        url=probe.url,
        error_text=probe.error_text,
    )

    # Reject a missing or unhealthy renderer before consulting state evidence.
    # The only acceptable UNKNOWN reason at this stage is the not-yet-attached
    # source timestamp; every other UNKNOWN result fails closed immediately.
    page_only = evaluate_page_health(
        observation,
        now=clock(),
        connection_failure_at=connection_failure_at,
    )
    if page_only.status is not PageHealthStatus.HEALTHY and (
        page_only.status is PageHealthStatus.UNHEALTHY
        or page_only.reasons != ("valid_game_state_observation_missing",)
    ):
        try:
            require_healthy_page(page_only)
        except RuntimeError as exc:
            raise PageHealthGateError(str(exc)) from exc

    # Require validated source evidence before touching the HTTP adapter.
    # Recheck after the read as well, so a concurrent failure or stale event
    # cannot authorize a result retroactively.
    initial_state_at = latest_valid_state_at()
    observation = replace(observation, latest_valid_state_at=initial_state_at)
    preliminary = evaluate_page_health(
        observation,
        now=clock(),
        connection_failure_at=connection_failure_at,
    )
    try:
        require_healthy_page(preliminary)
    except RuntimeError as exc:
        raise PageHealthGateError(str(exc)) from exc

    state = read_state()
    # A renderer may navigate or disconnect during the HTTP read. Re-probe
    # before releasing the payload rather than reusing the pre-read snapshot.
    post_probe = probe_page()
    if (
        post_probe.target_found is not True
        or post_probe.title != probe.title
        or post_probe.url_identity != probe.url_identity
    ):
        raise PageHealthGateError("renderer_identity_changed_during_read")
    observation = PageHealthObservation(
        checked_at=post_probe.checked_at,
        target_found=post_probe.target_found,
        document_ready_state=post_probe.document_ready_state,
        title=post_probe.title,
        url=post_probe.url,
        error_text=post_probe.error_text,
    )
    state_at = latest_valid_state_at()
    observation = replace(observation, latest_valid_state_at=state_at)
    final_decision = evaluate_page_health(
        observation,
        now=clock(),
        connection_failure_at=connection_failure_at,
    )
    try:
        require_healthy_page(final_decision)
    except RuntimeError as exc:
        raise PageHealthGateError(str(exc)) from exc
    return state, observation
