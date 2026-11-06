"""Read-only inspection of accessible UI labels in the selected local renderer."""
from __future__ import annotations

import argparse
import json
from typing import Any

from kancollebot.adapters.poi.cdp_websocket import connect_poi_session


DEFAULT_KEYWORDS = (
    "expedition", "mission", "reward", "result", "return", "dispatch",
    "遠征", "任務", "報酬", "結果", "帰投", "出撃",
    "远征", "任务", "奖励", "结果", "返回",
)
MAX_CANDIDATES = 40


def matching_accessible_nodes(
    nodes: Any, *, keywords: tuple[str, ...] = DEFAULT_KEYWORDS
) -> tuple[dict[str, str], ...]:
    """Return bounded role/name pairs matching known expedition-related terms."""
    if not isinstance(nodes, list):
        raise ValueError("Accessibility tree nodes must be a list")
    normalized = tuple(word.casefold() for word in keywords if word)
    found = []
    seen = set()
    for node in nodes:
        if not isinstance(node, dict) or node.get("ignored") is True:
            continue
        role_value = node.get("role")
        name_value = node.get("name")
        role = role_value.get("value") if isinstance(role_value, dict) else None
        name = name_value.get("value") if isinstance(name_value, dict) else None
        if not isinstance(role, str) or not isinstance(name, str) or not name.strip():
            continue
        if normalized and not any(word in name.casefold() for word in normalized):
            continue
        key = (role, name)
        if key in seen:
            continue
        seen.add(key)
        found.append({"role": role, "name": name})
        if len(found) >= MAX_CANDIDATES:
            break
    return tuple(found)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://127.0.0.1:9222")
    parser.add_argument("--title-hint", default="poi")
    parser.add_argument("--url-hint", default="")
    args = parser.parse_args()

    try:
        with connect_poi_session(
            args.endpoint,
            title_hint=args.title_hint,
            url_hint=args.url_hint,
        ) as session:
            result = session.transport.send("Accessibility.getFullAXTree", {})
        nodes = result.get("nodes") if isinstance(result, dict) else None
        candidates = matching_accessible_nodes(nodes)
        report = {
            "status": "PASS_READ_ONLY_SCAN" if candidates else "NO_MATCHING_ACCESSIBLE_TARGETS",
            "candidate_count": len(candidates),
            "candidates": list(candidates),
            "candidates_truncated_at": MAX_CANDIDATES,
            "input_sent": False,
            "javascript_evaluation_sent": False,
            "game_action_sent": False,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if candidates else 1
    except Exception as exc:
        print(json.dumps({
            "status": "FAILED",
            "error_type": type(exc).__name__,
            "input_sent": False,
            "javascript_evaluation_sent": False,
            "game_action_sent": False,
        }, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
