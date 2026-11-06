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
    HOME_PORT_RETURN_NOTICE = "HOME_PORT_RETURN_NOTICE"
    SORTIE_MENU = "SORTIE_MENU"
    EXPEDITION_LIST = "EXPEDITION_LIST"
    SUPPLY_MENU = "SUPPLY_MENU"
    SUPPLY_FLEET_1_SELECTED = "SUPPLY_FLEET_1_SELECTED"
    SUPPLY_FLEET_2_SELECTED = "SUPPLY_FLEET_2_SELECTED"
    SUPPLY_FLEET_3_SELECTED = "SUPPLY_FLEET_3_SELECTED"
    # Fleet 4's supply tab has a captured fingerprint template since
    # 2026-11-06 (crop 341,155,30,40). The enum member is what lets the page
    # classifier name that state; the relative-highlight fallback it once
    # needed is retired (TEMPLATE_FREE_SUPPLY_FLEETS is empty). The *dispatch*
    # tab state FLEET_4_SELECTED below is a different, still-untemplated page.
    SUPPLY_FLEET_4_SELECTED = "SUPPLY_FLEET_4_SELECTED"
    SUPPLY_FLEET_2_SHIP_1_CHECKED = "SUPPLY_FLEET_2_SHIP_1_CHECKED"
    SUPPLY_FLEET_2_SHIP_1_UNCHECKED = "SUPPLY_FLEET_2_SHIP_1_UNCHECKED"
    SUPPLY_FLEET_2_SHIP_2_CHECKED = "SUPPLY_FLEET_2_SHIP_2_CHECKED"
    SUPPLY_FLEET_2_SHIP_2_UNCHECKED = "SUPPLY_FLEET_2_SHIP_2_UNCHECKED"
    SUPPLY_FLEET_2_SHIP_3_CHECKED = "SUPPLY_FLEET_2_SHIP_3_CHECKED"
    SUPPLY_FLEET_2_SHIP_3_UNCHECKED = "SUPPLY_FLEET_2_SHIP_3_UNCHECKED"
    SUPPLY_FLEET_2_SHIP_4_CHECKED = "SUPPLY_FLEET_2_SHIP_4_CHECKED"
    SUPPLY_FLEET_2_SHIP_4_UNCHECKED = "SUPPLY_FLEET_2_SHIP_4_UNCHECKED"
    SUPPLY_FLEET_3_SHIP_1_CHECKED = "SUPPLY_FLEET_3_SHIP_1_CHECKED"
    SUPPLY_FLEET_3_SHIP_1_UNCHECKED = "SUPPLY_FLEET_3_SHIP_1_UNCHECKED"
    SUPPLY_FLEET_3_SHIP_2_CHECKED = "SUPPLY_FLEET_3_SHIP_2_CHECKED"
    SUPPLY_FLEET_3_SHIP_2_UNCHECKED = "SUPPLY_FLEET_3_SHIP_2_UNCHECKED"
    SUPPLY_FLEET_3_SHIP_3_CHECKED = "SUPPLY_FLEET_3_SHIP_3_CHECKED"
    SUPPLY_FLEET_3_SHIP_3_UNCHECKED = "SUPPLY_FLEET_3_SHIP_3_UNCHECKED"
    SUPPLY_FLEET_3_SHIP_4_CHECKED = "SUPPLY_FLEET_3_SHIP_4_CHECKED"
    SUPPLY_FLEET_3_SHIP_4_UNCHECKED = "SUPPLY_FLEET_3_SHIP_4_UNCHECKED"
    MISSION_SELECTED = "MISSION_SELECTED"
    FLEET_3_SELECTED = "FLEET_3_SELECTED"
    FLEET_2_SELECTED = "FLEET_2_SELECTED"
    # Fleet 4 has a dispatch tab like Fleets 2 and 3, so the post-dispatch
    # checkpoint enumerates FLEET_4_SELECTED as a candidate page. It has no
    # fingerprint template and no accessible marker yet, which is fine: the
    # state simply never matches and the caller refuses to click. Leaving it
    # out of the enum instead turned that refusal into a ValueError that
    # bypassed the caller's "not this screen" handling (live 2026-11-05).
    FLEET_4_SELECTED = "FLEET_4_SELECTED"
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
    UiState.HOME_PORT_RETURN_NOTICE: ("遠征から戻ってきました", "returned from expedition"),
    UiState.RETURN_RESULT: ("遠征 失敗", "遠征 成功", "expedition failed", "expedition success"),
    UiState.BONUS_ITEMS: ("獲得資源", "獲得アイテム", "獲得ボーナス", "bonus items", "reward items"),
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
    has_home = any(
        marker.casefold() in text
        for marker in _STATE_MARKERS[UiState.HOME_PORT]
        for text in texts
    )
    has_return_notice = any(
        marker.casefold() in text
        for marker in _STATE_MARKERS[UiState.HOME_PORT_RETURN_NOTICE]
        for text in texts
    )
    if has_home and has_return_notice:
        return UiState.HOME_PORT_RETURN_NOTICE
    matched = [
        state for state, markers in _STATE_MARKERS.items()
        if state is not UiState.HOME_PORT_RETURN_NOTICE
        and any(marker.casefold() in text for marker in markers for text in texts)
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
