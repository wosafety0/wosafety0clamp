from __future__ import annotations

import unittest

from kancollebot.adapters.poi.ui_state_gate import (
    UiState,
    UiStateGateError,
    classify_accessible_state,
    has_unique_cancel_control,
    require_state,
)


def node(name: str, role: str = "text", *, ignored: bool = False) -> dict:
    return {"name": {"value": name}, "role": {"value": role}, "ignored": ignored}


class UiStateGateTests(unittest.TestCase):
    def test_classifies_only_explicit_unique_state_markers(self):
        self.assertIs(classify_accessible_state([node("母港")]), UiState.HOME_PORT)
        self.assertIs(classify_accessible_state([node("遠征 失敗！")]), UiState.RETURN_RESULT)
        self.assertIs(classify_accessible_state([node("獲得アイテム")]), UiState.BONUS_ITEMS)

    def test_ambiguous_or_missing_evidence_is_unknown(self):
        self.assertIs(classify_accessible_state([]), UiState.UNKNOWN)
        self.assertIs(classify_accessible_state([node("次")]), UiState.UNKNOWN)
        self.assertIs(classify_accessible_state([node("母港"), node("遠征 失敗")]), UiState.UNKNOWN)
        self.assertIs(classify_accessible_state([node("母港", ignored=True)]), UiState.UNKNOWN)

    def test_require_state_fails_closed(self):
        with self.assertRaisesRegex(UiStateGateError, "refusing input"):
            require_state([node("次")], UiState.BONUS_ITEMS)
        require_state([node("母港")], UiState.HOME_PORT)

    def test_cancel_requires_error_dialog_and_one_button(self):
        good = [node("エラーが発生したため、ページ更新します。"), node("取消", "button")]
        self.assertTrue(has_unique_cancel_control(good))
        with self.assertRaises(UiStateGateError):
            require_state(good, UiState.HOME_PORT)
        self.assertFalse(has_unique_cancel_control([node("母港"), node("取消", "button")]))
        self.assertFalse(has_unique_cancel_control([node("エラーが発生したため"), node("取消", "button"), node("Cancel", "button")]))
        self.assertFalse(has_unique_cancel_control([node("エラーが発生したため"), node("取消", "text")]))

    def test_malformed_tree_is_rejected(self):
        with self.assertRaises(ValueError):
            classify_accessible_state({"nodes": []})


if __name__ == "__main__":
    unittest.main()
