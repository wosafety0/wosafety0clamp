"""The minimal navigation fallback for a coarse page class.

Phase 3 let an incompatible page class *withhold* an action and stopped there.
Phase 4 answers the next question - "so what should happen about the page?" -
with the smallest possible policy, one route per coarse class:

===========  ===========================  ==================================
class        route                        what the loop may do
===========  ===========================  ==================================
``HOME``     ``continue``                 nothing; the intended controller
                                          proceeds
``MENU``     ``home_navigation``          delegate to the existing Home Port
                                          routine
``MODAL``    ``hold_for_business_handler`` hold: send nothing, then stop. Not a
                                          hand-off - see below
``UNKNOWN``  ``observe_again``            look again; never click
===========  ===========================  ==================================

The two delegating-looking rows are not the same thing, and the names say so:

* ``home_navigation`` really does call something. The caller runs the existing
  ``home_port_recovery`` with that routine's own bounds and proofs.
* ``hold_for_business_handler`` calls nothing. It states that the page belongs
  to a controller this loop is not (the settlement drain, the reward
  controller) and that sending a generic click would bypass the handler that
  proves what it advances. The caller therefore stops with the same pause
  label phase 3 used, leaving the page untouched. No code path here invokes
  the settlement or reward controller; they run on their own when they run.

Three boundaries are structural, not conventions:

* **This module never clicks, never captures and never navigates.** It returns
  a route; the caller executes it. ``MENU`` therefore does not implement a
  second Home Port walk - it names the routine that already exists
  (``NAVIGATION_HOME_PORT_RECOVERY``), which keeps its own bounded attempts,
  its own post-click state proof and its own reward-screen refusal. Rewriting
  that walk here would be a second navigator with weaker gates.
* **``MODAL`` is not dismissed generically.** The return notice, the result
  page and the reward pages advance on a click anywhere and belong to the
  settlement / reward controllers. A generic "close the overlay" route would
  bypass exactly the handlers that prove what they advance, so ``MODAL`` is a
  hold: zero clicks, zero navigation. The route is named
  ``hold_for_business_handler`` and not ``business_handler`` on purpose - the
  loop does not call that handler, it declines to act so the handler keeps its
  authority over the page.
* **``UNKNOWN`` cannot become ``HOME``.** There is no attempt counter, no
  elapsed-time fallback and no default argument that returns ``HOME`` or
  ``continue``: the route is a pure function of the page string, so observing
  an ``UNKNOWN`` page any number of times returns the same answer. Promotion
  by repetition is not a policy this module can express.

``authorizes_click`` is constant ``False`` on every route. Delegating a
navigation is not authorising it: the delegated routine re-proves the screen
after each click and refuses to click through a reward screen.
"""

from __future__ import annotations

from dataclasses import dataclass

ROUTE_UNCHANGED = "unchanged"
ROUTE_CONTINUE = "continue"
ROUTE_HOME_NAVIGATION = "home_navigation"
ROUTE_HOLD_FOR_BUSINESS_HANDLER = "hold_for_business_handler"
ROUTE_OBSERVE_AGAIN = "observe_again"

#: The coarse classes this policy was taught. Anything else - a page name from
#: a newer classifier, a conflict collapsed upstream - carries no evidence and
#: keeps the pre-phase-4 behaviour.
HOME = "HOME"
MENU = "MENU"
MODAL = "MODAL"
UNKNOWN = "UNKNOWN"

#: The one existing routine a ``MENU`` page may be handed to. Named rather than
#: imported so this module stays free of game-session and transport types; the
#: caller resolves the name to the function it already runs.
NAVIGATION_HOME_PORT_RECOVERY = "home_port_recovery"

REASON_NO_EVIDENCE = "no_evidence"
REASON_PAGE_IS_HOME = "page_is_home"
REASON_PAGE_IS_MENU = "page_is_menu"
REASON_PAGE_IS_MODAL = "page_is_modal"
REASON_PAGE_IS_UNKNOWN = "page_is_unknown"


@dataclass(frozen=True)
class PageFallback:
    """One route decision, with enough detail to audit it in the run log.

    ``hold`` means "send nothing and stop this tick": the page belongs to a
    controller this loop is not, and nothing in this module calls that
    controller. ``reobserve`` means the caller should read the page again
    before trying anything else, and must not click in the meantime.
    """

    route: str
    page: str | None
    intent: str | None
    reason: str
    delegates_navigation: str | None = None
    hold: bool = False
    reobserve: bool = False
    #: Constant. This module has no authority to click; it only routes.
    authorizes_click: bool = False

    def as_log_fields(self) -> dict[str, object]:
        """The fields the run log records for this decision."""
        return {
            "route": self.route,
            "page": self.page,
            "intent": self.intent,
            "reason": self.reason,
            "delegates_navigation": self.delegates_navigation,
            "hold": self.hold,
            "reobserve": self.reobserve,
            "authorizes_click": self.authorizes_click,
        }


def plan_page_fallback(page: str | None, intent: str | None = None) -> PageFallback:
    """Route one coarse page class. Pure: no clock, no counter, no session.

    ``intent`` is recorded for audit only. The route is decided by the page
    class alone, which is what makes "``UNKNOWN`` never becomes ``HOME``" a
    property of the code rather than a promise about the caller.
    """
    if page not in (HOME, MENU, MODAL, UNKNOWN):
        # ``None``, an empty string and any class this module was never taught
        # all mean "no page evidence": the caller keeps its existing behaviour
        # rather than being sent somewhere on a guess.
        return PageFallback(
            route=ROUTE_UNCHANGED,
            page=page,
            intent=intent,
            reason=REASON_NO_EVIDENCE,
        )
    if page == HOME:
        return PageFallback(
            route=ROUTE_CONTINUE,
            page=page,
            intent=intent,
            reason=REASON_PAGE_IS_HOME,
        )
    if page == MENU:
        return PageFallback(
            route=ROUTE_HOME_NAVIGATION,
            page=page,
            intent=intent,
            reason=REASON_PAGE_IS_MENU,
            delegates_navigation=NAVIGATION_HOME_PORT_RECOVERY,
            reobserve=True,
        )
    if page == MODAL:
        return PageFallback(
            route=ROUTE_HOLD_FOR_BUSINESS_HANDLER,
            page=page,
            intent=intent,
            reason=REASON_PAGE_IS_MODAL,
            hold=True,
        )
    return PageFallback(
        route=ROUTE_OBSERVE_AGAIN,
        page=page,
        intent=intent,
        reason=REASON_PAGE_IS_UNKNOWN,
        reobserve=True,
    )
