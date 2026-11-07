"""Bind the Poi continuous UI driver to the global settlement drain protocols.

``settlement_drain`` deliberately knows nothing about fleets, weapons of choice
or Poi: it takes a page observer and a one-input driver. This module is the only
place that maps the driver's screen names onto ``DrainPageState``, so the drain
itself stays transport-free and testable, and the mapping has exactly one
definition.

Why this is not part of ``adapters.poi``: a Poi adapter must not import the
expedition layer (enforced by ``tests/test_import_direction_guard.py``).
``DrainPageState`` lives in ``expeditions``, so the binding lives here and the
driver exposes two *fleet-agnostic* public methods that this module calls:

* ``probe_settlement_page() -> str | None`` - which reward page is on screen;
* ``probe_home_port() -> bool`` - whether a stable Home Port is on screen;
* ``advance_settlement_page(screen) -> None`` - the one input that page accepts.

None of those takes a fleet id, which is the point: the pages belong to the
account, and guessing which fleet owns them is exactly what P0.2 removes.
"""
from __future__ import annotations

from typing import Any, Mapping

from kancollebot.expeditions.settlement_drain import DrainPageState, DrainSample

__all__ = [
    "PAGE_TO_SCREEN",
    "SCREEN_TO_PAGE",
    "PoiSettlementDrainDriver",
    "PoiSettlementDrainObserver",
]

#: The driver's screen names, mapped onto the drain's page vocabulary. A screen
#: missing from this table can never be advanced by the drain.
SCREEN_TO_PAGE: Mapping[str, DrainPageState] = {
    "HOME_PORT_RETURN_NOTICE": DrainPageState.RETURN_NOTICE,
    "RETURN_RESULT": DrainPageState.EXPEDITION_RESULT,
    "BONUS_ITEMS": DrainPageState.REWARD_PANEL,
}

PAGE_TO_SCREEN: Mapping[DrainPageState, str] = {
    page: screen for screen, page in SCREEN_TO_PAGE.items()
}


def _require_driver(driver: Any, *methods: str) -> Any:
    for method in methods:
        if not callable(getattr(driver, method, None)):
            raise ValueError(f"driver must expose a callable {method}()")
    return driver


def _map_drain_state(state: str) -> DrainPageState:
    """Map a proven UI state name onto the drain vocabulary, fail-closed."""
    page = SCREEN_TO_PAGE.get(state)
    if page is not None:
        return page
    if state == "HOME_PORT":
        return DrainPageState.HOME_PORT
    # Any other proven screen is UNKNOWN, never a best guess: the drain stops
    # rather than clicks something it does not recognise.
    return DrainPageState.UNKNOWN


class PoiSettlementDrainObserver:
    """Answer "which page is visible right now" without side effects."""

    __slots__ = ("_driver",)

    def __init__(self, driver: Any) -> None:
        self._driver = _require_driver(driver, "probe_settlement_page", "probe_home_port")

    def observe(self) -> DrainPageState:
        # One probe round = one screenshot (plus at most one composited frame
        # when a hidden return notice could be present). The single-frame
        # classifier scores every candidate state from that same frame. It is
        # a PROBE ONLY: ``PoiSettlementDrainDriver.advance`` still demands two
        # consecutive independently captured frames before any click.
        probe = getattr(self._driver, "probe_drain_state", None)
        if callable(probe):
            state = probe()
            if state is not None:
                return _map_drain_state(state)
        screen = self._driver.probe_settlement_page()
        if screen is not None:
            page = SCREEN_TO_PAGE.get(screen)
            # An unfamiliar screen name is UNKNOWN, never a best guess: the
            # drain stops rather than clicks something it does not recognise.
            return page if page is not None else DrainPageState.UNKNOWN
        if self._driver.probe_home_port():
            return DrainPageState.HOME_PORT
        return DrainPageState.UNKNOWN

    def sample(self) -> DrainSample:
        """One page *and* one frame fingerprint, from a single capture.

        The drain uses the fingerprint only to tell "still animating" from
        "provably frozen"; it never becomes a page classification and never
        authorises a click. A driver without the single-frame probe has no
        motion signal, so the drain keeps its historical verdict.
        """
        probe = getattr(self._driver, "probe_drain_sample", None)
        if callable(probe):
            sampled = probe()
            if isinstance(sampled, tuple) and len(sampled) == 2:
                state, fingerprint = sampled
                if isinstance(state, str):
                    return DrainSample(
                        page=_map_drain_state(state), fingerprint=fingerprint
                    )
        return DrainSample(page=self.observe())


class PoiSettlementDrainDriver:
    """Perform exactly one input for a settlement page, then return."""

    __slots__ = ("_driver",)

    def __init__(self, driver: Any) -> None:
        self._driver = _require_driver(driver, "advance_settlement_page")

    def advance(self, page: DrainPageState) -> None:
        if not isinstance(page, DrainPageState):
            raise ValueError("page must be a DrainPageState")
        screen = PAGE_TO_SCREEN.get(page)
        if screen is None:
            # HOME_PORT, dialogs and UNKNOWN are not advanceable. The drain
            # contract says it only ever advances pages it recognises, so this
            # is a programming error worth failing loudly on.
            raise ValueError(f"{page.value} is not an advanceable settlement page")
        self._driver.advance_settlement_page(screen)
