"""Stage 3: one input, one action identity; one lifecycle per settlement/resupply.

Two things are pinned here, and they are the two the audit said were missing:

* every click row can be tied to *that* input (``action_id``), and a click that
  cannot be logged never happens at all - including the two supply clicks that
  used to be sent before anything was written;
* settlement and resupply are their own lifecycles with their own
  ``correlation_id``, and neither borrows a ``dispatch_id``.
"""
from __future__ import annotations

import unittest
from contextlib import contextmanager
from unittest.mock import patch

from kancollebot.adapters.poi.cdp_input import RecordedPoint
from kancollebot.adapters.poi.continuous_expedition_ui import (
    CdpContinuousExpeditionUiDriver,
    ContinuousUiDriverError,
)
from kancollebot.adapters.poi.visual_target import VisualTargetPoint
from kancollebot.observability.audit_policy import AuditLogUnavailable
from kancollebot.observability.sink import correlation_scope
from kancollebot.core.observation_contracts import UiStateEvidence
from kancollebot.expeditions.continuous_resupply_controller import (
    ContinuousResupplyController,
    ContinuousResupplyError,
)
from kancollebot.expeditions.continuous_scheduler import (
    FleetPhase,
    UiOperationArbiter,
)
from kancollebot.expeditions.continuous_settlement_controller import (
    ContinuousSettlementController,
    ContinuousSettlementError,
)


class CorrelationSink:
    """A sink that keeps the identity the scope bound, the way RunLogSink does.

    Events emitted inside ``correlation_scope`` carry whatever the scope set, so
    a row's ``action_id`` / ``correlation_id`` is only observable through the
    sink - which is exactly the property being tested.
    """

    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []
        self._bound: dict[str, object] = {}

    def __call__(self, event, fields):
        row = {"event": event}
        row.update(dict(fields))
        row.update(self._bound)
        self.rows.append(row)

    @contextmanager
    def correlation(self, **updates):
        saved = dict(self._bound)
        for key, value in updates.items():
            if value is None:
                self._bound.pop(key, None)
            else:
                self._bound[key] = value
        try:
            yield
        finally:
            self._bound = saved

    def events(self) -> list[str]:
        return [str(row["event"]) for row in self.rows]

    def rows_for(self, event: str) -> list[dict[str, object]]:
        return [row for row in self.rows if row["event"] == event]


class ScriptedScreen:
    def __init__(self, state: str) -> None:
        self.state = state

    def verify(self, expected: str, *, visual_attempts: int | None = None) -> UiStateEvidence:
        if expected != self.state:
            raise ContinuousUiDriverError(
                f"expected {expected}, screen shows {self.state}"
            )
        return UiStateEvidence(
            state=expected, consecutive_states=(expected, expected), evidence="scripted"
        )


class _NoDetails:
    def reference(self, expedition_id: str):
        return None

    def require(self, expedition_id: str, screenshot_base64: str):
        raise AssertionError("must not read expedition details")


class _NoMarks:
    def require_decision(self, screenshot_base64: str):
        raise AssertionError("must not read dispatch marks")

    def require_fleet(self, fleet_id: int, screenshot_base64: str):
        raise AssertionError("must not read fleet marks")

    def fleets(self) -> tuple[int, ...]:
        return (2, 3)


def build_driver(sink: CorrelationSink, *, fail_on: str | None = None):
    """A driver whose clicks are recorded and whose screen advances with them."""
    screen = ScriptedScreen("HOME_PORT")
    clicks: list[str] = []
    transitions = {
        "supply-menu": "SUPPLY_FLEET_2_SELECTED",
        "fleet-supply-all-button": "HOME_PORT",
        "home-port": "HOME_PORT",
    }

    def click(point: RecordedPoint) -> None:
        clicks.append(point.label)
        if point.label == fail_on:
            raise RuntimeError("input channel closed")
        if point.label in transitions:
            screen.state = transitions[point.label]

    driver = CdpContinuousExpeditionUiDriver(
        game_input=type("Input", (), {"click": staticmethod(click)})(),
        verify_state=screen.verify,
        event_sink=sink,
        details=_NoDetails(),
        marks=_NoMarks(),
        capture=lambda: "frame",
        sleep=lambda seconds: None,
        settle_seconds=0.0,
    )
    return driver, clicks


def button_target():
    return VisualTargetPoint(
        x=1135.0,
        y=615.0,
        label="fleet-supply-all-button",
        bounds=(1090, 600, 90, 30),
        region=(1050, 590, 145, 60),
        source_frame_sha256="0" * 64,
        locator="opencv-canny-button-v1",
    )


class BrokenAuditSink(CorrelationSink):
    """A sink whose critical writes fail, like a full or read-only run log."""

    def __init__(self, *, fail_on="continuous_ui_click_attempted") -> None:
        super().__init__()
        self.fail_on = fail_on

    def __call__(self, event, fields):
        if event == self.fail_on:
            raise AuditLogUnavailable(f"{event} could not be written")
        super().__call__(event, fields)


class ClickIdentityTests(unittest.TestCase):
    def test_each_click_row_carries_the_same_action_id(self):
        sink = CorrelationSink()
        driver, clicks = build_driver(sink)

        driver._click(RecordedPoint(600, 400, "reward-advance"), action="advance", step=1)

        self.assertEqual(clicks, ["reward-advance"])
        rows = sink.rows
        self.assertEqual(
            [row["event"] for row in rows],
            ["continuous_ui_click_attempted", "continuous_ui_click_sent"],
        )
        ids = {row["action_id"] for row in rows}
        self.assertEqual(len(ids), 1)
        self.assertIsNotNone(next(iter(ids)))

    def test_two_clicks_get_two_identities(self):
        sink = CorrelationSink()
        driver, _clicks = build_driver(sink)

        driver._click(RecordedPoint(1, 1, "a"), action="a", step=1)
        driver._click(RecordedPoint(2, 2, "b"), action="b", step=2)

        sent = sink.rows_for("continuous_ui_click_sent")
        self.assertEqual(len(sent), 2)
        self.assertNotEqual(sent[0]["action_id"], sent[1]["action_id"])
        # An action identity is not a dispatch identity: the click never invents
        # one, because only the composition root owns the dispatch.
        self.assertNotIn("dispatch_id", sent[0])

    def test_a_failed_click_is_ambiguous_and_never_sent(self):
        sink = CorrelationSink()
        driver, _clicks = build_driver(sink, fail_on="reward-advance")

        with self.assertRaisesRegex(RuntimeError, "input channel closed"):
            driver._click(RecordedPoint(600, 400, "reward-advance"), action="advance", step=1)

        self.assertEqual(
            [row["event"] for row in sink.rows],
            ["continuous_ui_click_attempted", "continuous_ui_click_ambiguous"],
        )
        # The attempted row still names the input, and it was written first.
        self.assertEqual(sink.rows[0]["target"], "reward-advance")


class ResupplyClickOrderingTests(unittest.TestCase):
    def test_every_supply_click_is_routed_through_the_one_input_boundary(self):
        sink = CorrelationSink()
        driver, clicks = build_driver(sink)

        with patch(
            "kancollebot.adapters.poi.continuous_expedition_ui.locate_fleet_supply_all_button",
            return_value=button_target(),
        ):
            driver.resupply_fleet(fleet_id=2)

        self.assertEqual(clicks, ["supply-menu", "fleet-supply-all-button", "home-port"])
        sent = sink.rows_for("continuous_ui_click_sent")
        self.assertEqual([row["target"] for row in sent], clicks)
        ids = [row["action_id"] for row in sent]
        self.assertEqual(len(set(ids)), 3)
        for row in sent:
            self.assertIsNotNone(row["action_id"])

    def test_a_closed_input_channel_on_the_supply_button_logs_ambiguity(self):
        sink = CorrelationSink()
        driver, clicks = build_driver(sink, fail_on="fleet-supply-all-button")

        with patch(
            "kancollebot.adapters.poi.continuous_expedition_ui.locate_fleet_supply_all_button",
            return_value=button_target(),
        ):
            with self.assertRaisesRegex(RuntimeError, "input channel closed"):
                driver.resupply_fleet(fleet_id=2)

        self.assertEqual(clicks, ["supply-menu", "fleet-supply-all-button"])
        events = sink.events()
        self.assertIn("continuous_ui_click_attempted", events)
        self.assertIn("continuous_ui_click_ambiguous", events)
        # The domain marker is only written after a successful click, so its
        # absence is what proves the ordering.
        self.assertNotIn("continuous_ui_supply_click_sent", events)


class FailClosedTests(unittest.TestCase):
    def test_a_broken_audit_log_sends_no_supply_click_at_all(self):
        # The whole point of writing ``attempted`` first: if the row cannot be
        # written, the input is never dispatched - including the two clicks that
        # used to be sent before anything was logged.
        sink = BrokenAuditSink()
        driver, clicks = build_driver(sink)

        with patch(
            "kancollebot.adapters.poi.continuous_expedition_ui.locate_fleet_supply_all_button",
            return_value=button_target(),
        ):
            with self.assertRaises(AuditLogUnavailable):
                driver.resupply_fleet(fleet_id=2)

        self.assertEqual(clicks, [])
        # Diagnostics written before the click are allowed to exist; what must
        # not exist is a click row, because no click happened.
        self.assertEqual(
            [row for row in sink.rows if row["event"].startswith("continuous_ui_click")],
            [],
        )

    def test_step_is_not_an_identity(self):
        # ``step`` is a legacy retry index: a supply reuses 2/3/4 and the
        # observation loop reuses its attempt number. Two inputs with the same
        # step must never collapse into one identity.
        sink = CorrelationSink()
        driver, _clicks = build_driver(sink)

        driver._click(RecordedPoint(1, 1, "a"), action="return_home_port", step=1)
        driver._click(RecordedPoint(1, 1, "a"), action="return_home_port", step=1)

        ids = [row["action_id"] for row in sink.rows_for("continuous_ui_click_sent")]
        self.assertEqual(len(ids), 2)
        self.assertNotEqual(ids[0], ids[1])

    def test_an_ambiguous_click_keeps_the_identity_of_its_attempt(self):
        sink = CorrelationSink()
        driver, _clicks = build_driver(sink, fail_on="reward-advance")

        with self.assertRaises(RuntimeError):
            driver._click(RecordedPoint(600, 400, "reward-advance"), action="advance", step=1)

        attempted = sink.rows_for("continuous_ui_click_attempted")
        ambiguous = sink.rows_for("continuous_ui_click_ambiguous")
        self.assertEqual(len(attempted), 1)
        self.assertEqual(len(ambiguous), 1)
        self.assertEqual(attempted[0]["action_id"], ambiguous[0]["action_id"])

    def test_clicks_inside_one_dispatch_share_the_dispatch_id_only(self):
        sink = CorrelationSink()
        driver, _clicks = build_driver(sink)

        with correlation_scope(sink, dispatch_id="dispatch-20261106T000000Z-aabbccddeeff"):
            driver._click(RecordedPoint(1, 1, "a"), action="open_sortie", step=1)
            driver._click(RecordedPoint(2, 2, "b"), action="confirm_mission", step=4)

        sent = sink.rows_for("continuous_ui_click_sent")
        self.assertEqual(len(sent), 2)
        self.assertEqual({row["dispatch_id"] for row in sent}, {"dispatch-20261106T000000Z-aabbccddeeff"})
        self.assertEqual(len({row["action_id"] for row in sent}), 2)


class ParallelInputPathTests(unittest.TestCase):
    """The two other input boundaries, checked rather than assumed.

    The audit found three places that dispatch a physical input. Only the
    continuous driver's ``_click`` mints an ``action_id``; the other two are
    excluded on purpose, and these tests make the exclusion explicit so a future
    change cannot quietly add a fourth unlabelled click site.
    """

    def test_the_refresh_path_is_correlated_by_refresh_id_not_action_id(self):
        import pathlib

        source = pathlib.Path(
            "src/kancollebot/adapters/poi/visible_ui_refresh.py"
        ).read_text(encoding="utf-8")
        # It already has its own lifecycle identity, stamped on *every* row of a
        # refresh by ``_emit``, and its click ordering is already
        # attempted-before-input. Adding a second, finer id would give two
        # overlapping identities for the same input.
        self.assertIn("refresh_id", source)
        self.assertIn("_refresh_context", source)
        self.assertNotIn("action_id", source)

    def test_the_unused_cdp_driver_is_not_wired_into_any_production_path(self):
        import pathlib

        offenders = []
        for folder in ("src", "tools"):
            for path in pathlib.Path(folder).rglob("*.py"):
                if path.name == "cdp_ui_driver.py":
                    continue
                text = path.read_text(encoding="utf-8")
                if "cdp_ui_driver" in text or "PoiCdpContinuousUiDriver" in text:
                    offenders.append(str(path))
        self.assertEqual(offenders, [])


class _FakeScheduler:
    dispatch_fleet_ids = (2,)

    def fleet(self, fleet_id):
        raise AssertionError("the audit gate runs before any fleet state is read")


class _RecordingUi:
    def __init__(self):
        self.calls: list[str] = []

    def resupply_fleet(self, *, fleet_id):
        self.calls.append(f"resupply:{fleet_id}")
        return UiStateEvidence("HOME_PORT", ("HOME_PORT", "HOME_PORT"), "scripted")

    def collect_return_result(self, *, fleet_id):
        self.calls.append(f"collect:{fleet_id}")
        return UiStateEvidence("RETURN_RESULT", ("RETURN_RESULT", "RETURN_RESULT"), "scripted")


class AuditGateTests(unittest.TestCase):
    """A log that cannot be written must stop the action, not just go missing."""

    def test_settlement_sends_nothing_when_the_audit_log_is_unwritable(self):
        sink = CorrelationSink()
        ui = _RecordingUi()

        def broken_gate():
            raise RuntimeError("run log is not writable")

        controller = ContinuousSettlementController(
            scheduler=_FakeScheduler(),
            state_reader=object(),
            ui_driver=ui,
            ui_arbiter=UiOperationArbiter(),
            event_sink=sink,
            allow_ui_actions=True,
            audit_gate=broken_gate,
        )

        with self.assertRaisesRegex(RuntimeError, "not writable"):
            controller.settle_one_returned(2)

        self.assertEqual(ui.calls, [])
        self.assertEqual(sink.rows, [])

    def test_resupply_sends_nothing_when_the_audit_log_is_unwritable(self):
        sink = CorrelationSink()
        ui = _RecordingUi()

        def broken_gate():
            raise RuntimeError("run log is not writable")

        controller = ContinuousResupplyController(
            scheduler=_FakeScheduler(),
            ui_driver=ui,
            fleet_data_provider=lambda: (_ for _ in ()).throw(
                AssertionError("the audit gate runs before any supply data is read")
            ),
            ui_arbiter=UiOperationArbiter(),
            event_sink=sink,
            allow_ui_actions=True,
            audit_gate=broken_gate,
        )

        with self.assertRaisesRegex(RuntimeError, "not writable"):
            controller.resupply_one_needed(2)

        self.assertEqual(ui.calls, [])
        self.assertEqual(sink.rows, [])

    def test_settlement_opens_its_own_lifecycle(self):
        sink = CorrelationSink()
        controller = ContinuousSettlementController(
            scheduler=_FakeScheduler(),
            state_reader=object(),
            ui_driver=_RecordingUi(),
            ui_arbiter=UiOperationArbiter(),
            event_sink=sink,
            allow_ui_actions=True,
            audit_gate=lambda: None,
        )

        # The state reader is a bare object, so the read fails immediately;
        # the lifecycle row is what this test is about.
        with self.assertRaises(Exception):
            controller.settle_one_returned(2)

        started = sink.rows_for("settlement_started")
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0]["lifecycle_kind"], "settlement")
        self.assertIsNotNone(started[0]["correlation_id"])
        self.assertNotIn("dispatch_id", started[0])


if __name__ == "__main__":
    unittest.main()
