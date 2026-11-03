"""Offline tests for window-level Cancel, against a fake user32 backend.

The fake reproduces the live shape measured on a real block: a disabled Poi main
window, one ``#32770`` dialog owned by it, and buttons labelled 确定 / 取消.
"""
from __future__ import annotations

import json
import unittest

from kancollebot.adapters.poi.modal_window import (
    BM_CLICK,
    DialogState,
    RecoveryOutcome,
    WindowInfo,
    WindowInputNotAllowed,
    dismiss_blocking_modal,
    find_cancel_button,
    find_modal_dialog,
    normalise_label,
)


MAIN = 0x100
DIALOG = 0x200
ACCEPT = 0x301
CANCEL = 0x302


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
    def __init__(self, windows, buttons, *, click_ok=True, dialog_persists=False):
        self.windows = windows
        self.buttons = buttons
        self.click_ok = click_ok
        self.dialog_persists = dialog_persists
        self.clicked: list[int] = []

    def top_level_windows(self):
        return tuple(self.windows)

    def child_buttons(self, handle):
        return tuple(self.buttons.items())

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

        def probe_fn():
            calls["n"] += 1
            return probe if calls["n"] == 1 else after

        return dismiss_blocking_modal(
            allow_window_input=True,
            backend=backend,
            probe=probe_fn,
            polls=kwargs.get("polls", 2),
            poll_seconds=0,
            sleep=lambda _s: None,
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


class MessageTests(unittest.TestCase):
    def test_the_click_message_is_bm_click(self) -> None:
        self.assertEqual(BM_CLICK, 0x00F5)


if __name__ == "__main__":
    unittest.main()
