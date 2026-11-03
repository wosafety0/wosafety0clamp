"""Offline tests for the game-renderer block probe.

No test touches a live Poi: every session is a fake that either answers, stays
silent, or refuses to open. The blocking case is modelled on the real one, where
a native confirm parks the game renderer while the Poi shell keeps rendering.
"""
from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout

from kancollebot.adapters.poi.renderer_block import (
    MANUAL_ACTION,
    PROBE_METHOD,
    SHELL_PROBE_METHOD,
    BlockReason,
    RendererBlockedError,
    Surface,
    live_probe,
    probe_block,
    require_responsive,
)


class _Channel:
    """A transport that either answers one command or stays silent."""

    def __init__(self, *, silent: bool = False) -> None:
        self.silent = silent
        self.sent: list[tuple[str, dict]] = []

    def send(self, method, params):
        self.sent.append((method, params))
        if self.silent:
            raise RuntimeError("CDP command outcome is unknown")
        return {"cssLayoutViewport": {"clientWidth": 1200, "clientHeight": 720}}


class _Session:
    def __init__(self, channel: _Channel, *, close_raises: Exception | None = None) -> None:
        self.transport = channel
        self.closed = False
        self.close_raises = close_raises

    def close(self):
        self.closed = True
        if self.close_raises is not None:
            raise self.close_raises


def answering() -> _Channel:
    return _Channel()


def silent() -> _Channel:
    return _Channel(silent=True)


def session_factory(channel: _Channel, *, close_raises=None):
    """Build a factory that always hands back the same session."""
    session = _Session(channel, close_raises=close_raises)
    return lambda: session


def tracked(channel: _Channel, *, close_raises=None):
    """Build a factory plus the single session it will hand back."""
    session = _Session(channel, close_raises=close_raises)
    return (lambda: session), session


def blocked_probe():
    return probe_block(game_session=session_factory(silent()), shell_session=session_factory(answering()))


def healthy_probe():
    return probe_block(game_session=session_factory(answering()), shell_session=session_factory(answering()))


class ResponsiveSurfaceTests(unittest.TestCase):
    def test_an_answered_probe_allows_input(self) -> None:
        shell_calls: list[str] = []

        def shell():
            shell_calls.append("probed")
            return _Session(answering())

        probe = probe_block(game_session=session_factory(answering()), shell_session=shell)
        self.assertIs(probe.game, Surface.RESPONSIVE)
        self.assertIs(probe.reason, BlockReason.READY)
        self.assertTrue(probe.input_allowed)
        self.assertFalse(probe.dialog_suspected)
        self.assertEqual(shell_calls, [], "the shell is only probed when the game is silent")

    def test_the_probe_uses_the_cheapest_renderer_command(self) -> None:
        channel = answering()
        probe_block(game_session=session_factory(channel), shell_session=session_factory(answering()))
        self.assertEqual([method for method, _ in channel.sent], [PROBE_METHOD])
        self.assertEqual(channel.sent[0][1], {})

    def test_the_session_is_always_closed(self) -> None:
        game, game_session = tracked(answering())
        probe_block(game_session=game, shell_session=session_factory(answering()))
        self.assertTrue(game_session.closed)

    def test_a_close_failure_does_not_mask_a_verdict(self) -> None:
        game, _ = tracked(answering(), close_raises=OSError("socket already gone"))
        probe = probe_block(game_session=game, shell_session=session_factory(answering()))
        self.assertIs(probe.game, Surface.RESPONSIVE)
        self.assertTrue(probe.input_allowed)


class BlockedSurfaceTests(unittest.TestCase):
    def test_silent_game_with_live_shell_reads_as_a_blocking_modal(self) -> None:
        probe = blocked_probe()
        self.assertIs(probe.game, Surface.BLOCKED)
        self.assertIs(probe.shell, Surface.RESPONSIVE)
        self.assertIs(probe.reason, BlockReason.BLOCKED_GAME_RENDERER_UNANSWERED)
        self.assertTrue(probe.dialog_suspected)
        self.assertFalse(probe.input_allowed)
        self.assertGreaterEqual(probe.latency_seconds, 0.0)

    def test_the_shell_is_probed_only_after_the_game_stays_silent(self) -> None:
        shell_channel = answering()
        probe_block(game_session=session_factory(silent()), shell_session=session_factory(shell_channel))
        self.assertEqual([m for m, _ in shell_channel.sent], [SHELL_PROBE_METHOD])

    def test_a_silent_shell_too_reads_as_an_app_freeze(self) -> None:
        probe = probe_block(game_session=session_factory(silent()), shell_session=session_factory(silent()))
        self.assertIs(probe.reason, BlockReason.BLOCKED_APP_UNRESPONSIVE)
        self.assertFalse(probe.dialog_suspected, "a whole-app freeze is not a modal")

    def test_an_unopenable_game_target_is_its_own_reason(self) -> None:
        def refuse():
            raise ConnectionRefusedError("no such target")

        probe = probe_block(game_session=refuse, shell_session=session_factory(answering()))
        self.assertIs(probe.game, Surface.UNREACHABLE)
        self.assertIs(probe.reason, BlockReason.BLOCKED_GAME_TARGET_UNREACHABLE)
        self.assertFalse(probe.input_allowed)
        self.assertIsNone(probe.latency_seconds)

    def test_an_unopenable_shell_is_not_mistaken_for_a_modal(self) -> None:
        def refuse():
            raise ConnectionRefusedError("no poi page")

        probe = probe_block(game_session=session_factory(silent()), shell_session=refuse)
        self.assertIs(probe.reason, BlockReason.BLOCKED_SHELL_UNREACHABLE)
        self.assertFalse(probe.dialog_suspected)


class FailClosedTests(unittest.TestCase):
    def test_require_responsive_refuses_a_blocked_surface(self) -> None:
        with self.assertRaises(RendererBlockedError) as caught:
            require_responsive(blocked_probe())
        self.assertIn("blocked_game_renderer_unanswered", str(caught.exception))

    def test_require_responsive_passes_a_live_surface_through(self) -> None:
        probe = healthy_probe()
        self.assertIs(require_responsive(probe), probe)

    def test_live_probe_rejects_a_non_positive_timeout(self) -> None:
        for bad in (0, -1.0):
            with self.subTest(timeout=bad), self.assertRaises(ValueError):
                live_probe(timeout=bad)


class EventTests(unittest.TestCase):
    def test_the_event_is_json_safe(self) -> None:
        event = blocked_probe().as_event()
        json.dumps(event)  # must not raise
        self.assertEqual(event["reason"], "blocked_game_renderer_unanswered")
        self.assertTrue(event["dialog_suspected"])
        self.assertFalse(event["input_allowed"])
        self.assertIsInstance(event["latency_seconds"], float)

    def test_an_unreachable_surface_reports_no_latency(self) -> None:
        def refuse():
            raise OSError("nope")

        self.assertIsNone(probe_block(game_session=refuse, shell_session=refuse).as_event()["latency_seconds"])


class CommandLineTests(unittest.TestCase):
    def _run(self, argv, probe):
        from tools import check_poi_renderer as tool

        original = tool.live_probe
        tool.live_probe = lambda **kwargs: probe
        buffer = io.StringIO()
        try:
            with redirect_stdout(buffer):
                code = tool.main(argv)
        finally:
            tool.live_probe = original
        return code, buffer.getvalue()

    def test_a_blocked_surface_exits_two_and_names_the_manual_action(self) -> None:
        code, out = self._run(["--timeout", "1"], blocked_probe())
        self.assertEqual(code, 2)
        self.assertIn("blocked_game_renderer_unanswered", out)
        self.assertIn(MANUAL_ACTION, out)
        self.assertIn("エラーが発生したため、ページ更新します。", out)

    def test_a_responsive_surface_exits_zero(self) -> None:
        code, out = self._run([], healthy_probe())
        self.assertEqual(code, 0)
        self.assertIn("safe to drive", out)

    def test_json_output_carries_the_verdict(self) -> None:
        code, out = self._run(["--json"], blocked_probe())
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["game"], "blocked")

    def test_a_probe_that_cannot_run_exits_one(self) -> None:
        from tools import check_poi_renderer as tool

        original = tool.live_probe

        def explode(**kwargs):
            raise RuntimeError("boom")

        tool.live_probe = explode
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = tool.main([])
        finally:
            tool.live_probe = original
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
