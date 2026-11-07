"""Veto-only page evidence for the supervisor's existing FleetPhase decisions.

Phase 3 connects the phase-2 coarse page classification to the dispatch /
settlement / resupply decisions the supervisor already makes. The connection is
deliberately one-directional:

* **FleetPhase remains the only source of intent.** ``_pending_kind`` still
  decides whether a fleet needs settlement, resupply or dispatch; a page class
  can never create work, change a phase, or promote a fleet into an action.
* **Page evidence can only withhold, never permit.** If the page class is an
  accepted coarse class that the intended action cannot operate from, the
  supervisor holds the tick instead of acting. Everything else - no evidence,
  stale evidence, ``UNKNOWN`` - leaves the existing behaviour byte-identical.
  ``UNKNOWN`` is never read as ``HOME``: it simply carries no evidence.
* **No recovery logic here.** A mismatch is reported and held; navigating back
  is phase 4's job, not this module's.

Evidence is only meaningful while it is fresh. A verdict captured before a
30-minute timer sleep says nothing about the screen at dispatch time, so
``PageEvidence.is_fresh`` gates every check.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

ACTION_DISPATCH = "dispatch"
ACTION_RESUPPLY = "resupply"
ACTION_SETTLEMENT = "settlement"

PAGE_HOME = "HOME"
PAGE_MENU = "MENU"
PAGE_MODAL = "MODAL"
PAGE_UNKNOWN = "UNKNOWN"

KNOWN_PAGES = frozenset({PAGE_HOME, PAGE_MENU, PAGE_MODAL, PAGE_UNKNOWN})

#: Which coarse page classes each action can legitimately start from.
#:
#: * dispatch requires a proven Home Port (the dispatch controller's own
#:   two-frame HOME_PORT rule); a menu or an overlay means the screen moved.
#: * resupply starts from Home Port and walks through the supply pages, which
#:   are MENU. A business overlay on top means the reward/settlement
#:   controllers still owe that screen.
#: * settlement drains whatever page is up - it navigates from Home Port
#:   through the result and bonus pages - so every class is compatible and the
#:   check can never block it. It is listed for completeness, not for vetoes.
COMPATIBLE_PAGES: dict[str, frozenset[str]] = {
    ACTION_DISPATCH: frozenset({PAGE_HOME}),
    ACTION_RESUPPLY: frozenset({PAGE_HOME, PAGE_MENU}),
    ACTION_SETTLEMENT: frozenset({PAGE_HOME, PAGE_MENU, PAGE_MODAL}),
}

REASON_NO_EVIDENCE = "no_evidence"
REASON_STALE_EVIDENCE = "stale_evidence"
REASON_UNKNOWN_PAGE = "unknown_page"
REASON_COMPATIBLE = "compatible"
REASON_INCOMPATIBLE = "incompatible"
REASON_UNKNOWN_INTENT = "unknown_intent"

DEFAULT_EVIDENCE_MAX_AGE_SECONDS = 15.0


@dataclass(frozen=True)
class PageEvidence:
    """One coarse page verdict with the moment it was observed."""

    page: str
    detail_state: str | None = None
    observed_at: float = 0.0
    max_age_seconds: float = DEFAULT_EVIDENCE_MAX_AGE_SECONDS

    def is_fresh(self, now: float | None = None) -> bool:
        moment = time.monotonic() if now is None else now
        return (moment - self.observed_at) <= self.max_age_seconds


@dataclass(frozen=True)
class PageIntentCheck:
    """Whether page evidence allows the intended action to start this tick."""

    allowed: bool
    intent: str | None
    page: str | None
    reason: str


def check_page_evidence(
    intent: str | None,
    evidence: PageEvidence | None,
    *,
    now: float | None = None,
) -> PageIntentCheck:
    """Decide whether ``evidence`` may veto ``intent``.

    The default answer is always "allowed": only a fresh, accepted,
    demonstrably incompatible page class blocks an action.
    """
    if intent is None:
        return PageIntentCheck(True, None, None, REASON_UNKNOWN_INTENT)
    if evidence is None:
        return PageIntentCheck(True, intent, None, REASON_NO_EVIDENCE)

    compatible = COMPATIBLE_PAGES.get(intent)
    if compatible is None:
        # An action this module was never taught keeps its existing behaviour:
        # new intents must opt in to the veto explicitly.
        return PageIntentCheck(True, intent, evidence.page, REASON_UNKNOWN_INTENT)

    page = evidence.page
    if not evidence.is_fresh(now):
        return PageIntentCheck(True, intent, page, REASON_STALE_EVIDENCE)
    if page not in KNOWN_PAGES:
        # A class this module was never taught (or a conflict collapsed to
        # UNKNOWN upstream) carries no evidence, so it cannot veto.
        return PageIntentCheck(True, intent, page, REASON_UNKNOWN_PAGE)
    if page == PAGE_UNKNOWN:
        return PageIntentCheck(True, intent, page, REASON_UNKNOWN_PAGE)

    if page in compatible:
        return PageIntentCheck(True, intent, page, REASON_COMPATIBLE)
    return PageIntentCheck(False, intent, page, REASON_INCOMPATIBLE)
