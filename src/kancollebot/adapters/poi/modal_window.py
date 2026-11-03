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
  must pass ``allow_window_input=True`` explicitly;
* a shared :class:`DismissBudget` caps how many times one process may press, so
  a persistent network fault cannot be silenced into an endless press loop.

**The dialog is identified by content, not only by shape.** The body sentence is
not a child control -- TaskDialog paints it into a ``DirectUIHWND`` (measured
350x90), so ``GetWindowText`` returns nothing for it and MSAA reports only the
window title. It is reachable through UI Automation, which walks the DirectUI
tree. Measured live on the real prompt:

    'エラーが発生したため、ページ更新します。'
    '确定'  '取消'  '关闭'

So before any press, the sentence must be read and must contain a marker from
:data:`CONTENT_MARKERS`. A dialog whose text cannot be read, or whose text does
not mention refreshing, is refused -- that is what stops an unrelated Poi dialog
from being cancelled just because it happens to offer a Cancel button.

The two languages are separate on purpose: the **body** is game text and is
Japanese only (KanColle has no other client), while the **button labels** are
drawn by Poi's Electron shell and follow the Windows locale, which is why the
live prompt reads ``'エラーが発生したため、ページ更新します。'`` with
``'确定'``/``'取消'``/``'关闭'`` underneath it.

Nothing here is Windows-version specific beyond plain user32/ole32 calls, and
UI Automation is driven through ctypes so the project stays dependency-free.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol


MODAL_DIALOG_CLASSES = frozenset({"#32770"})
POI_EXECUTABLE = "poi.exe"
# The dialog is drawn by Poi's Electron main process, so its button labels come
# from the *host* locale (the live machine reports Simplified Chinese: 确定 /
# 取消 / 关闭), never from the game. That is why this set stays multilingual
# even though the game text below is Japanese only. Matched exactly, after
# stripping whitespace and Windows accelerator markers, so "取消并关闭" is not
# accepted.
CANCEL_LABELS = frozenset({"取消", "キャンセル", "cancel", "abbrechen", "annuler"})
BM_CLICK = 0x00F5

# The body of the refresh prompt, as measured from the live UI Automation tree:
#   'エラーが発生したため、ページ更新します。'
# This sentence is game text, and KanColle ships a Japanese client only -- there
# is no other language build to accommodate, so a single marker set is correct
# rather than a placeholder for future i18n. Matching one of these is what
# distinguishes "the game wants to reload" from "some other Poi dialog that
# happens to have a Cancel button". Any wording that is not listed still fails
# closed with UNRECOGNISED_DIALOG rather than being pressed blind, because an
# unreadable or unexpected body means we are looking at a dialog we do not
# understand, not at a translation of the one we do.
CONTENT_MARKERS = ("ページ更新",)

POLL_SECONDS = 0.4
DEFAULT_POLLS = 8
# One process gets three presses. A prompt that keeps coming back means the
# network is still down, and pressing a fourth time would only hide that.
DEFAULT_DISMISS_LIMIT = 3


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
    # The budget ran out, so nothing was touched. Distinct from REFUSED: the
    # dialog was fine, the reason is that pressing again would be hiding a
    # fault rather than fixing one.
    EXHAUSTED = "exhausted"
    # The dialog's own text could not be read at all, so there is no way to
    # confirm it is the refresh prompt. Refused rather than pressed.
    CONTENT_UNVERIFIED = "content_unverified"
    # The text was read and does not mention refreshing, so this is a different
    # dialog. Refused: pressing Cancel here would be cancelling something else.
    UNRECOGNISED_DIALOG = "unrecognised_dialog"


@dataclass(slots=True)
class DismissBudget:
    """How many times one process may still press Cancel.

    Sharing one instance across every recovery point is the whole point: the
    startup probe and each failed tick draw from the same allowance, so a fault
    that keeps raising the prompt stops being dismissable after a few presses
    instead of being quietly cancelled forever.
    """

    limit: int = DEFAULT_DISMISS_LIMIT
    used: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.limit, bool) or not isinstance(self.limit, int) or self.limit < 1:
            raise ValueError("limit must be a positive integer")
        if self.used < 0 or self.used > self.limit:
            raise ValueError("used must be within the limit")

    def available(self) -> bool:
        return self.used < self.limit

    def spend(self) -> None:
        if not self.available():
            raise ValueError("the dismiss budget is already spent")
        self.used += 1

    def describe(self) -> str:
        return f"{self.used}/{self.limit}"


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
    budget: str | None = None
    dialog_texts: tuple[str, ...] = ()

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
            "dismiss_budget": self.budget,
            "dialog_texts": list(self.dialog_texts),
            "probe_after": after,
        }


class WindowBackend(Protocol):
    """The narrow slice of user32 this module needs."""

    def top_level_windows(self) -> tuple[WindowInfo, ...]:
        """Every visible or hidden top-level window, with its owning executable."""

    def child_buttons(self, handle: int) -> tuple[tuple[int, str], ...]:
        """The (handle, label) of each button child, in enumeration order."""

    def child_texts(self, handle: int) -> tuple[tuple[str, str], ...]:
        """Every child control's (class name, text), for naming a dialog.

        Read-only; used to work out what a dialog actually says before anything
        is allowed to match on that wording.
        """

    def window_exists(self, handle: int) -> bool:
        """True while the window handle is still valid."""

    def post_click(self, handle: int) -> bool:
        """Post one BM_CLICK to a button; True when the message was queued."""


def normalise_label(label: str) -> str:
    """Strip accelerator markers and whitespace before comparing a label."""
    return label.replace("&", "").strip().casefold()


# --- reading what the dialog says ---------------------------------------
#
# TaskDialog paints its body into a DirectUIHWND, so the sentence is not a
# child window: GetWindowText returns "" and MSAA reports only the title. UI
# Automation walks that DirectUI tree and does expose the text, so it is driven
# here directly through ctypes -- no comtypes, no pywin32, matching the rest of
# the project. Only ``NameProperty`` is read; nothing is invoked.
_UIA_CLSID = (0xFF48DBA4, 0x60EF, 0x4201, (0xAA, 0x87, 0x54, 0x10, 0x3E, 0xEF, 0x59, 0x4E))
_UIA_IID = (0x30CBE57D, 0xD9D0, 0x452A, (0xAB, 0x13, 0x7A, 0xC5, 0xAC, 0x48, 0x25, 0xEE))
_UIA_NAME_PROPERTY = 30005
_UIA_TREE_SCOPE_DESCENDANTS = 4
_UIA_VT_BSTR = 8


def read_dialog_texts(handle: int) -> tuple[str, ...] | None:
    """Every name UI Automation exposes under ``handle``.

    Returns ``None`` when UIA cannot answer at all (not Windows, COM refused,
    the window is not a UIA provider, no element for the handle). An empty
    tuple means UIA answered but the dialog carries no text. Callers must treat
    both as "cannot confirm", never as "no objection".

    Never raises: this runs while the game surface is already broken, and a
    COM exception escaping here would unwind the recovery instead of refusing
    it, so every failure collapses to ``None``.
    """
    try:
        return _uia_texts(handle)
    except Exception:  # noqa: BLE001 - must never raise into a recovery path
        return None


def _uia_texts(handle: int) -> tuple[str, ...] | None:
    if sys.platform != "win32":
        return None
    import ctypes
    import ctypes.wintypes as wt

    class _Guid(ctypes.Structure):
        _fields_ = [
            ("data1", wt.DWORD),
            ("data2", wt.WORD),
            ("data3", wt.WORD),
            ("data4", ctypes.c_ubyte * 8),
        ]

    def _guid(parts: tuple[int, int, int, tuple[int, ...]]) -> "_Guid":
        first, second, third, rest = parts
        return _Guid(first, second, third, (ctypes.c_ubyte * 8)(*rest))

    # 64-bit VARIANT is 24 bytes; over-allocating is harmless on 32-bit because
    # COM writes at most sizeof(VARIANT).
    class _Variant(ctypes.Structure):
        _fields_ = [
            ("vt", ctypes.c_ushort),
            ("r1", ctypes.c_ushort),
            ("r2", ctypes.c_ushort),
            ("r3", ctypes.c_ushort),
            ("value", ctypes.c_ulonglong),
            ("extra", ctypes.c_ulonglong),
        ]

    try:
        ole32 = ctypes.WinDLL("ole32")
        oleaut32 = ctypes.WinDLL("oleaut32")
    except OSError:
        return None

    ole32.CoInitializeEx(None, 0x2)  # STA; S_FALSE when already initialised

    automation = ctypes.c_void_p()
    clsid, iid = _guid(_UIA_CLSID), _guid(_UIA_IID)
    if ole32.CoCreateInstance(
        ctypes.byref(clsid), None, 0x1, ctypes.byref(iid), ctypes.byref(automation)
    ) != 0 or not automation.value:
        return None

    def vtable(obj: ctypes.c_void_p) -> ctypes.Array:
        return ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents

    def call(obj: ctypes.c_void_p, index: int, restype, *argtypes):
        return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(
            vtable(obj)[index]
        )

    def release(obj: ctypes.c_void_p) -> None:
        if obj and obj.value:
            call(obj, 2, ctypes.c_ulong)(obj)

    element = ctypes.c_void_p()
    try:
        # UIA raises rather than returning an error code when the window is not
        # a UIA provider (measured: WinError -2147220991). Every such failure
        # means the same thing here -- the text cannot be read -- so it is
        # reported as None and the caller refuses, instead of unwinding a
        # recovery path that is already handling a broken surface.
        try:
            if call(automation, 6, ctypes.HRESULT, wt.HWND, ctypes.POINTER(ctypes.c_void_p))(
                automation, wt.HWND(handle), ctypes.byref(element)
            ) != 0 or not element.value:
                return None
        except OSError:
            return None

        condition = ctypes.c_void_p()
        if call(automation, 21, ctypes.HRESULT, ctypes.POINTER(ctypes.c_void_p))(
            automation, ctypes.byref(condition)
        ) != 0 or not condition.value:
            return None

        array = ctypes.c_void_p()
        try:
            if call(
                element,
                6,
                ctypes.HRESULT,
                ctypes.c_int,
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_void_p),
            )(element, _UIA_TREE_SCOPE_DESCENDANTS, condition, ctypes.byref(array)) != 0:
                return None
            if not array.value:
                return ()

            length = ctypes.c_int()
            call(array, 3, ctypes.HRESULT, ctypes.POINTER(ctypes.c_int))(
                array, ctypes.byref(length)
            )
            texts: list[str] = []
            for index in range(max(0, length.value)):
                child = ctypes.c_void_p()
                if call(
                    array, 4, ctypes.HRESULT, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)
                )(array, index, ctypes.byref(child)) != 0 or not child.value:
                    continue
                try:
                    variant = _Variant()
                    if call(
                        child, 10, ctypes.HRESULT, ctypes.c_int, ctypes.POINTER(_Variant)
                    )(child, _UIA_NAME_PROPERTY, ctypes.byref(variant)) != 0:
                        continue
                    if variant.vt == _UIA_VT_BSTR and variant.value:
                        text = ctypes.wstring_at(variant.value)
                        if text.strip():
                            texts.append(text)
                finally:
                    release(child)
            return tuple(texts)
        finally:
            release(array)
            release(condition)
    finally:
        release(element)
        release(automation)


def content_confirms_refresh(
    texts: tuple[str, ...] | None, *, markers: tuple[str, ...] = CONTENT_MARKERS
) -> bool:
    """True only when readable text actually mentions refreshing."""
    if not texts:
        return False
    return any(marker in text for text in texts for marker in markers)


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
    budget: DismissBudget | None = None,
    read_texts: Callable[[int], tuple[str, ...] | None] = read_dialog_texts,
    require_content: bool = True,
) -> ModalRecovery:
    """Press Cancel on the blocking dialog, after proving it is the blocker.

    ``probe`` must return a verdict shaped like ``renderer_block.BlockProbe``:
    ``input_allowed``, ``dialog_suspected`` and ``as_event``. The dismissal only
    starts from a state CDP has already declared unusable *and* attributed to a
    modal, so no window is ever touched on a surface that merely looked slow.

    A ``budget`` that is already spent ends the attempt before any window is
    enumerated, and a press that actually happens spends one unit. Refusals and
    no-ops cost nothing, so the allowance counts presses, not attempts.

    ``require_content`` is the second fence: the dialog's own sentence is read
    through UI Automation and must mention refreshing. Unreadable text gives
    ``CONTENT_UNVERIFIED`` and readable-but-unrelated text gives
    ``UNRECOGNISED_DIALOG``; both refuse, so a different Poi dialog that happens
    to offer Cancel is never the thing that gets cancelled. Turning it off is
    only for tests and diagnostics -- it removes the only check that looks at
    what the dialog says.
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
            RecoveryOutcome.NO_OP,
            "the game renderer already answers",
            None,
            None,
            before,
            None if budget is None else budget.describe(),
        )
    if not getattr(before, "dialog_suspected", False):
        return ModalRecovery(
            RecoveryOutcome.REFUSED,
            "the block is not attributable to a modal, so no window is touched",
            None,
            None,
            before,
            None if budget is None else budget.describe(),
        )
    if budget is not None and not budget.available():
        return ModalRecovery(
            RecoveryOutcome.EXHAUSTED,
            (
                f"the auto-dismiss budget is spent ({budget.describe()}); the prompt "
                "keeps returning, so this is a fault to look at, not to cancel again"
            ),
            None,
            None,
            before,
            budget.describe(),
        )

    lookup = find_modal_dialog(backend, exe=exe)
    if lookup.state is not DialogState.FOUND or lookup.dialog is None:
        return ModalRecovery(
            RecoveryOutcome.REFUSED,
            lookup.detail,
            None,
            None,
            before,
            None if budget is None else budget.describe(),
        )

    button, labels = find_cancel_button(backend, lookup.dialog)
    if button is None:
        return ModalRecovery(
            RecoveryOutcome.REFUSED,
            f"no single cancel button among {labels!r}; refusing to close blindly",
            lookup.dialog,
            None,
            before,
            None if budget is None else budget.describe(),
        )

    texts: tuple[str, ...] = ()
    if require_content:
        read = read_texts(lookup.dialog.handle)
        if not read:
            return ModalRecovery(
                RecoveryOutcome.CONTENT_UNVERIFIED,
                (
                    "the dialog's own text could not be read, so it cannot be "
                    "confirmed as the refresh prompt; nothing was pressed"
                ),
                lookup.dialog,
                button.label,
                before,
                None if budget is None else budget.describe(),
            )
        texts = read
        if not content_confirms_refresh(texts):
            return ModalRecovery(
                RecoveryOutcome.UNRECOGNISED_DIALOG,
                (
                    f"the dialog says {list(texts)!r}, which does not mention "
                    f"refreshing ({list(CONTENT_MARKERS)!r}); refusing to cancel a "
                    "dialog that is not the known prompt"
                ),
                lookup.dialog,
                button.label,
                before,
                None if budget is None else budget.describe(),
                texts,
            )

    if not backend.post_click(button.handle):
        return ModalRecovery(
            RecoveryOutcome.REFUSED,
            "the click message could not be queued",
            lookup.dialog,
            button.label,
            before,
            None if budget is None else budget.describe(),
            texts,
        )
    if budget is not None:
        budget.spend()

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
            None if budget is None else budget.describe(),
            texts,
        )
    return ModalRecovery(
        RecoveryOutcome.STILL_BLOCKED,
        "the dialog is gone but the renderer still does not answer",
        lookup.dialog,
        button.label,
        after,
        None if budget is None else budget.describe(),
        texts,
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

    def child_texts(self, handle: int) -> tuple[tuple[str, str], ...]:
        ctypes, wt, user32 = self._ctypes, self._wt, self._user32
        found: list[tuple[str, str]] = []
        enum_proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

        def collect(child, _lparam):
            class_buffer = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(child, class_buffer, 128)
            text = self._text(child, 512)
            if text:
                found.append((class_buffer.value, text))
            return True

        user32.EnumChildWindows(handle, enum_proc(collect), 0)
        return tuple(found)

    def post_click(self, handle: int) -> bool:
        return bool(self._user32.PostMessageW(handle, BM_CLICK, 0, 0))
