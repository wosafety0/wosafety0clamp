"""Dismiss the blocking refresh dialog by pressing its own Cancel button.

The refresh prompt the game raises on a network fault is a real Win32 dialog,
not a Chromium-rendered one: it is a ``#32770`` top-level window owned by Poi's
main window, which is disabled while it is up. That is why CDP can neither see
nor close it -- the renderer is parked and the dialog lives outside the page.

Because it is a real dialog it owns real child buttons, each carrying its label:

    #32770 (owner = main window, main window disabled)
      Button  "确定"     <- reloads the page
      Button  "取消"     <- cancels

So the dismissal does not need coordinates, OCR or a guess. The Cancel button is
selected **by its label**, and the click is posted straight to that button. The
accept button is never a candidate, so an accidental reload is not merely
unlikely, it is unreachable: nothing in this module can address "确定".

This is the only input path in the project that leaves CDP, so it is fenced:

* it runs only after CDP has proved the game renderer is parked while the Poi
  shell still answers, so a merely slow page can never trigger a window click;
* it requires exactly one modal dialog owned by Poi's main window, and refuses
  when the picture is ambiguous;
* it requires exactly one cancel-labelled button, and refuses when there is none
  or more than one -- there is no fallback that closes the dialog blindly;
* it never retries a failed dismissal beyond the bounded poll, and the caller
  must pass ``allow_window_input=True`` explicitly.

Nothing here is Windows-version specific beyond plain user32 calls.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol


MODAL_DIALOG_CLASSES = frozenset({"#32770"})
POI_EXECUTABLE = "poi.exe"
# Labels that mean "do not do the thing". Matched exactly, after stripping
# whitespace and Windows accelerator markers, so "取消并关闭" is not accepted.
CANCEL_LABELS = frozenset({"取消", "キャンセル", "cancel", "abbrechen", "annuler"})
BM_CLICK = 0x00F5

POLL_SECONDS = 0.4
DEFAULT_POLLS = 8


class WindowInputNotAllowed(RuntimeError):
    """Raised when window-level input was attempted without explicit consent."""


class WindowAccessUnavailable(RuntimeError):
    """Raised when the platform cannot enumerate windows."""


class DialogState(str, Enum):
    FOUND = "found"
    NONE = "none"
    AMBIGUOUS = "ambiguous"


class RecoveryOutcome(str, Enum):
    NO_OP = "no_op"
    REFUSED = "refused"
    DISMISSED = "dismissed"
    STILL_BLOCKED = "still_blocked"


@dataclass(frozen=True, slots=True)
class WindowInfo:
    handle: int
    pid: int
    exe: str
    title: str
    class_name: str
    owner: int
    visible: bool
    enabled: bool
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class ModalDialog:
    handle: int
    owner_handle: int
    title: str
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class DialogLookup:
    state: DialogState
    dialog: ModalDialog | None
    candidates: tuple[WindowInfo, ...]
    detail: str


@dataclass(frozen=True, slots=True)
class CancelButton:
    handle: int
    label: str


@dataclass(frozen=True, slots=True)
class ModalRecovery:
    outcome: RecoveryOutcome
    reason: str
    dialog: ModalDialog | None
    cancel_label: str | None
    probe_after: object | None

    def as_event(self) -> dict[str, object]:
        """Flatten into JSON-safe telemetry."""
        after = None
        as_event = getattr(self.probe_after, "as_event", None)
        if callable(as_event):
            after = as_event()
        return {
            "outcome": self.outcome.value,
            "reason": self.reason,
            "dialog_handle": None if self.dialog is None else self.dialog.handle,
            "cancel_label": self.cancel_label,
            "probe_after": after,
        }


class WindowBackend(Protocol):
    """The narrow slice of user32 this module needs."""

    def top_level_windows(self) -> tuple[WindowInfo, ...]:
        """Every visible or hidden top-level window, with its owning executable."""

    def child_buttons(self, handle: int) -> tuple[tuple[int, str], ...]:
        """The (handle, label) of each button child, in enumeration order."""

    def window_exists(self, handle: int) -> bool:
        """True while the window handle is still valid."""

    def post_click(self, handle: int) -> bool:
        """Post one BM_CLICK to a button; True when the message was queued."""


def normalise_label(label: str) -> str:
    """Strip accelerator markers and whitespace before comparing a label."""
    return label.replace("&", "").strip().casefold()


def find_modal_dialog(
    backend: WindowBackend, *, exe: str = POI_EXECUTABLE
) -> DialogLookup:
    """Locate the one dialog that is modal over Poi's main window.

    A ``#32770`` window owned by the main window, while that main window is
    disabled, is the signature of a blocking dialog. Anything other than
    exactly one such window is reported rather than guessed at.
    """
    windows = backend.top_level_windows()
    owned = [w for w in windows if w.exe.casefold().endswith(exe.casefold())]
    if not owned:
        return DialogLookup(DialogState.NONE, None, (), "Poi has no top-level window")
    by_handle = {w.handle: w for w in owned}

    # The owner is resolved per candidate instead of guessing which of Poi's
    # several top-level windows is "the main one" by area: the property that
    # actually matters is that the window *owning this dialog* is disabled,
    # which is what a modal loop does to its owner.
    candidates = tuple(
        w
        for w in owned
        if w.visible
        and w.owner != 0
        and w.class_name in MODAL_DIALOG_CLASSES
        and (owner := by_handle.get(w.owner)) is not None
        and owner.visible
        and owner.owner == 0
        and not owner.enabled
    )
    if not candidates:
        return DialogLookup(
            DialogState.NONE,
            None,
            (),
            "no #32770 dialog is owned by a disabled Poi window",
        )
    if len(candidates) > 1:
        return DialogLookup(
            DialogState.AMBIGUOUS,
            None,
            candidates,
            f"{len(candidates)} modal dialogs are open at once",
        )
    found = candidates[0]
    return DialogLookup(
        DialogState.FOUND,
        ModalDialog(
            handle=found.handle,
            owner_handle=found.owner,
            title=found.title,
            width=found.width,
            height=found.height,
        ),
        candidates,
        "one modal dialog is owned by a disabled Poi window",
    )


def find_cancel_button(
    backend: WindowBackend, dialog: ModalDialog
) -> tuple[CancelButton | None, tuple[str, ...]]:
    """Return the single cancel-labelled button, plus every label seen.

    The full label list is returned so a refusal can say what the dialog
    actually offered instead of failing mutely.
    """
    labels: list[str] = []
    matches: list[CancelButton] = []
    for handle, label in backend.child_buttons(dialog.handle):
        labels.append(label)
        if normalise_label(label) in CANCEL_LABELS:
            matches.append(CancelButton(handle, label))
    if len(matches) != 1:
        return None, tuple(labels)
    return matches[0], tuple(labels)


def dismiss_blocking_modal(
    *,
    allow_window_input: bool,
    backend: WindowBackend,
    probe: Callable[[], object],
    exe: str = POI_EXECUTABLE,
    polls: int = DEFAULT_POLLS,
    poll_seconds: float = POLL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> ModalRecovery:
    """Press Cancel on the blocking dialog, after proving it is the blocker.

    ``probe`` must return a verdict shaped like ``renderer_block.BlockProbe``:
    ``input_allowed``, ``dialog_suspected`` and ``as_event``. The dismissal only
    starts from a state CDP has already declared unusable *and* attributed to a
    modal, so no window is ever touched on a surface that merely looked slow.
    """
    if not allow_window_input:
        raise WindowInputNotAllowed(
            "window-level input needs explicit consent; CDP alone cannot press Cancel"
        )
    if polls < 1 or poll_seconds < 0:
        raise ValueError("polls must be positive and poll_seconds non-negative")

    before = probe()
    if getattr(before, "input_allowed", False):
        return ModalRecovery(
            RecoveryOutcome.NO_OP, "the game renderer already answers", None, None, before
        )
    if not getattr(before, "dialog_suspected", False):
        return ModalRecovery(
            RecoveryOutcome.REFUSED,
            "the block is not attributable to a modal, so no window is touched",
            None,
            None,
            before,
        )

    lookup = find_modal_dialog(backend, exe=exe)
    if lookup.state is not DialogState.FOUND or lookup.dialog is None:
        return ModalRecovery(
            RecoveryOutcome.REFUSED, lookup.detail, None, None, before
        )

    button, labels = find_cancel_button(backend, lookup.dialog)
    if button is None:
        return ModalRecovery(
            RecoveryOutcome.REFUSED,
            f"no single cancel button among {labels!r}; refusing to close blindly",
            lookup.dialog,
            None,
            before,
        )

    if not backend.post_click(button.handle):
        return ModalRecovery(
            RecoveryOutcome.REFUSED,
            "the click message could not be queued",
            lookup.dialog,
            button.label,
            before,
        )

    for _ in range(polls):
        if not backend.window_exists(lookup.dialog.handle):
            break
        sleep(poll_seconds)

    after = probe()
    if getattr(after, "input_allowed", False):
        return ModalRecovery(
            RecoveryOutcome.DISMISSED,
            "cancel was pressed and the game renderer answers again",
            lookup.dialog,
            button.label,
            after,
        )
    return ModalRecovery(
        RecoveryOutcome.STILL_BLOCKED,
        "the dialog is gone but the renderer still does not answer",
        lookup.dialog,
        button.label,
        after,
    )


class Win32Backend:
    """The user32 backend; refuses to load anywhere but Windows."""

    _GW_OWNER = 4
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    def __init__(self) -> None:
        if not sys.platform.startswith("win"):
            raise WindowAccessUnavailable("window enumeration needs Windows")
        import ctypes
        import ctypes.wintypes as wt

        self._ctypes = ctypes
        self._wt = wt
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def _exe_of(self, pid: int) -> str:
        process = self._kernel32.OpenProcess(
            self._PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not process:
            return ""
        try:
            buffer = self._ctypes.create_unicode_buffer(1024)
            size = self._wt.DWORD(1024)
            ok = self._kernel32.QueryFullProcessImageNameW(
                process, 0, buffer, self._ctypes.byref(size)
            )
            return buffer.value if ok else ""
        finally:
            self._kernel32.CloseHandle(process)

    def _text(self, handle: int, size: int = 512) -> str:
        buffer = self._ctypes.create_unicode_buffer(size)
        self._user32.GetWindowTextW(handle, buffer, size)
        return buffer.value

    def top_level_windows(self) -> tuple[WindowInfo, ...]:
        ctypes, wt, user32 = self._ctypes, self._wt, self._user32
        rows: list[WindowInfo] = []
        enum_proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

        def collect(handle, _lparam):
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
            exe = self._exe_of(pid.value)
            if not exe:
                return True
            class_buffer = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(handle, class_buffer, 256)
            rect = wt.RECT()
            user32.GetWindowRect(handle, ctypes.byref(rect))
            rows.append(
                WindowInfo(
                    handle=handle,
                    pid=pid.value,
                    exe=exe,
                    title=self._text(handle),
                    class_name=class_buffer.value,
                    owner=user32.GetWindow(handle, self._GW_OWNER),
                    visible=bool(user32.IsWindowVisible(handle)),
                    enabled=bool(user32.IsWindowEnabled(handle)),
                    width=rect.right - rect.left,
                    height=rect.bottom - rect.top,
                )
            )
            return True

        user32.EnumWindows(enum_proc(collect), 0)
        return tuple(rows)

    def child_buttons(self, handle: int) -> tuple[tuple[int, str], ...]:
        ctypes, wt, user32 = self._ctypes, self._wt, self._user32
        found: list[tuple[int, str]] = []
        enum_proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

        def collect(child, _lparam):
            class_buffer = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(child, class_buffer, 128)
            if class_buffer.value.casefold() == "button":
                found.append((child, self._text(child, 256)))
            return True

        user32.EnumChildWindows(handle, enum_proc(collect), 0)
        return tuple(found)

    def window_exists(self, handle: int) -> bool:
        return bool(self._user32.IsWindow(handle))

    def post_click(self, handle: int) -> bool:
        return bool(self._user32.PostMessageW(handle, BM_CLICK, 0, 0))
