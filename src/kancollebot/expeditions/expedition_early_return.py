"""Early return for an expedition whose *displayed* remaining time is under 1 min.

Game mechanic (sourced, not inferred)
-------------------------------------

* kcwiki, 远征: 「远征可以提早完成。当远征还有不到一分钟就完成时，此时你回到母港，
  就会发现远征完成的提示。」 and 「远征结束时如果你已经处于母港主界面则不会有提示，
  必须去其他任意界面再回到母港才会出现提示。」
  https://zh.kcwiki.cn/wiki/远征
* 日文 KanColle Wiki*, 遠征 / TIPS: 「遠征完了まで残り1分を切っているとき､母港画面へ
  移動すると残り時間を待たずに遠征が完了する」 and, decisively for the decision
  input, 「帰還できるのは遠征先選択画面の表示が1分を切ったとき。PCの時計や外部ツールで
  時間を計っているとズレが生じる可能性があるので注意。」
  https://wikiwiki.jp/kancolle/遠征

So the *displayed* countdown is the authority. A local timer may decide when it is
worth looking, but it may never decide that the mechanic applies. Both sources
agree the trigger is "return to the home port", and the Chinese source adds the
one non-obvious constraint: if the client is already at the home port, it must
first go somewhere else and then come back, otherwise no notice appears.

Evidence status: the mechanic is sourced from two wikis. It is **not** verified
in this game client yet, so the coordinator reports exactly what it observed and
never reports success from navigation alone - only a fresh Poi read showing the
pending-settlement state counts.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

__all__ = [
    "CountdownReading",
    "DEFAULT_EARLY_RETURN_THRESHOLD_SECONDS",
    "EarlyReturnObservation",
    "EarlyReturnOutcome",
    "ExpeditionEarlyReturn",
]

#: The wiki states "less than one minute". Kept as an explicit constant because
#: it is a game rule, not a tuning knob: lowering it would trigger late, raising
#: it would trigger before the client allows the early completion.
DEFAULT_EARLY_RETURN_THRESHOLD_SECONDS = 60

#: ``api_mission[0]`` values used by the mechanic.
_MISSION_ON_EXPEDITION = 1
_MISSION_PENDING_SETTLEMENT = 2

HOME_PORT = "HOME_PORT"


@dataclass(frozen=True, slots=True)
class CountdownReading:
    """What the expedition screen actually displays, plus its parsed value."""

    text: str
    remaining_seconds: int

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise ValueError("text must be a string")


@dataclass(frozen=True, slots=True)
class EarlyReturnObservation:
    """One Poi reading, with the freshness the decision is allowed to rely on."""

    mission_status: int | None
    fresh: bool
    detail: str = ""

    def __post_init__(self) -> None:
        if self.mission_status is not None and (
            isinstance(self.mission_status, bool)
            or not isinstance(self.mission_status, int)
        ):
            raise ValueError("mission_status must be an int or None")


@dataclass(frozen=True, slots=True)
class EarlyReturnOutcome:
    """Everything needed to reconstruct the attempt from the log alone."""

    triggered: bool
    succeeded: bool
    reason: str
    remaining_seconds: int | None = None
    displayed_remaining: str | None = None
    page_before: str | None = None
    page_after: str | None = None
    poi_before_status: int | None = None
    poi_after_status: int | None = None
    poi_before_fresh: bool | None = None
    poi_after_fresh: bool | None = None
    screen_switched: bool = False

    def as_log_fields(self, *, fleet_id: int) -> dict[str, object]:
        return {
            "fleet_id": fleet_id,
            "triggered": self.triggered,
            "succeeded": self.succeeded,
            "reason": self.reason,
            "remaining_seconds": self.remaining_seconds,
            "displayed_remaining": self.displayed_remaining,
            "page_before": self.page_before,
            "page_after": self.page_after,
            "poi_before_status": self.poi_before_status,
            "poi_after_status": self.poi_after_status,
            "poi_before_fresh": self.poi_before_fresh,
            "poi_after_fresh": self.poi_after_fresh,
            "screen_switched": self.screen_switched,
            "game_api_requests": False,
        }


class ExpeditionEarlyReturn:
    """Try the documented early completion for one fleet, at most once per call.

    All game access is injected, so the decision can be tested without a client:

    * ``read_poi()`` -> :class:`EarlyReturnObservation` (server truth + freshness)
    * ``open_expedition_list()`` -> the page name reached (normal UI navigation)
    * ``read_countdown()`` -> :class:`CountdownReading` or ``None`` when the
      displayed time cannot be read (an uncalibrated reader must say so)
    * ``current_page()`` -> the page the client shows right now
    * ``go_home_port()`` / ``leave_home_port()`` -> one normal UI switch each

    It never clicks 遠征中止/召還, never constructs a game request, and never
    reports success without a fresh Poi read showing the pending-settlement
    state.
    """

    def __init__(
        self,
        *,
        fleet_id: int,
        read_poi: Callable[[], EarlyReturnObservation],
        open_expedition_list: Callable[[], str | None],
        read_countdown: Callable[[], CountdownReading | None],
        current_page: Callable[[], str | None],
        go_home_port: Callable[[], None],
        leave_home_port: Callable[[], None],
        event_sink: Callable[[str, Mapping[str, object]], None] | None = None,
        threshold_seconds: int = DEFAULT_EARLY_RETURN_THRESHOLD_SECONDS,
    ) -> None:
        if isinstance(fleet_id, bool) or not isinstance(fleet_id, int) or fleet_id <= 0:
            raise ValueError("fleet_id must be a positive int")
        for name, value in (
            ("read_poi", read_poi),
            ("open_expedition_list", open_expedition_list),
            ("read_countdown", read_countdown),
            ("current_page", current_page),
            ("go_home_port", go_home_port),
            ("leave_home_port", leave_home_port),
        ):
            if not callable(value):
                raise ValueError(f"{name} must be callable")
        if event_sink is not None and not callable(event_sink):
            raise ValueError("event_sink must be callable when given")
        if (
            isinstance(threshold_seconds, bool)
            or not isinstance(threshold_seconds, int)
            or threshold_seconds <= 0
        ):
            raise ValueError("threshold_seconds must be a positive int")
        self._fleet_id = fleet_id
        self._read_poi = read_poi
        self._open_expedition_list = open_expedition_list
        self._read_countdown = read_countdown
        self._current_page = current_page
        self._go_home_port = go_home_port
        self._leave_home_port = leave_home_port
        self._event_sink = event_sink
        self._threshold = threshold_seconds

    def _emit(self, event: str, fields: Mapping[str, object]) -> None:
        if self._event_sink is not None:
            self._event_sink(event, dict(fields))

    def attempt(self) -> EarlyReturnOutcome:
        """One bounded attempt: at most one meaningful screen switch."""
        before = self._read_poi()
        page_before = self._current_page()
        self._emit("expedition_early_return_checked", {
            "fleet_id": self._fleet_id,
            "phase": "precondition",
            "poi_mission_status": before.mission_status,
            "poi_fresh": before.fresh,
            "page": page_before,
            "threshold_seconds": self._threshold,
        })

        if not before.fresh:
            return self._finish(EarlyReturnOutcome(
                triggered=False,
                succeeded=False,
                reason="poi_observation_not_fresh",
                page_before=page_before,
                poi_before_status=before.mission_status,
                poi_before_fresh=False,
            ))
        if before.mission_status != _MISSION_ON_EXPEDITION:
            return self._finish(EarlyReturnOutcome(
                triggered=False,
                succeeded=False,
                reason="fleet_is_not_on_an_expedition",
                page_before=page_before,
                poi_before_status=before.mission_status,
                poi_before_fresh=True,
            ))

        # Step 2 of the mechanic: the countdown only exists on the expedition
        # screen, so the client has to be there to read it. This is also the
        # single "away from home port" switch when the client starts at home.
        reached = self._open_expedition_list()
        page_at_countdown = self._current_page()
        self._emit("expedition_early_return_screen", {
            "fleet_id": self._fleet_id,
            "phase": "expedition_list",
            "requested": "EXPEDITION_LIST",
            "reached": reached,
            "page": page_at_countdown,
        })

        reading = self._read_countdown()
        if reading is None:
            self._return_home_if_needed(page_at_countdown)
            return self._finish(EarlyReturnOutcome(
                triggered=False,
                succeeded=False,
                reason="displayed_remaining_time_unreadable",
                page_before=page_before,
                page_after=self._current_page(),
                poi_before_status=before.mission_status,
                poi_before_fresh=True,
                screen_switched=True,
            ))

        if reading.remaining_seconds < 0:
            self._return_home_if_needed(page_at_countdown)
            return self._finish(EarlyReturnOutcome(
                triggered=False,
                succeeded=False,
                reason="displayed_remaining_time_invalid",
                remaining_seconds=reading.remaining_seconds,
                displayed_remaining=reading.text,
                page_before=page_before,
                page_after=self._current_page(),
                poi_before_status=before.mission_status,
                poi_before_fresh=True,
                screen_switched=True,
            ))

        if reading.remaining_seconds >= self._threshold:
            # Not yet: go back to the home port so the loop resumes its normal
            # wait, and do not switch again.
            self._return_home_if_needed(page_at_countdown)
            return self._finish(EarlyReturnOutcome(
                triggered=False,
                succeeded=False,
                reason="displayed_remaining_time_at_or_above_threshold",
                remaining_seconds=reading.remaining_seconds,
                displayed_remaining=reading.text,
                page_before=page_before,
                page_after=self._current_page(),
                poi_before_status=before.mission_status,
                poi_before_fresh=True,
                screen_switched=True,
            ))

        # Below the threshold: send the client to the home port. If it is
        # already there (navigation refused, or the client moved on its own),
        # leave first - the wiki is explicit that no notice appears otherwise.
        current = self._current_page()
        if current == HOME_PORT:
            self._leave_home_port()
        self._go_home_port()
        page_after = self._current_page()
        self._emit("expedition_early_return_switch", {
            "fleet_id": self._fleet_id,
            "phase": "home_port",
            "from_page": current,
            "to_page": page_after,
            "remaining_seconds": reading.remaining_seconds,
            "displayed_remaining": reading.text,
        })

        after = self._read_poi()
        succeeded = after.fresh and after.mission_status == _MISSION_PENDING_SETTLEMENT
        if succeeded:
            reason = "early_return_confirmed_by_poi_pending_settlement"
        elif not after.fresh:
            reason = "post_switch_poi_observation_not_fresh"
        elif after.mission_status == _MISSION_ON_EXPEDITION:
            reason = "client_still_reports_the_fleet_on_expedition"
        else:
            reason = f"unexpected_mission_status:{after.mission_status}"
        return self._finish(EarlyReturnOutcome(
            triggered=True,
            succeeded=succeeded,
            reason=reason,
            remaining_seconds=reading.remaining_seconds,
            displayed_remaining=reading.text,
            page_before=page_before,
            page_after=page_after,
            poi_before_status=before.mission_status,
            poi_after_status=after.mission_status,
            poi_before_fresh=True,
            poi_after_fresh=after.fresh,
            screen_switched=True,
        ))

    # -- internals ----------------------------------------------------------

    def _return_home_if_needed(self, page: str | None) -> None:
        """One switch back to the home port, and only when we are not there."""
        if page == HOME_PORT:
            return
        self._go_home_port()

    def _finish(self, outcome: EarlyReturnOutcome) -> EarlyReturnOutcome:
        self._emit("expedition_early_return_finished", outcome.as_log_fields(
            fleet_id=self._fleet_id
        ))
        return outcome


def read_remaining_from_text(text: str) -> CountdownReading | None:
    """Parse ``M:SS`` / ``MM:SS`` / ``SS`` out of a displayed countdown.

    Kept next to the coordinator so the parsing rule has one definition; the
    caller passes the *displayed* string, never a locally computed duration.
    """
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    cleaned = text.strip().replace("：", ":")
    if not cleaned:
        return None

    def _ascii_digits(value: str) -> bool:
        # ``str.isdigit`` accepts full-width and other Unicode digits; a
        # countdown that gates a game action must be ASCII or not be read.
        return bool(value) and value.isascii() and value.isdigit()

    if ":" in cleaned:
        head, _, tail = cleaned.rpartition(":")
        if not _ascii_digits(tail) or not head.lstrip("-").isdigit():
            return None
        if not head.isascii():
            return None
        minutes = int(head)
        seconds = int(tail)
        if minutes < 0 or seconds < 0 or seconds >= 60:
            return None
        return CountdownReading(text=cleaned, remaining_seconds=minutes * 60 + seconds)
    if _ascii_digits(cleaned):
        return CountdownReading(text=cleaned, remaining_seconds=int(cleaned))
    return None
