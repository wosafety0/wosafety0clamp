"""Run the passive expedition-result observer and receive its witness.

The observer waits for the exchange the game client makes on its own when a
fleet's expedition result is settled. It does not refresh the page, send game
input, or initiate a game API request. The witness is delivered over this
child process's captured stdout.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any


_WITNESS_PREFIX = "[MissionResultObserver] WITNESS_JSON:"
_STATUS_PREFIX = "[MissionResultObserver] STATUS:"
_DETAIL_PREFIX = "[MissionResultObserver] DETAIL:"
_WITNESS_ENV = "KCB_MISSION_RESULT_WITNESS_STDOUT"
#: The child's armed window. It must not outlive the caller, and the caller
#: must not be surprised by an earlier exit, so the child is told the same
#: timeout the wrapper enforces.
_TIMEOUT_ENV = "KCB_MISSION_RESULT_TIMEOUT_MS"
_DEFAULT_TIMEOUT_SECONDS = 130.0
_MAX_STDOUT_CHARS = 1_000_000
_MAX_WITNESS_CHARS = 500_000

#: The only fields of the witness this wrapper reads. Everything else in the
#: child's output is ignored, so an unexpected field cannot reach the caller.
_ALLOWED_DATA_FIELDS = ("apiDeckId", "apiQuestName", "apiClearResult", "apiShipId")
_ALLOWED_SOURCE_FIELDS = (
    "kind", "path", "host", "httpStatus", "requestedAt", "responseReceivedAt",
)


def child_environment(timeout_seconds: float) -> dict[str, str]:
    """The environment the observer child runs with.

    The child is told the caller's timeout so it cannot outlive the caller,
    and it is told to write its witness to stdout so the in-memory bridge
    creates no files.
    """
    env = os.environ.copy()
    env[_WITNESS_ENV] = "1"
    env[_TIMEOUT_ENV] = str(int(timeout_seconds * 1000))
    return env


def _nonnegative_int_list(value: object, lengths: tuple[int, ...]) -> list[int] | None:
    if not isinstance(value, list) or len(value) not in lengths:
        return None
    cleaned: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            return None
        cleaned.append(item)
    return cleaned


def _normalize_reward_receipt(value: object) -> dict[str, Any]:
    """Keep only the minimal reward receipt; malformed details stay incomplete."""
    if not isinstance(value, dict):
        return {
            "status": "incomplete",
            "material": None,
            "useitemFlags": None,
            "items": [],
        }

    status = value.get("status")
    if status not in {"complete", "incomplete", "no_reward", "unknown"}:
        status = "incomplete"

    material = _nonnegative_int_list(value.get("material"), (4, 8))
    flags = _nonnegative_int_list(value.get("useitemFlags"), (2,))
    raw_items = value.get("items")
    items: list[dict[str, int]] = []
    invalid_item = not isinstance(raw_items, list)
    seen_slots: set[int] = set()
    if isinstance(raw_items, list):
        for item in raw_items:
            if not isinstance(item, dict):
                invalid_item = True
                continue
            slot = item.get("slot")
            item_id = item.get("useitemId")
            count = item.get("useitemCount")
            if (
                isinstance(slot, bool) or not isinstance(slot, int) or slot not in (1, 2)
                or isinstance(item_id, bool) or not isinstance(item_id, int) or item_id <= 0
                or isinstance(count, bool) or not isinstance(count, int) or count <= 0
                or slot in seen_slots
            ):
                invalid_item = True
                continue
            seen_slots.add(slot)
            items.append({
                "slot": slot,
                "useitemId": item_id,
                "useitemCount": count,
            })

    if status == "complete":
        if material is None or flags is None or invalid_item:
            status = "incomplete"
        elif any(flag > 0 and slot not in seen_slots
                 for slot, flag in enumerate(flags, start=1)):
            status = "incomplete"

    return {
        "status": status,
        "material": material,
        "useitemFlags": flags,
        "items": items,
    }


class PassiveMissionResultObserver:
    """One-shot passive observer wrapper; no observer is started by default."""

    def __init__(
        self,
        script_path: str | Path,
        *,
        node_executable: str = "node",
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not node_executable.strip():
            raise ValueError("node_executable must not be empty")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        self.script_path = Path(script_path).expanduser()
        self.node_executable = node_executable
        self.timeout_seconds = timeout_seconds
        self.last_status = "not_started"
        self.last_detail: dict[str, Any] | None = None

    def capture(self) -> dict[str, Any] | None:
        """Wait for one natural result exchange; return its witness or None."""
        self.last_detail = None
        script = self.script_path.resolve()
        if not script.is_file():
            self.last_status = "script_missing"
            return None

        env = child_environment(self.timeout_seconds)
        command = [
            self.node_executable,
            "-e",
            "require(process.argv[1])()",
            str(script),
        ]
        try:
            result = subprocess.run(
                command,
                cwd=str(script.parent),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            self.last_status = "process_timeout"
            return None
        except OSError:
            self.last_status = "launch_failed"
            return None
        return self._consume_result(result)

    def capture_cancellable(
        self, cancel_event: threading.Event
    ) -> dict[str, Any] | None:
        """Run the observer with a cancellation signal for background owners."""
        self.last_detail = None
        if cancel_event.is_set():
            self.last_status = "cancelled"
            return None
        script = self.script_path.resolve()
        if not script.is_file():
            self.last_status = "script_missing"
            return None

        env = child_environment(self.timeout_seconds)
        command = [
            self.node_executable,
            "-e",
            "require(process.argv[1])()",
            str(script),
        ]
        try:
            process = subprocess.Popen(
                command,
                cwd=str(script.parent),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError:
            self.last_status = "launch_failed"
            return None

        deadline = time.monotonic() + self.timeout_seconds
        while True:
            if cancel_event.is_set():
                process.terminate()
                try:
                    process.communicate(timeout=1.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                self.last_status = "cancelled"
                return None

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                process.communicate()
                self.last_status = "process_timeout"
                return None
            try:
                stdout, stderr = process.communicate(timeout=min(0.1, remaining))
                result = subprocess.CompletedProcess(
                    command, process.returncode, stdout, stderr
                )
                return self._consume_result(result)
            except subprocess.TimeoutExpired:
                continue

    def _consume_result(
        self, result: subprocess.CompletedProcess[str]
    ) -> dict[str, Any] | None:
        if len(result.stdout) > _MAX_STDOUT_CHARS:
            self.last_status = "output_too_large"
            return None
        if result.returncode != 0:
            self.last_status = "observer_failed"
            return None

        status_lines = [
            line[len(_STATUS_PREFIX):].strip()
            for line in result.stdout.splitlines()
            if line.startswith(_STATUS_PREFIX)
        ]
        if len(status_lines) > 1:
            self.last_status = "invalid_output"
            return None
        reported_status = status_lines[0] if status_lines else None
        if reported_status == "TIMEOUT":
            detail_lines = [
                line[len(_DETAIL_PREFIX):].strip()
                for line in result.stdout.splitlines()
                if line.startswith(_DETAIL_PREFIX)
            ]
            if len(detail_lines) == 1 and len(detail_lines[0]) <= 4096:
                try:
                    detail = json.loads(detail_lines[0])
                except json.JSONDecodeError:
                    detail = None
                if isinstance(detail, dict):
                    self.last_detail = detail

        lines = [
            line[len(_WITNESS_PREFIX):]
            for line in result.stdout.splitlines()
            if line.startswith(_WITNESS_PREFIX)
        ]
        if not lines:
            status_map = {
                "TIMEOUT": "natural_response_timeout",
                "TARGET_DISCOVERY_FAILED": "target_discovery_failed",
                "CDP_CONNECTION_FAILED": "cdp_connection_failed",
                "CDP_CONNECTION_CLOSED": "cdp_connection_closed",
                "CDP_PROTOCOL_ERROR": "cdp_protocol_error",
                "CDP_COMMAND_FAILED": "cdp_command_failed",
                "REJECTED": "response_rejected",
            }
            self.last_status = status_map.get(reported_status, "no_capture")
            return None
        if len(lines) != 1 or len(lines[0]) > _MAX_WITNESS_CHARS:
            self.last_status = "invalid_output"
            return None
        try:
            witness = json.loads(lines[0])
        except json.JSONDecodeError:
            self.last_status = "invalid_json"
            return None
        if not isinstance(witness, dict):
            self.last_status = "invalid_shape"
            return None
        if reported_status != "CAPTURED":
            self.last_status = "invalid_output"
            return None

        normalized = self._normalize(witness)
        if normalized is None:
            self.last_status = "invalid_shape"
            return None
        self.last_status = "captured"
        return normalized

    @staticmethod
    def _normalize(witness: dict[str, Any]) -> dict[str, Any] | None:
        """Keep only the fields this project is allowed to use."""
        data = witness.get("data")
        source = witness.get("source")
        if not isinstance(data, dict) or not isinstance(source, dict):
            return None
        if any(key not in data for key in _ALLOWED_DATA_FIELDS):
            return None
        deck_id = data.get("apiDeckId")
        quest_name = data.get("apiQuestName")
        clear_result = data.get("apiClearResult")
        ship_ids = data.get("apiShipId")
        if isinstance(deck_id, bool) or not isinstance(deck_id, int) or deck_id < 1:
            return None
        if not isinstance(quest_name, str) or not quest_name.strip():
            return None
        if isinstance(clear_result, bool) or not isinstance(clear_result, int):
            return None
        if not isinstance(ship_ids, list) or not ship_ids:
            return None
        cleaned: list[int] = []
        for value in ship_ids:
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                continue
            if value not in cleaned:
                cleaned.append(value)
        if not cleaned:
            return None
        normalized_data = {
            "apiDeckId": deck_id,
            "apiQuestName": quest_name.strip(),
            "apiClearResult": clear_result,
            "apiShipId": cleaned,
        }
        if "apiRewardReceipt" in data:
            normalized_data["apiRewardReceipt"] = _normalize_reward_receipt(
                data.get("apiRewardReceipt")
            )
        return {
            "source": {
                key: source[key]
                for key in _ALLOWED_SOURCE_FIELDS
                if key in source
            },
            "data": normalized_data,
        }
