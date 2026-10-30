"""Fail-closed UI state classification from accessible text evidence.

This module does not inspect pixels or infer canvas contents. If the renderer does
not expose sufficient accessible labels, classification remains UNKNOWN and the
caller must not send input.
"""
from __future__ import annotations

from enum import Enum
from typing import Any


class UiState(str, Enum):
    HOME_PORT = "HOME_PORT"
    RETURN_RESULT = "RETURN_RESULT"
    BONUS_ITEMS = "BONUS_ITEMS"
    ERROR_DIALOG = "ERROR_DIALOG"
    UNKNOWN = "UNKNOWN"


class UiStateGateError(RuntimeError):
    """Raised when accessible evidence cannot authorize the expected UI action."""


# Keep these phrases specific: generic words such as "next" or "confirm" alone
# are not enough to infer a game screen.
_STATE_MARKERS: dict[UiState, tuple[str, ...]] = {
    UiState.HOME_PORT: ("母港", "home port"),
    UiState.RETURN_RESULT: ("遠征 失敗", "遠征 成功", "expedition failed", "expedition success"),
    UiState.BONUS_ITEMS: ("獲得資源", "獲得アイテム", "bonus items", "reward items"),
    UiState.ERROR_DIALOG: ("エラーが発生したため", "an error occurred"),
}
_CANCEL_MARKERS = ("取消", "キャンセル", "cancel")


def _accessible_text(nodes: Any) -> tuple[str, ...]:
    if not isinstance(nodes, list):
        raise ValueError("Accessibility tree nodes must be a list")
    texts: list[str] = []
    for node in nodes:
        if not isinstance(node, dict) or node.get("ignored") is True:
            continue
        name = node.get("name")
        if isinstance(name, dict):
            name = name.get("value")
        if isinstance(name, str) and name.strip():
            texts.append(name.strip().casefold())
    return tuple(texts)


def classify_accessible_state(nodes: Any) -> UiState:
    """Classify only when exactly one state has explicit accessible evidence."""
    texts = _accessible_text(nodes)
    matched = [
        state for state, markers in _STATE_MARKERS.items()
        if any(marker.casefold() in text for marker in markers for text in texts)
    ]
    return matched[0] if len(matched) == 1 else UiState.UNKNOWN


def has_unique_cancel_control(nodes: Any) -> bool:
    """True only when an error dialog and exactly one named cancel control exist."""
    if classify_accessible_state(nodes) is not UiState.ERROR_DIALOG:
        return False
    cancel_names = []
    for node in nodes:
        if not isinstance(node, dict) or node.get("ignored") is True:
            continue
        role = node.get("role")
        role = role.get("value") if isinstance(role, dict) else role
        name = node.get("name")
        name = name.get("value") if isinstance(name, dict) else name
        if isinstance(name, str) and any(mark in name.casefold() for mark in _CANCEL_MARKERS):
            if isinstance(role, str) and role.casefold() in {"button", "pushbutton"}:
                cancel_names.append(name)
    return len(cancel_names) == 1


def require_state(nodes: Any, expected: UiState) -> None:
    """Raise unless current accessible evidence uniquely matches expected state."""
    actual = classify_accessible_state(nodes)
    if actual is not expected:
        raise UiStateGateError(
            f"Expected accessible UI state {expected.value}, observed {actual.value}; refusing input"
        )
    if actual is UiState.ERROR_DIALOG and not has_unique_cancel_control(nodes):
        raise UiStateGateError("Error dialog has no uniquely identified accessible Cancel control")
