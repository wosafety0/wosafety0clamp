"""Phase 3: coarse page evidence may withhold an action, never create one.

The supervisor's intent still comes from FleetPhase alone (``_pending_kind``).
This suite pins the one-directional contract:

* fresh, accepted, incompatible page evidence stops the tick with a precise
  ``page_mismatch:<intent>:<page>`` reason and zero controller calls;
* anything else - no evidence, stale evidence, ``UNKNOWN`` - leaves the
  pre-phase-3 behaviour byte-identical;
* ``UNKNOWN`` is never read as Home Port, and page evidence can never satisfy
  a safety gate: with ``allow_ui_actions`` false the tick still refuses with
  ``ui_actions_disabled`` no matter how good the page looks.
"""
from __future__ import annotations

import time
import unittest
from contextlib import contextmanager
from types import SimpleNamespace

from kancollebot.expeditions.continuous_scheduler import FleetPhase
from kancollebot.expeditions.continuous_supervisor import (
    ContinuousExpeditionSupervisor,
)
from kancollebot.expeditions.page_intent import (
    ACTION_DISPATCH,
    ACTION_RESUPPLY,
    ACTION_SETTLEMENT,
    DEFAULT_EVIDENCE_MAX_AGE_SECONDS,
    REASON_COMPATIBLE,
    REASON_INCOMPATIBLE,
    REASON_NO_EVIDENCE,
    REASON_STALE_EVIDENCE,
    REASON_UNKNOWN_INTENT,
    REASON_UNKNOWN_PAGE,
    PageEvidence,
    check_page_evidence,
)
from kancollebot.expeditions.page_navigation import (
    NAVIGATION_HOME_PORT_RECOVERY,
    ROUTE_HOLD_FOR_BUSINESS_HANDLER,
    ROUTE_HOME_NAVIGATION,
)


# -- doubles ---------------------------------------------------------------

class FakeScheduler:
    dispatch_fleet_ids = (2, 3)

    def __init__(self, phases, home_verified=None):
        self.states = {
            fleet_id: SimpleNamespace(
                phase=phase,
                home_port_verified=(home_verified or {}).get(fleet_id, False),
            )
            for fleet_id, phase in phases.items()
        }

    def fleet(self, fleet_id):
        return self.states[fleet_id]


class FakeMonitor:
    def __init__(self, scheduler, event_sink=None):
        self.scheduler = scheduler
        self.event_sink = event_sink

    def tick(self):
        return SimpleNamespace(fleets=())


class FakeDispatchController:
    def __init__(self, scheduler, *, enabled=True, event_sink=None):
        self.scheduler = scheduler
        self.event_sink = event_sink
        self.allow_ui_actions = enabled
        self.calls = []

    def dispatch_one_ready(self, fleet_id, *, dispatch_id=None):
        self.calls.append(fleet_id)
        self.scheduler.states[fleet_id].phase = FleetPhase.EXPEDITION_ACTIVE
        self.scheduler.states[fleet_id].home_port_verified = True
        return SimpleNamespace(fleet_id=fleet_id)


class FakeSettlementController:
    """The settlement controller reduced to what the supervisor touches."""

    def __init__(self, scheduler, *, enabled=True, event_sink=None):
        self.scheduler = scheduler
        self.event_sink = event_sink
        self.allow_ui_actions = enabled
        self.calls = []

    def settle_one_returned(self, fleet_id):
        self.calls.append(fleet_id)
        self.scheduler.states[fleet_id].phase = FleetPhase.RESUPPLY_REQUIRED
        return SimpleNamespace(fleet_id=fleet_id, settled=True)


class RecordingSink:

    def __init__(self):
        self.rows = []

    def __call__(self, event, fields):
        row = {"event": event}
        row.update(dict(fields))
        self.rows.append(row)

    @contextmanager
    def correlation(self, **updates):
        yield

    def events(self):
        return [row["event"] for row in self.rows]

    def one(self, event):
        rows = [row for row in self.rows if row["event"] == event]
        assert len(rows) == 1, f"expected one {event}, got {rows}"
        return rows[0]


def _evidence(page, *, age=0.0, detail=None):
    return PageEvidence(
        page=page,
        detail_state=detail,
        observed_at=time.monotonic() - age,
    )


# -- pure checks -----------------------------------------------------------

class CheckPageEvidenceTests(unittest.TestCase):
    def test_fresh_incompatible_page_blocks_the_action(self):
        check = check_page_evidence(ACTION_DISPATCH, _evidence("MENU"))
        self.assertFalse(check.allowed)
        self.assertEqual(check.reason, REASON_INCOMPATIBLE)
        self.assertEqual(check.page, "MENU")

    def test_fresh_compatible_page_allows_the_action(self):
        check = check_page_evidence(ACTION_DISPATCH, _evidence("HOME"))
        self.assertTrue(check.allowed)
        self.assertEqual(check.reason, REASON_COMPATIBLE)

    def test_unknown_page_is_no_evidence_and_never_home(self):
        # The phase-3 failure mode this guards: treating UNKNOWN as HOME to
        # keep dispatching. UNKNOWN carries no evidence at all.
        check = check_page_evidence(ACTION_DISPATCH, _evidence("UNKNOWN"))
        self.assertTrue(check.allowed)
        self.assertEqual(check.reason, REASON_UNKNOWN_PAGE)
        self.assertEqual(check.page, "UNKNOWN")

    def test_stale_evidence_cannot_veto(self):
        check = check_page_evidence(
            ACTION_DISPATCH,
            _evidence("MENU", age=DEFAULT_EVIDENCE_MAX_AGE_SECONDS + 1),
        )
        self.assertTrue(check.allowed)
        self.assertEqual(check.reason, REASON_STALE_EVIDENCE)

    def test_freshness_boundary_is_inclusive_at_the_limit(self):
        now = 1000.0
        at_limit = PageEvidence(page="MENU", observed_at=now - 15.0)
        just_over = PageEvidence(page="MENU", observed_at=now - 15.001)
        self.assertTrue(at_limit.is_fresh(now))
        self.assertFalse(just_over.is_fresh(now))

    def test_no_evidence_keeps_existing_behaviour(self):
        check = check_page_evidence(ACTION_DISPATCH, None)
        self.assertTrue(check.allowed)
        self.assertEqual(check.reason, REASON_NO_EVIDENCE)

    def test_no_intent_means_no_veto(self):
        check = check_page_evidence(None, _evidence("MODAL"))
        self.assertTrue(check.allowed)
        self.assertEqual(check.reason, REASON_UNKNOWN_INTENT)

    def test_unknown_intent_keeps_existing_behaviour(self):
        check = check_page_evidence("teleport", _evidence("MODAL"))
        self.assertTrue(check.allowed)
        self.assertEqual(check.reason, REASON_UNKNOWN_INTENT)

    def test_resupply_may_start_from_home_or_menu_but_not_an_overlay(self):
        self.assertTrue(check_page_evidence(ACTION_RESUPPLY, _evidence("HOME")).allowed)
        self.assertTrue(check_page_evidence(ACTION_RESUPPLY, _evidence("MENU")).allowed)
        self.assertFalse(check_page_evidence(ACTION_RESUPPLY, _evidence("MODAL")).allowed)

    def test_settlement_is_never_blocked_by_page_class(self):
        for page in ("HOME", "MENU", "MODAL", "UNKNOWN"):
            check = check_page_evidence(ACTION_SETTLEMENT, _evidence(page))
            self.assertTrue(check.allowed, page)


# -- supervisor integration ------------------------------------------------

class SupervisorPageEvidenceTests(unittest.TestCase):
    def make_supervisor(self, phases, *, enabled=True, home_verified=None):
        scheduler = FakeScheduler(phases, home_verified)
        sink = RecordingSink()
        monitor = FakeMonitor(scheduler, sink)
        controller = FakeDispatchController(
            scheduler, enabled=enabled, event_sink=sink
        )
        supervisor = ContinuousExpeditionSupervisor(
            monitor=monitor, dispatch_controller=controller
        )
        return supervisor, scheduler, controller, sink

    def test_no_evidence_dispatches_exactly_as_before(self):
        supervisor, _, controller, sink = self.make_supervisor({2: FleetPhase.READY})
        report = supervisor.tick()
        self.assertEqual(controller.calls, [2])
        self.assertIsNone(report.paused_reason)
        self.assertNotIn("supervisor_page_evidence", sink.events())

    def test_fresh_menu_evidence_withholds_the_dispatch(self):
        supervisor, _, controller, sink = self.make_supervisor({2: FleetPhase.READY})
        report = supervisor.tick(page_evidence=_evidence("MENU", detail="EXPEDITION_LIST"))
        self.assertEqual(controller.calls, [])
        self.assertEqual(report.dispatches, ())
        self.assertEqual(report.paused_reason, "page_mismatch:dispatch:MENU")
        mismatch = sink.one("supervisor_page_mismatch")
        self.assertEqual(mismatch["intent"], ACTION_DISPATCH)
        self.assertEqual(mismatch["detail_state"], "EXPEDITION_LIST")
        self.assertEqual(mismatch["clicks_sent_this_tick"], 0)

    def test_fresh_overlay_evidence_withholds_the_dispatch(self):
        supervisor, _, controller, _ = self.make_supervisor({2: FleetPhase.READY})
        report = supervisor.tick(page_evidence=_evidence("MODAL", detail="BONUS_ITEMS"))
        self.assertEqual(controller.calls, [])
        self.assertEqual(report.paused_reason, "page_mismatch:dispatch:MODAL")

    def test_unknown_evidence_still_dispatches_and_is_recorded_as_unknown(self):
        supervisor, _, controller, sink = self.make_supervisor({2: FleetPhase.READY})
        report = supervisor.tick(page_evidence=_evidence("UNKNOWN"))
        self.assertEqual(controller.calls, [2])
        self.assertIsNone(report.paused_reason)
        row = sink.one("supervisor_page_evidence")
        self.assertEqual(row["page"], "UNKNOWN")
        self.assertTrue(row["allowed"])
        self.assertEqual(row["reason"], REASON_UNKNOWN_PAGE)

    def test_stale_menu_evidence_does_not_withhold(self):
        supervisor, _, controller, sink = self.make_supervisor({2: FleetPhase.READY})
        stale = _evidence("MENU", age=DEFAULT_EVIDENCE_MAX_AGE_SECONDS + 5)
        report = supervisor.tick(page_evidence=stale)
        self.assertEqual(controller.calls, [2])
        self.assertIsNone(report.paused_reason)
        self.assertEqual(sink.one("supervisor_page_evidence")["reason"], REASON_STALE_EVIDENCE)

    def test_page_evidence_cannot_satisfy_the_ui_action_gate(self):
        # A perfect HOME read must never authorise a controller that is not
        # allowed to click: the existing gate still decides.
        supervisor, _, controller, _ = self.make_supervisor(
            {2: FleetPhase.READY}, enabled=False
        )
        report = supervisor.tick(page_evidence=_evidence("HOME"))
        self.assertEqual(controller.calls, [])
        self.assertEqual(report.paused_reason, "ui_actions_disabled")

    def test_page_evidence_never_creates_work_for_an_idle_scheduler(self):
        # An active fleet whose Home Port is verified needs nothing; a page
        # read cannot turn that into a dispatch.
        supervisor, _, controller, _ = self.make_supervisor(
            {2: FleetPhase.EXPEDITION_ACTIVE}, home_verified={2: True}
        )
        report = supervisor.tick(page_evidence=_evidence("HOME"))
        self.assertEqual(controller.calls, [])
        self.assertEqual(report.dispatches, ())
        self.assertIsNone(report.paused_reason)


# -- phase 4: the route attached to a withheld tick -------------------------

class SupervisorPageFallbackTests(unittest.TestCase):
    """A withheld tick now names the smallest route back - and still sends
    nothing. The supervisor cannot click, so the route is a decision the loop
    executes, never an action already taken."""

    def make_supervisor(self, phases, *, enabled=True, home_verified=None):
        scheduler = FakeScheduler(phases, home_verified)
        sink = RecordingSink()
        monitor = FakeMonitor(scheduler, sink)
        controller = FakeDispatchController(
            scheduler, enabled=enabled, event_sink=sink
        )
        supervisor = ContinuousExpeditionSupervisor(
            monitor=monitor, dispatch_controller=controller
        )
        return supervisor, scheduler, controller, sink

    def test_menu_mismatch_carries_the_home_navigation_route(self):
        supervisor, _, controller, sink = self.make_supervisor(
            {2: FleetPhase.READY}
        )
        report = supervisor.tick(page_evidence=_evidence("MENU", detail="EXPEDITION_LIST"))
        self.assertEqual(controller.calls, [])
        fallback = report.page_fallback
        self.assertIsNotNone(fallback)
        self.assertEqual(fallback.route, ROUTE_HOME_NAVIGATION)
        self.assertEqual(fallback.delegates_navigation, NAVIGATION_HOME_PORT_RECOVERY)
        self.assertFalse(fallback.authorizes_click)

        row = sink.one("supervisor_page_fallback")
        self.assertEqual(row["route"], ROUTE_HOME_NAVIGATION)
        self.assertEqual(row["page"], "MENU")
        self.assertEqual(row["intent"], ACTION_DISPATCH)
        self.assertEqual(row["delegates_navigation"], "home_port_recovery")
        self.assertFalse(row["authorizes_click"])
        # The phase-3 label is unchanged: same pause, same reason.
        self.assertEqual(report.paused_reason, "page_mismatch:dispatch:MENU")
        self.assertEqual(row["paused_reason"], report.paused_reason)

    def test_modal_mismatch_is_held_without_a_hand_off(self):
        supervisor, _, controller, sink = self.make_supervisor(
            {2: FleetPhase.READY}
        )
        report = supervisor.tick(page_evidence=_evidence("MODAL", detail="BONUS_ITEMS"))
        self.assertEqual(controller.calls, [])
        fallback = report.page_fallback
        self.assertEqual(fallback.route, ROUTE_HOLD_FOR_BUSINESS_HANDLER)
        self.assertTrue(fallback.hold)
        self.assertIsNone(fallback.delegates_navigation)
        self.assertFalse(fallback.authorizes_click)
        self.assertTrue(sink.one("supervisor_page_fallback")["hold"])

    def test_a_tick_that_was_not_withheld_carries_no_route(self):
        for page in ("HOME", "UNKNOWN"):
            supervisor, _, controller, sink = self.make_supervisor(
                {2: FleetPhase.READY}
            )
            report = supervisor.tick(page_evidence=_evidence(page))
            self.assertEqual(controller.calls, [2], page)
            self.assertIsNone(report.page_fallback, page)
            self.assertNotIn("supervisor_page_fallback", sink.events(), page)

    def test_no_evidence_tick_carries_no_route(self):
        supervisor, _, _, _ = self.make_supervisor({2: FleetPhase.READY})
        report = supervisor.tick()
        self.assertIsNone(report.page_fallback)

    def test_the_route_never_satisfies_the_ui_action_gate(self):
        # A planned MENU route must not make a disabled controller clickable.
        supervisor, _, controller, _ = self.make_supervisor(
            {2: FleetPhase.READY}, enabled=False
        )
        report = supervisor.tick(page_evidence=_evidence("MENU"))
        self.assertEqual(controller.calls, [])
        # The page veto fires first and carries a route; the gate still stands
        # for every tick that reaches the controller.
        self.assertEqual(report.paused_reason, "page_mismatch:dispatch:MENU")

    def test_unknown_evidence_never_produces_a_home_route(self):
        supervisor, _, controller, _ = self.make_supervisor({2: FleetPhase.READY})
        report = supervisor.tick(page_evidence=_evidence("UNKNOWN"))
        self.assertIsNone(report.page_fallback)
        self.assertEqual(controller.calls, [2])


class ModalsBelongToTheBusinessControllersTests(unittest.TestCase):
    """Phase 5: a MODAL page must never cost the settlement controller a tick.

    Phase 4 holds a MODAL page so the generic navigator cannot click through
    it. That hold is only correct because the settlement and reward controllers
    keep working: they are the ones that advance those pages with proof. These
    tests pin the distinction - the coarse class can withhold a *dispatch* or a
    *resupply*, and must not touch *settlement*.
    """

    def make_supervisor(self, phases, *, settlement_enabled=True):
        scheduler = FakeScheduler(phases)
        sink = RecordingSink()
        monitor = FakeMonitor(scheduler, sink)
        dispatch = FakeDispatchController(scheduler, event_sink=sink)
        settlement = FakeSettlementController(
            scheduler, enabled=settlement_enabled, event_sink=sink
        )
        supervisor = ContinuousExpeditionSupervisor(
            monitor=monitor,
            dispatch_controller=dispatch,
            settlement_controller=settlement,
        )
        return supervisor, scheduler, dispatch, settlement, sink

    def test_a_reward_page_does_not_block_the_settlement_controller(self):
        # The real situation: the return notice / result / bonus page is up and
        # the fleet still owes a settlement. The page is MODAL, and settlement
        # must proceed - the controller is the only thing allowed to advance it.
        for phase in (
            FleetPhase.RETURNED_PENDING_UI,
            FleetPhase.RETURN_RESULT,
            FleetPhase.BONUS_ITEMS,
        ):
            with self.subTest(phase=phase):
                supervisor, _, dispatch, settlement, sink = self.make_supervisor(
                    {2: phase}
                )
                report = supervisor.tick(
                    page_evidence=_evidence("MODAL", detail="BONUS_ITEMS")
                )
                self.assertEqual(settlement.calls, [2])
                self.assertEqual(dispatch.calls, [])
                self.assertIsNone(report.page_fallback)
                self.assertNotIn("supervisor_page_mismatch", sink.events())
                row = sink.one("supervisor_page_evidence")
                self.assertTrue(row["allowed"])
                self.assertEqual(row["reason"], REASON_COMPATIBLE)

    def test_the_hold_still_applies_to_a_resupply_intent(self):
        # MODAL is a hold for dispatch/resupply - it is a business overlay the
        # resupply walk cannot start from - but never for settlement.
        supervisor, _, dispatch, settlement, sink = self.make_supervisor(
            {2: FleetPhase.RESUPPLY_REQUIRED}
        )
        report = supervisor.tick(page_evidence=_evidence("MODAL", detail="RETURN_RESULT"))
        self.assertEqual(settlement.calls, [])
        self.assertEqual(report.paused_reason, "page_mismatch:resupply:MODAL")
        self.assertEqual(
            report.page_fallback.route, ROUTE_HOLD_FOR_BUSINESS_HANDLER
        )
        self.assertTrue(sink.one("supervisor_page_mismatch"))

    def test_a_disabled_settlement_controller_still_fails_closed(self):
        # The page class must not be what makes a settlement possible: with UI
        # actions disabled the supervisor pauses exactly as it did before
        # phase 3, whatever the page says.
        supervisor, _, _, settlement, _ = self.make_supervisor(
            {2: FleetPhase.RETURN_RESULT}, settlement_enabled=False
        )
        report = supervisor.tick(page_evidence=_evidence("MODAL"))
        self.assertEqual(settlement.calls, [])
        self.assertIsNotNone(report.paused_reason)


class RepeatedUnknownObservationTests(unittest.TestCase):
    """Phase 5: UNKNOWN repeated is still no evidence, and still no click.

    UNKNOWN is the most common real reading (transition frames, partially
    painted overlays). Observing it many times in a row must not accumulate
    into authority: the supervisor keeps its old behaviour, sends nothing, and
    never attaches a route. Repetition is not evidence.
    """

    def make_supervisor(self, phases, home_verified=None, *, enabled=True):
        scheduler = FakeScheduler(phases, home_verified)
        sink = RecordingSink()
        monitor = FakeMonitor(scheduler, sink)
        dispatch = FakeDispatchController(scheduler, enabled=enabled, event_sink=sink)
        settlement = FakeSettlementController(scheduler, event_sink=sink)
        supervisor = ContinuousExpeditionSupervisor(
            monitor=monitor,
            dispatch_controller=dispatch,
            settlement_controller=settlement,
        )
        return supervisor, scheduler, dispatch, settlement, sink

    def test_twenty_unknown_observations_send_nothing(self):
        supervisor, _, dispatch, settlement, sink = self.make_supervisor(
            {2: FleetPhase.EXPEDITION_ACTIVE}, home_verified={2: True}
        )
        for _ in range(20):
            report = supervisor.tick(page_evidence=_evidence("UNKNOWN"))
            self.assertEqual(report.dispatches, ())
            self.assertIsNone(report.page_fallback)
            self.assertIsNone(report.paused_reason)
        self.assertEqual(dispatch.calls, [])
        self.assertEqual(settlement.calls, [])
        self.assertNotIn("supervisor_page_mismatch", sink.events())
        # Every single observation was recorded as UNKNOWN - none of them was
        # rewritten into a class the loop could act on.
        rows = [row for row in sink.rows if row["event"] == "supervisor_page_evidence"]
        self.assertEqual(len(rows), 20)
        self.assertEqual({row["page"] for row in rows}, {"UNKNOWN"})
        self.assertEqual({row["allowed"] for row in rows}, {True})

    def test_unknown_under_a_real_intent_is_still_read_as_no_evidence(self):
        # A dispatch is wanted and UI actions are off: UNKNOWN neither unblocks
        # it nor turns into a route. The reason stays unknown_page across ticks.
        supervisor, _, dispatch, _, sink = self.make_supervisor(
            {2: FleetPhase.READY}, enabled=False
        )
        for _ in range(5):
            supervisor.tick(page_evidence=_evidence("UNKNOWN"))
        self.assertEqual(dispatch.calls, [])
        rows = [row for row in sink.rows if row["event"] == "supervisor_page_evidence"]
        self.assertEqual(len(rows), 5)
        self.assertEqual({row["reason"] for row in rows}, {REASON_UNKNOWN_PAGE})
        self.assertEqual({row["page"] for row in rows}, {"UNKNOWN"})
        self.assertNotIn("supervisor_page_fallback", sink.events())

    def test_unknown_never_unblocks_a_blocked_fleet(self):
        supervisor, _, dispatch, _, _ = self.make_supervisor({2: FleetPhase.BLOCKED})
        for _ in range(5):
            report = supervisor.tick(page_evidence=_evidence("UNKNOWN"))
            self.assertEqual(dispatch.calls, [])
            self.assertIsNone(report.page_fallback)
            self.assertIsNotNone(report.paused_reason)

    def test_unknown_carries_no_route_even_when_it_allows_the_tick(self):
        # Allowed-with-no-route is the whole point: the dispatch that follows is
        # the controller's own decision under its own two-frame Home Port gate,
        # not something the page class granted.
        supervisor, _, dispatch, _, _ = self.make_supervisor({2: FleetPhase.READY})
        report = supervisor.tick(page_evidence=_evidence("UNKNOWN"))
        self.assertEqual(dispatch.calls, [2])
        self.assertIsNone(report.page_fallback)


if __name__ == "__main__":
    unittest.main()
