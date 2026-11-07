"""Phase 4: the minimal navigation fallback is a pure route, not a navigator.

The contract this suite pins:

* one route per coarse class - HOME continues, MENU delegates to the existing
  Home Port routine, MODAL is held for a business controller, UNKNOWN looks
  again;
* the module itself never clicks, captures or navigates, and no route ever
  claims click authority;
* ``UNKNOWN`` cannot become ``HOME`` by repetition: the function has no
  attempt counter, no clock and no default that returns HOME, so any number of
  observations of an UNKNOWN page returns the same route.
"""
from __future__ import annotations

import ast
import inspect
import unittest

from kancollebot.expeditions import page_navigation
from kancollebot.expeditions.page_navigation import (
    HOME,
    MENU,
    MODAL,
    NAVIGATION_HOME_PORT_RECOVERY,
    REASON_NO_EVIDENCE,
    REASON_PAGE_IS_HOME,
    REASON_PAGE_IS_MENU,
    REASON_PAGE_IS_MODAL,
    REASON_PAGE_IS_UNKNOWN,
    ROUTE_CONTINUE,
    ROUTE_HOLD_FOR_BUSINESS_HANDLER,
    ROUTE_HOME_NAVIGATION,
    ROUTE_OBSERVE_AGAIN,
    ROUTE_UNCHANGED,
    UNKNOWN,
    plan_page_fallback,
)


class RouteTableTests(unittest.TestCase):
    def test_home_continues_without_navigating(self):
        fallback = plan_page_fallback(HOME, "dispatch")
        self.assertEqual(fallback.route, ROUTE_CONTINUE)
        self.assertEqual(fallback.reason, REASON_PAGE_IS_HOME)
        self.assertIsNone(fallback.delegates_navigation)
        self.assertFalse(fallback.hold)
        self.assertFalse(fallback.reobserve)

    def test_menu_delegates_to_the_existing_home_port_routine(self):
        fallback = plan_page_fallback(MENU, "dispatch")
        self.assertEqual(fallback.route, ROUTE_HOME_NAVIGATION)
        self.assertEqual(fallback.reason, REASON_PAGE_IS_MENU)
        # The name, not a reimplementation: the routine keeps its own bounded
        # attempts, post-click proof and reward-screen refusal.
        self.assertEqual(
            fallback.delegates_navigation, NAVIGATION_HOME_PORT_RECOVERY
        )
        self.assertEqual(NAVIGATION_HOME_PORT_RECOVERY, "home_port_recovery")
        self.assertTrue(fallback.reobserve)

    def test_modal_is_held_without_a_hand_off(self):
        fallback = plan_page_fallback(MODAL, "dispatch")
        self.assertEqual(fallback.route, ROUTE_HOLD_FOR_BUSINESS_HANDLER)
        self.assertEqual(fallback.reason, REASON_PAGE_IS_MODAL)
        self.assertTrue(fallback.hold)
        # No navigation and no re-observation: the overlay stays for the
        # settlement / reward controller that owns it.
        self.assertIsNone(fallback.delegates_navigation)
        self.assertFalse(fallback.reobserve)

    def test_the_modal_route_name_does_not_promise_a_call(self):
        # The route is logged and read back during review, so the word has to
        # match the code. Phase 4 sends nothing on MODAL and stops; a route
        # called "business_handler" would claim a hand-off that never happens.
        self.assertEqual(
            ROUTE_HOLD_FOR_BUSINESS_HANDLER, "hold_for_business_handler"
        )
        self.assertIn("hold", ROUTE_HOLD_FOR_BUSINESS_HANDLER)
        for page in (HOME, MODAL, UNKNOWN):
            self.assertIsNone(
                plan_page_fallback(page, "dispatch").delegates_navigation, page
            )

    def test_only_the_menu_route_names_a_delegate(self):
        delegates = {
            page: plan_page_fallback(page, "dispatch").delegates_navigation
            for page in (HOME, MENU, MODAL, UNKNOWN)
        }
        self.assertEqual(delegates[MENU], NAVIGATION_HOME_PORT_RECOVERY)
        self.assertEqual(
            [page for page, name in delegates.items() if name is not None],
            [MENU],
            "MENU is the only class that hands work to an existing routine",
        )

    def test_unknown_is_observed_again(self):
        fallback = plan_page_fallback(UNKNOWN, "dispatch")
        self.assertEqual(fallback.route, ROUTE_OBSERVE_AGAIN)
        self.assertEqual(fallback.reason, REASON_PAGE_IS_UNKNOWN)
        self.assertTrue(fallback.reobserve)
        self.assertFalse(fallback.hold)
        self.assertIsNone(fallback.delegates_navigation)

    def test_untaught_or_missing_page_keeps_existing_behaviour(self):
        for page in (None, "", "TELEPORT", "home", "Menu"):
            fallback = plan_page_fallback(page, "dispatch")
            self.assertEqual(fallback.route, ROUTE_UNCHANGED, page)
            self.assertEqual(fallback.reason, REASON_NO_EVIDENCE, page)
            self.assertIsNone(fallback.delegates_navigation, page)

    def test_no_route_ever_authorises_a_click(self):
        for page in (HOME, MENU, MODAL, UNKNOWN, None, "TELEPORT"):
            fallback = plan_page_fallback(page, "dispatch")
            self.assertFalse(fallback.authorizes_click, page)
            self.assertFalse(fallback.as_log_fields()["authorizes_click"], page)


class UnknownIsNotPromotedTests(unittest.TestCase):
    def test_repeated_unknown_observations_never_become_home(self):
        # The phase-4 failure mode this guards: "after N observations treat an
        # unnamed page as Home Port so the loop can make progress".
        first = plan_page_fallback(UNKNOWN)
        for _ in range(500):
            again = plan_page_fallback(UNKNOWN)
            self.assertEqual(again, first)
        self.assertNotIn(first.route, (ROUTE_CONTINUE, ROUTE_HOME_NAVIGATION))
        self.assertNotEqual(first.route, ROUTE_UNCHANGED)

    def test_the_function_cannot_count_attempts(self):
        # Structural, not behavioural: with no attempt / elapsed / count
        # parameter there is nothing to compare against a threshold, so
        # promotion by repetition is not expressible.
        parameters = set(inspect.signature(plan_page_fallback).parameters)
        self.assertEqual(parameters, {"page", "intent"})

    def test_the_module_holds_no_mutable_state(self):
        # Constants only: every public UPPER_CASE name must be immutable, so
        # there is nowhere for an observation count to live.
        module_level = {
            name: value
            for name, value in vars(page_navigation).items()
            if name.isupper()
            and not inspect.ismodule(value)
            and not inspect.isfunction(value)
            and not inspect.isclass(value)
        }
        self.assertTrue(module_level, "the route constants must be module-level")
        for name, value in module_level.items():
            self.assertIsInstance(
                value,
                (str, tuple, frozenset, dict),
                f"{name} must be an immutable constant, not per-run state",
            )


class NoSecondNavigatorTests(unittest.TestCase):
    @staticmethod
    def _code_only() -> str:
        """Module source with every docstring removed, so prose cannot pass
        or fail a code check."""
        tree = ast.parse(inspect.getsource(page_navigation))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                continue
            body = getattr(node, "body", None)
            if not body:
                continue
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                first.value.value = ""
        return ast.unparse(tree)

    def test_the_module_contains_no_click_capture_or_transport(self):
        code = self._code_only()
        for forbidden in (
            ".click(",
            "capture_screenshot",
            "transport",
            "game_session",
            "time.sleep",
            "monotonic",
            "import time",
        ):
            self.assertNotIn(forbidden, code, forbidden)

    def test_the_module_imports_no_adapter(self):
        code = self._code_only()
        self.assertNotIn("kancollebot.adapters", code)

    def test_intent_is_recorded_but_never_changes_the_route(self):
        for page in (HOME, MENU, MODAL, UNKNOWN):
            routes = {
                plan_page_fallback(page, intent).route
                for intent in (None, "dispatch", "settlement", "resupply", "x")
            }
            self.assertEqual(len(routes), 1, page)

    def test_log_fields_carry_the_why(self):
        fields = plan_page_fallback(MENU, "dispatch").as_log_fields()
        self.assertEqual(fields["route"], ROUTE_HOME_NAVIGATION)
        self.assertEqual(fields["page"], MENU)
        self.assertEqual(fields["intent"], "dispatch")
        self.assertEqual(fields["delegates_navigation"], "home_port_recovery")
        self.assertFalse(fields["authorizes_click"])


if __name__ == "__main__":
    unittest.main()
