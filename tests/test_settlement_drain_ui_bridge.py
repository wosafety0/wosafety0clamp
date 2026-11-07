"""The drain-to-driver bridge: page names in, one input out, no fleet id.

The bridge is the only place where the driver's screen vocabulary meets the
drain's page vocabulary, so these tests pin the mapping in both directions and
pin the two refusals that keep the drain from clicking something it does not
recognise.
"""
from __future__ import annotations

import unittest
from contextlib import contextmanager

from kancollebot.expeditions.settlement_drain import DrainPageState
from kancollebot.expeditions.settlement_drain_ui import (
    PAGE_TO_SCREEN,
    SCREEN_TO_PAGE,
    PoiSettlementDrainDriver,
    PoiSettlementDrainObserver,
)


class FakeDriver:
    def __init__(self, screen=None, home=False):
        self.screen = screen
        self.home = home
        self.advanced: list[str] = []
        self.probes = 0

    def probe_settlement_page(self):
        self.probes += 1
        return self.screen

    def probe_home_port(self):
        return self.home

    def advance_settlement_page(self, screen):
        self.advanced.append(screen)


class ObserverTests(unittest.TestCase):
    def test_the_three_reward_screens_map_onto_the_three_pages(self):
        expected = {
            "HOME_PORT_RETURN_NOTICE": DrainPageState.RETURN_NOTICE,
            "RETURN_RESULT": DrainPageState.EXPEDITION_RESULT,
            "BONUS_ITEMS": DrainPageState.REWARD_PANEL,
        }
        self.assertEqual(dict(SCREEN_TO_PAGE), expected)
        for screen, page in expected.items():
            observer = PoiSettlementDrainObserver(FakeDriver(screen=screen))
            self.assertIs(observer.observe(), page)

    def test_a_clean_home_port_reads_as_home_port(self):
        observer = PoiSettlementDrainObserver(FakeDriver(screen=None, home=True))
        self.assertIs(observer.observe(), DrainPageState.HOME_PORT)

    def test_no_screen_and_no_home_port_is_unknown_not_home(self):
        observer = PoiSettlementDrainObserver(FakeDriver(screen=None, home=False))
        self.assertIs(observer.observe(), DrainPageState.UNKNOWN)

    def test_an_unmapped_screen_name_is_unknown(self):
        observer = PoiSettlementDrainObserver(FakeDriver(screen="SOMETHING_NEW"))
        self.assertIs(observer.observe(), DrainPageState.UNKNOWN)

    def test_observation_never_advances_anything(self):
        driver = FakeDriver(screen="BONUS_ITEMS")
        PoiSettlementDrainObserver(driver).observe()
        self.assertEqual(driver.advanced, [])

    def test_a_driver_without_the_probes_is_refused(self):
        with self.assertRaisesRegex(ValueError, "probe_settlement_page"):
            PoiSettlementDrainObserver(object())


class DriverTests(unittest.TestCase):
    def test_each_page_advances_the_matching_screen(self):
        for page, screen in PAGE_TO_SCREEN.items():
            driver = FakeDriver()
            PoiSettlementDrainDriver(driver).advance(page)
            self.assertEqual(driver.advanced, [screen])

    def test_the_home_port_is_never_advanceable(self):
        driver = FakeDriver()
        with self.assertRaisesRegex(ValueError, "not an advanceable"):
            PoiSettlementDrainDriver(driver).advance(DrainPageState.HOME_PORT)
        self.assertEqual(driver.advanced, [])

    def test_unknown_and_dialog_pages_are_never_advanceable(self):
        for page in (
            DrainPageState.UNKNOWN,
            DrainPageState.UNKNOWN_DIALOG,
            DrainPageState.NETWORK_ERROR_POPUP,
        ):
            driver = FakeDriver()
            with self.assertRaises(ValueError):
                PoiSettlementDrainDriver(driver).advance(page)
            self.assertEqual(driver.advanced, [])

    def test_a_page_must_be_a_drain_page_state(self):
        with self.assertRaisesRegex(ValueError, "DrainPageState"):
            PoiSettlementDrainDriver(FakeDriver()).advance("BONUS_ITEMS")

    def test_a_driver_without_advance_is_refused(self):
        with self.assertRaisesRegex(ValueError, "advance_settlement_page"):
            PoiSettlementDrainDriver(object())


class _RecordingArbiter:
    """The shape of ``UiOperationArbiter``: one context manager per input."""

    def __init__(self):
        self.labels: list[str] = []
        self.held: list[str] = []

    @contextmanager
    def operation(self, label):
        self.labels.append(label)
        self.held.append(label)
        try:
            yield
        finally:
            self.held.pop()


class _LockAwareDriver(FakeDriver):
    """Records whether the shared mutex was held at the moment of the click."""

    def __init__(self, arbiter):
        super().__init__()
        self._arbiter = arbiter
        self.clicked_while_held: list[bool] = []

    def advance_settlement_page(self, screen):
        self.clicked_while_held.append(bool(self._arbiter.held))
        super().advance_settlement_page(screen)


class DrainDriverMutexTests(unittest.TestCase):
    """The global drain's click must sit inside the shared Poi UI mutex.

    It is the one visible click path that used to take no lock at all. The
    controllers hold it, and it is also what excludes a second process, so a
    drain clicking beside it defeats the exclusion the whole run depends on.
    """

    def test_the_input_is_sent_inside_the_shared_mutex(self):
        arbiter = _RecordingArbiter()
        driver = _LockAwareDriver(arbiter)

        PoiSettlementDrainDriver(driver, ui_arbiter=arbiter).advance(
            DrainPageState.EXPEDITION_RESULT
        )

        self.assertEqual(driver.advanced, ["RETURN_RESULT"])
        self.assertEqual(driver.clicked_while_held, [True])
        self.assertEqual(
            arbiter.labels, ["settlement-drain-expedition_result"]
        )
        self.assertEqual(arbiter.held, [])

    def test_each_advanced_page_takes_its_own_short_operation(self):
        arbiter = _RecordingArbiter()
        driver = FakeDriver()
        bridge = PoiSettlementDrainDriver(driver, ui_arbiter=arbiter)

        for page in (
            DrainPageState.RETURN_NOTICE,
            DrainPageState.EXPEDITION_RESULT,
            DrainPageState.REWARD_PANEL,
        ):
            bridge.advance(page)

        self.assertEqual(len(arbiter.labels), 3)
        self.assertEqual(len(set(arbiter.labels)), 3)

    def test_without_an_arbiter_the_bridge_behaves_exactly_as_before(self):
        driver = FakeDriver()

        PoiSettlementDrainDriver(driver).advance(DrainPageState.EXPEDITION_RESULT)

        self.assertEqual(driver.advanced, ["RETURN_RESULT"])

    def test_a_refused_page_never_takes_the_lock(self):
        arbiter = _RecordingArbiter()
        with self.assertRaisesRegex(ValueError, "not an advanceable"):
            PoiSettlementDrainDriver(
                FakeDriver(), ui_arbiter=arbiter
            ).advance(DrainPageState.UNKNOWN)

        self.assertEqual(arbiter.labels, [])


if __name__ == "__main__":
    unittest.main()
