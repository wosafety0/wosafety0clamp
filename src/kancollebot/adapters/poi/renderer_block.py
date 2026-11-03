"""Detect a blocking modal that has parked the game renderer.

When the game page hits an error it raises a renderer-side confirmation:

    poi                                   [x]
    エラーが発生したため、ページ更新します。
                              [ 確定 ] [ 取消 ]

Electron turns that ``window.confirm`` into a native modal owned by the main
process, and the game renderer's main thread stays parked until the button is
pressed. While it is up *every* renderer-domain CDP command goes unanswered --
``Page.getLayoutMetrics``, ``Page.enable``, ``Page.captureScreenshot`` and the
whitelisted ``Runtime.evaluate`` all time out -- yet the socket still connects
and the Poi shell keeps rendering.

The dialog is **not** a DevTools dialog: ``Page.handleJavaScriptDialog`` answers
with an error because the CDP dialog manager never sees an Electron modal. So
this module never pretends it can dismiss the dialog. It only answers one
question, and answers it fail-closed: is the game surface usable right now?

Detection is deliberately asymmetric. A *responsive* verdict needs one answered
command; a *blocked* verdict only needs silence, because sending input into a
surface that does not answer is the one thing that must never happen.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Protocol


DEFAULT_TIMEOUT_SECONDS = 2.0
# The cheapest command whose handler has to reach the game renderer. Screenshot
# capture would also work but costs far more, and Page.enable additionally
# mutates session state, so neither is used as the probe.
PROBE_METHOD = "Page.getLayoutMetrics"
SHELL_PROBE_METHOD = "Accessibility.getFullAXTree"


class Surface(str, Enum):
    """Whether one renderer answered a read-only command."""

    RESPONSIVE = "responsive"
    BLOCKED = "blocked"
    UNREACHABLE = "unreachable"


class BlockReason(str, Enum):
    """Why the surface is or is not usable, in the project's blocked_* style."""

    READY = "ready"
    BLOCKED_GAME_RENDERER_UNANSWERED = "blocked_game_renderer_unanswered"
    BLOCKED_GAME_TARGET_UNREACHABLE = "blocked_game_target_unreachable"
    BLOCKED_SHELL_UNREACHABLE = "blocked_shell_unreachable"
    BLOCKED_APP_UNRESPONSIVE = "blocked_app_unresponsive"


class RendererBlockedError(RuntimeError):
    """Raised when a caller tried to act on a surface that did not answer."""


MANUAL_ACTION = (
    "点【取消】。不要点【確定】：確定 会重新载入页面，可能直接变成猫。"
    "（--dismiss-cancel / --auto-dismiss-modal 已能自动按【取消】）"
)


class _CommandChannel(Protocol):
    def send(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send one CDP command and return its result."""


class _ProbeSession(Protocol):
    transport: _CommandChannel

    def close(self) -> None:
        """Release the loopback connection."""


SessionFactory = Callable[[], _ProbeSession]


@dataclass(frozen=True, slots=True)
class BlockProbe:
    """One read-only verdict on whether the game surface can be driven."""

    game: Surface
    shell: Surface
    reason: BlockReason
    latency_seconds: float | None
    detail: str

    @property
    def input_allowed(self) -> bool:
        """True only on a positively answered game surface."""
        return self.game is Surface.RESPONSIVE

    @property
    def dialog_suspected(self) -> bool:
        """True when the game renderer is parked but the shell still renders.

        A blocking native modal parks only the game renderer. A frozen or
        crashed app takes the shell renderer down as well, so the two cases
        stay distinguishable and are reported separately.
        """
        return self.game is Surface.BLOCKED and self.shell is Surface.RESPONSIVE

    def as_event(self) -> dict[str, object]:
        """Flatten into JSON-safe telemetry."""
        return {
            "game": self.game.value,
            "shell": self.shell.value,
            "reason": self.reason.value,
            "latency_seconds": None
            if self.latency_seconds is None
            else round(self.latency_seconds, 3),
            "dialog_suspected": self.dialog_suspected,
            "input_allowed": self.input_allowed,
            "detail": self.detail,
        }


def _probe_one(
    factory: SessionFactory,
    method: str,
    *,
    params: dict[str, Any] | None = None,
) -> tuple[Surface, float | None, str]:
    """Open a throwaway connection, send one command, and close it.

    A dedicated connection is used on purpose: a timed-out command marks its
    transport unusable, so reusing a driver's channel would poison the very
    session the caller wants to keep for the next step.
    """
    try:
        session = factory()
    except Exception as exc:  # noqa: BLE001 - discovery failure is a verdict
        return Surface.UNREACHABLE, None, f"{type(exc).__name__}: {exc}"
    started = time.perf_counter()
    try:
        session.transport.send(method, dict(params or {}))
    except Exception as exc:  # noqa: BLE001 - silence is the signal we report
        elapsed = time.perf_counter() - started
        return Surface.BLOCKED, elapsed, f"{type(exc).__name__}: {exc}"
    finally:
        try:
            session.close()
        except Exception:  # noqa: BLE001 - closing must never mask a verdict
            pass
    return Surface.RESPONSIVE, time.perf_counter() - started, "answered"


def probe_block(
    *,
    game_session: SessionFactory,
    shell_session: SessionFactory,
) -> BlockProbe:
    """Classify the game surface without sending any input.

    ``game_session`` and ``shell_session`` are factories so this module stays
    testable without a live Poi. They must each build a *new* connection.
    """
    game, latency, game_detail = _probe_one(game_session, PROBE_METHOD)
    if game is Surface.RESPONSIVE:
        return BlockProbe(
            game=game,
            shell=Surface.RESPONSIVE,
            reason=BlockReason.READY,
            latency_seconds=latency,
            detail="the game renderer answered a read-only command",
        )

    shell, _, shell_detail = _probe_one(shell_session, SHELL_PROBE_METHOD)
    if game is Surface.UNREACHABLE:
        reason = BlockReason.BLOCKED_GAME_TARGET_UNREACHABLE
        detail = f"the game target could not be opened: {game_detail}"
    elif shell is Surface.RESPONSIVE:
        reason = BlockReason.BLOCKED_GAME_RENDERER_UNANSWERED
        detail = (
            "the game renderer did not answer while the Poi shell still did; "
            f"a blocking modal is the usual cause ({game_detail})"
        )
    elif shell is Surface.UNREACHABLE:
        reason = BlockReason.BLOCKED_SHELL_UNREACHABLE
        detail = f"neither surface could be opened (shell: {shell_detail})"
    else:
        reason = BlockReason.BLOCKED_APP_UNRESPONSIVE
        detail = "neither the game renderer nor the Poi shell answered"
    return BlockProbe(
        game=game,
        shell=shell,
        reason=reason,
        latency_seconds=latency,
        detail=detail,
    )


def live_probe(*, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> BlockProbe:
    """Probe a running Poi over loopback CDP with default dependencies."""
    if not isinstance(timeout, (int, float)) or not timeout > 0:
        raise ValueError("timeout must be positive")

    from kancollebot.adapters.poi.cdp_websocket import (
        connect_game_webview,
        connect_poi_session,
    )

    return probe_block(
        game_session=lambda: connect_game_webview(timeout=timeout),
        shell_session=lambda: connect_poi_session(timeout=timeout),
    )


def require_responsive(probe: BlockProbe) -> BlockProbe:
    """Fail closed unless the game surface positively answered."""
    if probe.game is not Surface.RESPONSIVE:
        raise RendererBlockedError(
            f"{probe.reason.value}: {probe.detail}; refusing input on an "
            f"unanswered surface"
        )
    return probe
