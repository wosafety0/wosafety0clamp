"""Coarse-grained page classification over existing fingerprint rankings.

Four business-level buckets -- ``HOME`` / ``MENU`` / ``MODAL`` / ``UNKNOWN``
-- summarising the detailed OOI fingerprint states. This module is a pure
mapping layer between the existing visual recognition results and future
business decisions (FleetPhase wiring and navigation fallbacks are later
phases and are deliberately absent here):

* it consumes ``FingerprintRanking`` verdicts produced by the existing
  classifier family (0.90 / 0.02 thresholds); it is NOT a second vision
  mechanism, trains nothing, and adds no templates;
* the detailed page identity survives in the verdict -- the coarse class is
  an upper-layer summary and never replaces the underlying business page
  identity that the settlement / resupply / dispatch controllers rely on;
* ``UNKNOWN`` is fail-closed: an unaccepted ranking, a state outside the
  mapping, and conflicting evidence across capture layers all stay UNKNOWN.
  Classification coverage is never bought by forcing uncertain pages into a
  bucket.

An accepted ranking is page evidence only -- it never authorises a click.
"""

from __future__ import annotations

from dataclasses import dataclass

from kancollebot.adapters.poi.visual_fingerprint import FingerprintRanking

COARSE_HOME = "HOME"
COARSE_MENU = "MENU"
COARSE_MODAL = "MODAL"
COARSE_UNKNOWN = "UNKNOWN"

# Home port itself. The return notice is deliberately NOT here: it is a
# business overlay the settlement controller must handle, not a plain home
# port (see MODAL below).
_HOME_STATES = frozenset({"HOME_PORT"})

# Ordinary navigable function / menu pages: sortie flows, the expedition
# list, mission detail and fleet selection, and the supply pages. Reward
# claim and settlement screens are excluded on purpose -- business handlers
# own those pages and they are covered by MODAL, not MENU.
_MENU_STATES = frozenset(
    {
        "SORTIE_MENU",
        "EXPEDITION_LIST",
        "MISSION_SELECTED",
        "MISSION_SELECTED_M1",
        "FLEET_2_SELECTED",
        "FLEET_3_SELECTED",
        "SUPPLY_MENU",
        "SUPPLY_FLEET_1_SELECTED",
        "SUPPLY_FLEET_2_SELECTED",
        "SUPPLY_FLEET_3_SELECTED",
        "SUPPLY_FLEET_4_SELECTED",
    }
)

# Business overlays that existing controllers own: the home-port return
# notice bubble (settlement controller), the settlement result page and the
# bonus / reward pages. MODAL is a coarse category only -- it never
# authorises generic navigation clicks, and the existing settlement /
# reward page protections are untouched.
_MODAL_STATES = frozenset(
    {
        "HOME_PORT_RETURN_NOTICE",
        "RETURN_RESULT",
        "BONUS_ITEMS",
        "BONUS_ITEMS_M1",
        "BONUS_ITEMS_M2",
    }
)

_STATE_TO_COARSE = {
    **{name: COARSE_HOME for name in _HOME_STATES},
    **{name: COARSE_MENU for name in _MENU_STATES},
    **{name: COARSE_MODAL for name in _MODAL_STATES},
}

# Reasons recorded on the verdict for run-log audit.
REASON_ACCEPTED = "accepted"
REASON_NO_ACCEPTED_EVIDENCE = "no_accepted_evidence"
REASON_UNMAPPED_STATE = "unmapped_state"
REASON_LAYER_CONFLICT = "layer_conflict"


def coarse_page_for_state(state: str | None) -> str:
    """Map one detailed fingerprint state to its coarse page class.

    ``None``, ``"UNKNOWN"`` and any state name absent from the mapping all
    return ``UNKNOWN`` -- a state that was never consciously classified must
    not be squeezed into a bucket for coverage.
    """
    if state is None:
        return COARSE_UNKNOWN
    return _STATE_TO_COARSE.get(state, COARSE_UNKNOWN)


@dataclass(frozen=True)
class CoarsePageVerdict:
    """The coarse class of one observation, with its full evidence trail.

    ``detail_state`` keeps the underlying fingerprint verdict (the business
    page identity); ``page`` is only the summary above it. ``reason`` explains
    how the class was reached for run-log audit.
    """

    page: str
    detail_state: str | None
    best_name: str | None
    best_score: float
    second_name: str | None
    second_score: float
    reason: str
    conflict: bool = False


def classify_coarse_page(*rankings: FingerprintRanking) -> CoarsePageVerdict:
    """Classify one observation from one or more capture-layer rankings.

    Pass a single ranking for the common path, or several rankings when two
    capture layers (default surface / composited overlay) were both ranked.
    Accepted rankings vote with their coarse class:

    * no accepted ranking at all -> ``UNKNOWN`` (no accepted evidence);
    * all accepted rankings agree -> that class;
    * accepted rankings disagree across layers -> ``UNKNOWN`` with
      ``conflict=True`` -- the layers disagree and nothing may be claimed.
    """
    accepted = [r for r in rankings if r.accepted]
    if not accepted:
        deciding = rankings[0] if rankings else None
        return CoarsePageVerdict(
            page=COARSE_UNKNOWN,
            detail_state=None if deciding is None else deciding.state,
            best_name=None if deciding is None else deciding.best_name,
            best_score=0.0 if deciding is None else deciding.best_score,
            second_name=None if deciding is None else deciding.second_name,
            second_score=0.0 if deciding is None else deciding.second_score,
            reason=REASON_NO_ACCEPTED_EVIDENCE,
        )

    classes = {coarse_page_for_state(r.state) for r in accepted}
    if len(classes) > 1:
        deciding = accepted[0]
        return CoarsePageVerdict(
            page=COARSE_UNKNOWN,
            detail_state=deciding.state,
            best_name=deciding.best_name,
            best_score=deciding.best_score,
            second_name=deciding.second_name,
            second_score=deciding.second_score,
            reason=REASON_LAYER_CONFLICT,
            conflict=True,
        )

    page = classes.pop()
    deciding = accepted[0]
    if page is COARSE_UNKNOWN:
        # An accepted ranking whose state is not in the mapping (a newly
        # recorded state, say) stays UNKNOWN until it is consciously mapped.
        return CoarsePageVerdict(
            page=COARSE_UNKNOWN,
            detail_state=deciding.state,
            best_name=deciding.best_name,
            best_score=deciding.best_score,
            second_name=deciding.second_name,
            second_score=deciding.second_score,
            reason=REASON_UNMAPPED_STATE,
        )
    return CoarsePageVerdict(
        page=page,
        detail_state=deciding.state,
        best_name=deciding.best_name,
        best_score=deciding.best_score,
        second_name=deciding.second_name,
        second_score=deciding.second_score,
        reason=REASON_ACCEPTED,
    )
