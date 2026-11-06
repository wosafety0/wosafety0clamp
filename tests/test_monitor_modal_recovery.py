"""P0.2 生产验证 L1 发现的缺陷：模态恢复把"游戏健康"当成"故障已修复"。

2026-11-06 生产证据：监控入口在 freshness 失败路径上，``recover()`` 读的是
``probe_after.input_allowed``——游戏渲染器健康时它恒为 True，于是每个失败的
tick 都被当成"已修复、立即重试"：74 秒内约 260 个 tick（无 sleep、
``--max-ticks`` 失效），直到 Poi MCP 在负载下报错。修复后的契约是：

* 只有**真的按下了已知 Cancel 按钮**（``outcome == "dismissed"``）且其后
  surface 接受输入，``recover()`` 才允许返回 True；
* ``no_op`` / ``refused`` / ``still_blocked`` 一律 False，失败 tick 落回
  ``run_monitor`` 自己的节奏（sleep + 计数），由既有测试覆盖。

全部离线：不连 CDP、不点任何窗口、不启动 Poi。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from kancollebot.adapters.poi.modal_window import RecoveryOutcome  # noqa: E402
from run_continuous_expedition_monitor import (  # noqa: E402
    _dismiss_modal_if_needed,
    _modal_recovery_repaired,
)


class FakeRecovery:
    """Shape of ``modal_window``'s recovery result that the tool relies on."""

    def __init__(self, outcome: RecoveryOutcome, input_allowed: bool) -> None:
        self.outcome = outcome
        self.probe_after = SimpleNamespace(input_allowed=input_allowed)

    def as_event(self) -> dict:
        return {"outcome": self.outcome.value}


class ModalRecoveryRepairedTests(unittest.TestCase):
    def test_a_healthy_renderer_is_not_a_repair(self):
        # The exact production bug: game answers, nothing was pressed.
        self.assertFalse(_modal_recovery_repaired("no_op", SimpleNamespace(input_allowed=True)))

    def test_a_real_dismissal_with_input_allowed_is_a_repair(self):
        self.assertTrue(_modal_recovery_repaired("dismissed", SimpleNamespace(input_allowed=True)))

    def test_a_dismissal_that_leaves_input_blocked_is_not_a_repair(self):
        self.assertFalse(_modal_recovery_repaired("dismissed", SimpleNamespace(input_allowed=False)))

    def test_refusals_and_ambiguity_never_count(self):
        for outcome in ("refused", "still_blocked", "exhausted", "content_unverified", None, ""):
            with self.subTest(outcome=outcome):
                self.assertFalse(_modal_recovery_repaired(outcome, SimpleNamespace(input_allowed=True)))


class DismissModalContractTests(unittest.TestCase):
    """The glue reads ``outcome`` off the MODAL_CANCEL_ATTEMPTED event.

    This is the contract the repair depends on: if the event ever stops
    carrying the recovery outcome, ``recover()`` degrades back to False
    (safe) — but a rename to ``no_op``/``dismissed`` values would silently
    re-pin the bug. Both are pinned here.
    """

    def run_dismiss(self, recovery: FakeRecovery) -> tuple[list[dict], object]:
        events: list[dict] = []
        with mock.patch(
            "kancollebot.adapters.poi.modal_window.Win32Backend"
        ) as backend, mock.patch(
            "kancollebot.adapters.poi.modal_window.dismiss_blocking_modal",
            return_value=recovery,
        ):
            self.assertTrue(backend)  # the patch must be in place
            returned = _dismiss_modal_if_needed(
                SimpleNamespace(), emit=events.append
            )
        return events, returned

    def test_the_event_carries_the_outcome_and_the_return_is_the_after_probe(self):
        recovery = FakeRecovery(RecoveryOutcome.DISMISSED, True)
        events, returned = self.run_dismiss(recovery)

        self.assertEqual(events[0]["status"], "MODAL_CANCEL_ATTEMPTED")
        self.assertEqual(events[0]["outcome"], "dismissed")
        self.assertIs(returned, recovery.probe_after)
        # End to end along the repaired path:
        self.assertTrue(_modal_recovery_repaired(events[0]["outcome"], returned))

    def test_no_op_end_to_end_is_not_a_repair(self):
        # Game healthy, nothing pressed: the exact shape of the 2026-11-06 hot
        # loop, pinned so it cannot come back as "True" again.
        recovery = FakeRecovery(RecoveryOutcome.NO_OP, True)
        events, returned = self.run_dismiss(recovery)

        self.assertEqual(events[0]["outcome"], "no_op")
        self.assertIs(returned, recovery.probe_after)
        self.assertTrue(returned.input_allowed, "the game renderer answers")
        self.assertFalse(_modal_recovery_repaired(events[0]["outcome"], returned))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
