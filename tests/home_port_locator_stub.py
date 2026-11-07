"""Shared test seam for the rotating 母港 badge locator.

The production drivers resolve the 母港 click point from a real frame.  Offline
driver tests have no game frame, so they inject the project's explicit
``StaticHomePortLocator`` seam instead of a coordinate.

Vision itself is covered by ``test_home_port_button_vision.py``; the driver
tests only assert that the click is routed through a locator at all.

Import it from a test module with::

    try:  # `unittest discover -s tests` puts this directory on sys.path
        from home_port_locator_stub import STUB_HOME_PORT_LOCATOR
    except ImportError:  # `python -m unittest tests.test_...` imports a package
        from tests.home_port_locator_stub import STUB_HOME_PORT_LOCATOR
"""
from __future__ import annotations

from kancollebot.adapters.poi.cdp_input import RecordedPoint
from kancollebot.adapters.poi.home_port_button_vision import StaticHomePortLocator

#: The reviewed control label is ``home-port``; only the frame evidence is stubbed.
STUB_HOME_PORT_LOCATOR = StaticHomePortLocator(RecordedPoint(50, 50, "home-port"))
