"""Offline tests for window-level Cancel, against a fake user32 backend.

The fake reproduces the live shape measured on a real block: a disabled Poi main
window, one ``#32770`` dialog owned by it, and buttons labelled 确定 / 取消.
"""
from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace

from kancollebot.adapters.poi.modal_window import (
    BM_CLICK,
    CONTENT_MARKERS,
    DEFAULT_DISMISS_LIMIT,
    DialogState,
    DismissBudget,
    RecoveryOutcome,
    WindowInfo,
    WindowInputNotAllowed,
    content_confirms_refresh,
    dismiss_blocking_modal,
    find_cancel_button,
    find_modal_dialog,
    normalise_label,
)


MAIN = 0x100
DIALOG = 0x200
ACCEPT = 0x301
CANCEL = 0x302

# What UI Automation returns for the real prompt, measured live.
LIVE_TEXTS = (
    "エラーが発生したため、ページ更新します。",
    "确定",
    "取消",
    "关闭",
)


def window(handle, *, owner=0, class_name="Chrome_WidgetWin_1", enabled=True, visible=True,
           exe="C:/Games/poi/poi.exe", title="poi", width=1200, height=780):
    return WindowInfo(
        handle=handle, pid=42, exe=exe, title=title, class_name=class_name, owner=owner,
        visible=visible, enabled=enabled, width=width, height=height,
    )


def modal_world(*, main_enabled=False, dialog=True, extra_dialogs=0, labels=("确定", "取消")):
    """The window list of a blocked Poi, plus its dialog buttons."""
    windows = [window(MAIN, enabled=main_enabled)]
    if dialog:
        windows.append(window(DIALOG, owner=MAIN, class_name="#32770", width=366, height=129))
    for index in range(extra_dialogs):
        windows.append(
            window(0x210 + index, owner=MAIN, class_name="#32770", width=300, height=120)
        )
    buttons = [(ACCEPT, labels[0])] if len(labels) > 0 else []
    if len(labels) > 1:
        buttons.append((CANCEL, labels[1]))
    for index, label in enumerate(labels[2:], start=3):
        buttons.append((0x300 + index, label))
    return windows, dict(buttons)


class FakeBackend:
    def __init__(self, windows, buttons, *, click_ok=True, dialog_persists=False, texts=()):
        self.windows = windows
        self.buttons = buttons
        self.click_ok = click_ok
        self.dialog_persists = dialog_persists
        self.texts = texts
        self.clicked: list[int] = []
        self.enumerations = 0

    def top_level_windows(self):
        self.enumerations += 1
        return tuple(self.windows)

    def child_buttons(self, handle):
        return tuple(self.buttons.items())

    def child_texts(self, handle):
        return tuple(self.texts)

    def window_exists(self, handle):
        return handle == DIALOG and self.dialog_persists

    def post_click(self, handle):
        self.clicked.append(handle)
        return self.click_ok


class Probe:
    def __init__(self, *, input_allowed, dialog_suspected):
        self.input_allowed = input_allowed
        self.dialog_suspected = dialog_suspected

    def as_event(self):
        return {"input_allowed": self.input_allowed, "dialog_suspected": self.dialog_suspected}


def blocked():
    return Probe(input_allowed=False, dialog_suspected=True)


def responsive():
    return Probe(input_allowed=True, dialog_suspected=False)


class LabelTests(unittest.TestCase):
    def test_accelerators_and_spacing_are_normalised(self) -> None:
        self.assertEqual(normalise_label(" &Cancel "), "cancel")
        self.assertEqual(normalise_label("取消"), "取消")


class DialogLookupTests(unittest.TestCase):
    def test_the_blocked_shape_is_found(self) -> None:
        lookup = find_modal_dialog(FakeBackend(*modal_world()))
        self.assertIs(lookup.state, DialogState.FOUND)
        self.assertEqual(lookup.dialog.handle, DIALOG)
        self.assertEqual(lookup.dialog.owner_handle, MAIN)

    def test_an_enabled_main_window_means_nothing_is_modal(self) -> None:
        lookup = find_modal_dialog(FakeBackend(*modal_world(main_enabled=True)))
        self.assertIs(lookup.state, DialogState.NONE)
        self.assertIn("#32770", lookup.detail)

    def test_the_owner_is_resolved_from_the_dialog_not_by_area(self) -> None:
        # Poi really has several visible top-level windows, one of them larger
        # than the game window. Picking "the biggest" would inspect the wrong
        # window and miss the modal, so the owner of the dialog must decide.
        windows = [
            window(MAIN, enabled=False, width=1216, height=787),
            window(0x105, enabled=True, width=1920, height=1023),
            window(DIALOG, owner=MAIN, class_name="#32770", width=366, height=129),
        ]
        lookup = find_modal_dialog(FakeBackend(windows, {}))
        self.assertIs(lookup.state, DialogState.FOUND)
        self.assertEqual(lookup.dialog.owner_handle, MAIN)

    def test_two_dialogs_are_ambiguous_rather_than_guessed(self) -> None:
        lookup = find_modal_dialog(FakeBackend(*modal_world(extra_dialogs=1)))
        self.assertIs(lookup.state, DialogState.AMBIGUOUS)
        self.assertIsNone(lookup.dialog)

    def test_a_dialog_without_a_disabled_owner_is_ignored(self) -> None:
        windows = [window(MAIN, enabled=True), window(DIALOG, owner=MAIN, class_name="#32770")]
        lookup = find_modal_dialog(FakeBackend(windows, {}))
        self.assertIs(lookup.state, DialogState.NONE)

    def test_a_non_poi_dialog_is_not_a_candidate(self) -> None:
        windows = [
            window(MAIN, enabled=False),
            window(DIALOG, owner=MAIN, class_name="#32770", exe="C:/Other/app.exe"),
        ]
        self.assertIs(find_modal_dialog(FakeBackend(windows, {})).state, DialogState.NONE)

    def test_a_custom_class_is_not_assumed_to_be_a_dialog(self) -> None:
        windows = [window(MAIN, enabled=False), window(DIALOG, owner=MAIN, class_name="Chrome_WidgetWin_1")]
        self.assertIs(find_modal_dialog(FakeBackend(windows, {})).state, DialogState.NONE)


class CancelButtonTests(unittest.TestCase):
    def test_the_cancel_button_is_selected_by_its_label(self) -> None:
        backend = FakeBackend(*modal_world())
        dialog = find_modal_dialog(backend).dialog
        button, labels = find_cancel_button(backend, dialog)
        self.assertEqual(button.handle, CANCEL)
        self.assertEqual(button.label, "取消")
        self.assertEqual(labels, ("确定", "取消"))

    def test_the_accept_button_is_never_selected(self) -> None:
        backend = FakeBackend(*modal_world(labels=("确定",)))
        dialog = find_modal_dialog(backend).dialog
        button, labels = find_cancel_button(backend, dialog)
        self.assertIsNone(button)
        self.assertEqual(labels, ("确定",))

    def test_a_japanese_dialog_is_also_cancellable(self) -> None:
        backend = FakeBackend(*modal_world(labels=("OK", "キャンセル")))
        dialog = find_modal_dialog(backend).dialog
        self.assertEqual(find_cancel_button(backend, dialog)[0].label, "キャンセル")

    def test_two_cancel_buttons_are_refused(self) -> None:
        backend = FakeBackend(*modal_world(labels=("取消", "キャンセル")))
        dialog = find_modal_dialog(backend).dialog
        self.assertIsNone(find_cancel_button(backend, dialog)[0])


class DismissTests(unittest.TestCase):
    def _run(self, backend, probe, **kwargs):
        calls = {"n": 0}
        after = kwargs.get("after", responsive())
        texts = kwargs.get("texts", LIVE_TEXTS)

        def probe_fn():
            calls["n"] += 1
            return probe if calls["n"] == 1 else after

        def read_texts(_handle):
            return texts

        return dismiss_blocking_modal(
            allow_window_input=True,
            backend=backend,
            probe=probe_fn,
            polls=kwargs.get("polls", 2),
            poll_seconds=0,
            sleep=lambda _s: None,
            budget=kwargs.get("budget"),
            read_texts=read_texts,
            require_content=kwargs.get("require_content", True),
        )

    def test_consent_is_required(self) -> None:
        with self.assertRaises(WindowInputNotAllowed):
            dismiss_blocking_modal(
                allow_window_input=False,
                backend=FakeBackend(*modal_world()),
                probe=blocked,
            )

    def test_a_responsive_surface_is_left_alone(self) -> None:
        backend = FakeBackend(*modal_world())
        result = self._run(backend, responsive())
        self.assertIs(result.outcome, RecoveryOutcome.NO_OP)
        self.assertEqual(backend.clicked, [])

    def test_a_block_that_is_not_a_modal_is_left_alone(self) -> None:
        backend = FakeBackend(*modal_world())
        result = self._run(backend, Probe(input_allowed=False, dialog_suspected=False))
        self.assertIs(result.outcome, RecoveryOutcome.REFUSED)
        self.assertEqual(backend.clicked, [], "a slow page must never trigger a window click")

    def test_cancel_is_pressed_and_never_accept(self) -> None:
        backend = FakeBackend(*modal_world())
        result = self._run(backend, blocked())
        self.assertIs(result.outcome, RecoveryOutcome.DISMISSED)
        self.assertEqual(backend.clicked, [CANCEL])
        self.assertNotIn(ACCEPT, backend.clicked)
        self.assertEqual(result.cancel_label, "取消")

    def test_a_dialog_without_cancel_is_refused_not_closed(self) -> None:
        backend = FakeBackend(*modal_world(labels=("确定",)))
        result = self._run(backend, blocked())
        self.assertIs(result.outcome, RecoveryOutcome.REFUSED)
        self.assertIn("确定", result.reason)
        self.assertEqual(backend.clicked, [])

    def test_an_ambiguous_dialog_set_is_refused(self) -> None:
        backend = FakeBackend(*modal_world(extra_dialogs=1))
        result = self._run(backend, blocked())
        self.assertIs(result.outcome, RecoveryOutcome.REFUSED)
        self.assertEqual(backend.clicked, [])

    def test_a_click_that_cannot_be_queued_is_reported(self) -> None:
        backend = FakeBackend(*modal_world(), click_ok=False)
        result = self._run(backend, blocked())
        self.assertIs(result.outcome, RecoveryOutcome.REFUSED)
        self.assertIn("could not be queued", result.reason)

    def test_a_silent_renderer_after_cancel_is_not_called_success(self) -> None:
        backend = FakeBackend(*modal_world())
        result = self._run(backend, blocked(), after=Probe(input_allowed=False, dialog_suspected=False))
        self.assertIs(result.outcome, RecoveryOutcome.STILL_BLOCKED)
        self.assertEqual(backend.clicked, [CANCEL])

    def test_the_event_is_json_safe(self) -> None:
        backend = FakeBackend(*modal_world())
        event = self._run(backend, blocked()).as_event()
        json.dumps(event)
        self.assertEqual(event["outcome"], "dismissed")
        self.assertEqual(event["dialog_handle"], DIALOG)
        self.assertEqual(event["cancel_label"], "取消")


class DismissContentTests(unittest.TestCase):
    """The dialog must say it is refreshing before anything is pressed."""

    def _run(self, texts, **kwargs):
        backend = FakeBackend(*modal_world())
        calls = {"n": 0}
        after = kwargs.get("after", responsive())

        def probe_fn():
            calls["n"] += 1
            return blocked() if calls["n"] == 1 else after

        result = dismiss_blocking_modal(
            allow_window_input=True,
            backend=backend,
            probe=probe_fn,
            polls=2,
            poll_seconds=0,
            sleep=lambda _s: None,
            read_texts=lambda _handle: texts,
            require_content=kwargs.get("require_content", True),
        )
        return result, backend

    def test_the_live_sentence_clears_the_gate(self) -> None:
        result, backend = self._run(LIVE_TEXTS)
        self.assertIs(result.outcome, RecoveryOutcome.DISMISSED)
        self.assertEqual(backend.clicked, [CANCEL])
        self.assertEqual(result.dialog_texts, LIVE_TEXTS)

    def test_unreadable_text_refuses_instead_of_pressing(self) -> None:
        result, backend = self._run(None)
        self.assertIs(result.outcome, RecoveryOutcome.CONTENT_UNVERIFIED)
        self.assertEqual(backend.clicked, [], "unverified content must never be pressed")

    def test_empty_text_refuses_instead_of_pressing(self) -> None:
        result, backend = self._run(())
        self.assertIs(result.outcome, RecoveryOutcome.CONTENT_UNVERIFIED)
        self.assertEqual(backend.clicked, [])

    def test_an_unrelated_dialog_is_not_cancelled(self) -> None:
        result, backend = self._run(("Delete this fleet?", "OK", "Cancel"))
        self.assertIs(result.outcome, RecoveryOutcome.UNRECOGNISED_DIALOG)
        self.assertEqual(backend.clicked, [], "another dialog's Cancel must stay untouched")

    def test_only_the_buttons_without_a_body_are_not_enough(self) -> None:
        result, backend = self._run(("确定", "取消"))
        self.assertIs(result.outcome, RecoveryOutcome.UNRECOGNISED_DIALOG)
        self.assertEqual(backend.clicked, [])

    def test_the_refusal_names_what_was_read(self) -> None:
        result, _backend = self._run(("Delete this fleet?", "OK", "Cancel"))
        self.assertIn("Delete this fleet?", result.reason)
        self.assertIn("ページ更新", result.reason)

    def test_the_gate_can_be_disabled_for_diagnostics_only(self) -> None:
        result, backend = self._run(None, require_content=False)
        self.assertIs(result.outcome, RecoveryOutcome.DISMISSED)
        self.assertEqual(backend.clicked, [CANCEL])

    def test_the_event_carries_the_read_text(self) -> None:
        result, _backend = self._run(LIVE_TEXTS)
        event = result.as_event()
        json.dumps(event)
        self.assertEqual(event["dialog_texts"], list(LIVE_TEXTS))


class ContentMarkerTests(unittest.TestCase):
    def test_a_marker_anywhere_in_any_text_confirms(self) -> None:
        self.assertTrue(content_confirms_refresh(LIVE_TEXTS))

    def test_no_text_is_not_confirmation(self) -> None:
        self.assertFalse(content_confirms_refresh(None))
        self.assertFalse(content_confirms_refresh(()))

    def test_a_mention_of_reloading_matches_the_measured_wording(self) -> None:
        self.assertIn("ページ更新", CONTENT_MARKERS)
        self.assertTrue(content_confirms_refresh(("エラーが発生したため、ページ更新します。",)))

    def test_case_and_spacing_do_not_rescue_unrelated_wording(self) -> None:
        self.assertFalse(content_confirms_refresh(("ページ 更新",)))
        self.assertFalse(content_confirms_refresh(("page reload",)))


class DismissBudgetTests(unittest.TestCase):
    def test_a_fresh_budget_can_still_press(self) -> None:
        budget = DismissBudget(limit=DEFAULT_DISMISS_LIMIT)
        self.assertTrue(budget.available())
        self.assertEqual(budget.describe(), f"0/{DEFAULT_DISMISS_LIMIT}")

    def test_spending_stops_being_possible_at_the_limit(self) -> None:
        budget = DismissBudget(limit=2)
        budget.spend()
        self.assertTrue(budget.available())
        budget.spend()
        self.assertFalse(budget.available())
        self.assertEqual(budget.describe(), "2/2")

    def test_spending_past_the_limit_is_an_error_not_a_silent_no_op(self) -> None:
        budget = DismissBudget(limit=1)
        budget.spend()
        with self.assertRaises(ValueError):
            budget.spend()

    def test_a_non_positive_limit_is_rejected(self) -> None:
        for bad in (0, -1):
            with self.subTest(limit=bad):
                with self.assertRaises(ValueError):
                    DismissBudget(limit=bad)
        with self.assertRaises(ValueError):
            DismissBudget(limit=True)

    def test_used_cannot_start_outside_the_limit(self) -> None:
        with self.assertRaises(ValueError):
            DismissBudget(limit=1, used=2)
        with self.assertRaises(ValueError):
            DismissBudget(limit=1, used=-1)


class BudgetedDismissTests(unittest.TestCase):
    def _run(self, backend, probe, **kwargs):
        after = kwargs.get("after", responsive())

        def counted():
            counted.calls += 1
            return probe if counted.calls == 1 else after

        counted.calls = 0
        return dismiss_blocking_modal(
            allow_window_input=True,
            backend=backend,
            probe=counted,
            polls=kwargs.get("polls", 2),
            poll_seconds=0,
            sleep=lambda _s: None,
            budget=kwargs.get("budget"),
            read_texts=lambda _handle: LIVE_TEXTS,
        )

    def test_a_spent_budget_touches_no_window_at_all(self) -> None:
        backend = FakeBackend(*modal_world())
        budget = DismissBudget(limit=1, used=1)
        result = self._run(backend, blocked(), budget=budget)
        self.assertIs(result.outcome, RecoveryOutcome.EXHAUSTED)
        self.assertEqual(backend.clicked, [])
        self.assertEqual(
            backend.enumerations, 0, "an exhausted budget must not even enumerate windows"
        )
        self.assertIn("1/1", result.reason)

    def test_the_exhausted_outcome_is_its_own_verdict(self) -> None:
        budget = DismissBudget(limit=1, used=1)
        result = self._run(FakeBackend(*modal_world()), blocked(), budget=budget)
        self.assertIsNot(result.outcome, RecoveryOutcome.REFUSED)
        self.assertEqual(result.as_event()["outcome"], "exhausted")

    def test_a_press_spends_exactly_one_unit(self) -> None:
        backend = FakeBackend(*modal_world())
        budget = DismissBudget(limit=3)
        result = self._run(backend, blocked(), budget=budget)
        self.assertIs(result.outcome, RecoveryOutcome.DISMISSED)
        self.assertEqual(backend.clicked, [CANCEL])
        self.assertEqual(budget.used, 1)
        self.assertEqual(result.budget, "1/3")

    def test_a_refusal_costs_nothing(self) -> None:
        budget = DismissBudget(limit=3)
        backend = FakeBackend(*modal_world(labels=("确定",)))
        result = self._run(backend, blocked(), budget=budget)
        self.assertIs(result.outcome, RecoveryOutcome.REFUSED)
        self.assertEqual(budget.used, 0)

    def test_a_no_op_costs_nothing(self) -> None:
        budget = DismissBudget(limit=3)
        result = self._run(FakeBackend(*modal_world()), responsive(), budget=budget)
        self.assertIs(result.outcome, RecoveryOutcome.NO_OP)
        self.assertEqual(budget.used, 0)

    def test_a_press_that_leaves_the_renderer_silent_still_spends(self) -> None:
        budget = DismissBudget(limit=3)
        backend = FakeBackend(*modal_world())
        result = self._run(
            backend, blocked(), budget=budget, after=Probe(input_allowed=False, dialog_suspected=False)
        )
        self.assertIs(result.outcome, RecoveryOutcome.STILL_BLOCKED)
        self.assertEqual(budget.used, 1, "the press happened, so it counts")

    def test_without_a_budget_behaviour_is_unchanged(self) -> None:
        result = self._run(FakeBackend(*modal_world()), blocked())
        self.assertIs(result.outcome, RecoveryOutcome.DISMISSED)
        self.assertIsNone(result.budget)


class DialogTextsToolTests(unittest.TestCase):
    """The calibration hook: reading the wording must never send input."""

    def test_the_wording_can_be_read_without_touching_the_dialog(self) -> None:
        from tools import check_poi_renderer as tool

        backend = FakeBackend(
            *modal_world(),
            texts=(
                ("Static", "エラーが発生したため、ページ更新します。"),
                ("Button", "确定"),
                ("Button", "取消"),
            ),
        )
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = tool._dialog_texts(backend)
        output = buffer.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("ページ更新します", output)
        self.assertIn(hex(DIALOG), output)
        self.assertEqual(backend.clicked, [], "reading must not press anything")

    def test_no_dialog_means_there_is_nothing_to_calibrate(self) -> None:
        from tools import check_poi_renderer as tool

        backend = FakeBackend(*modal_world(dialog=False))
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = tool._dialog_texts(backend)
        self.assertEqual(code, 2)
        self.assertIn("nothing to calibrate", buffer.getvalue())

    def test_the_flag_returns_before_any_cdp_probe(self) -> None:
        from tools import check_poi_renderer as tool

        original_texts, original_probe = tool._dialog_texts, tool.live_probe
        tool._dialog_texts = lambda: 7

        def explode(**kwargs):
            raise AssertionError("--dialog-texts must not probe CDP")

        tool.live_probe = explode
        try:
            with redirect_stdout(io.StringIO()):
                code = tool.main(["--dialog-texts"])
        finally:
            tool._dialog_texts = original_texts
            tool.live_probe = original_probe
        self.assertEqual(code, 7)


class ModalBudgetWiringTests(unittest.TestCase):
    def test_auto_dismiss_creates_one_shared_allowance(self) -> None:
        from tools.run_continuous_expedition_monitor import _modal_budget

        budget = _modal_budget(
            SimpleNamespace(auto_dismiss_modal=True, modal_dismiss_limit=2)
        )
        self.assertIsNotNone(budget)
        self.assertEqual(budget.limit, 2)

    def test_turning_auto_dismiss_off_creates_none(self) -> None:
        from tools.run_continuous_expedition_monitor import _modal_budget

        budget = _modal_budget(
            SimpleNamespace(auto_dismiss_modal=False, modal_dismiss_limit=2)
        )
        self.assertIsNone(budget)


class MessageTests(unittest.TestCase):
    def test_the_click_message_is_bm_click(self) -> None:
        self.assertEqual(BM_CLICK, 0x00F5)


if __name__ == "__main__":
    unittest.main()
