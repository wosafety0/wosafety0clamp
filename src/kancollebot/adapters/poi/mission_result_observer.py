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
_DEFAULT_TIMEOUT_SECONDS = 130.0
_MAX_STDOUT_CHARS = 1_000_000
_MAX_WITNESS_CHARS = 500_000

#: The only fields of the witness this wrapper reads. Everything else in the
#: child's output is ignored, so an unexpected field cannot reach the caller.
_ALLOWED_DATA_FIELDS = ("apiDeckId", "apiQuestName", "apiClearResult", "apiShipId")
_ALLOWED_SOURCE_FIELDS = (
    "kind", "path", "host", "httpStatus", "requestedAt", "responseReceivedAt",
)


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

        env = os.environ.copy()
        env[_WITNESS_ENV] = "1"
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

        env = os.environ.copy()
        env[_WITNESS_ENV] = "1"
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
        return {
            "source": {
                key: source[key]
                for key in _ALLOWED_SOURCE_FIELDS
                if key in source
            },
            "data": {
                "apiDeckId": deck_id,
                "apiQuestName": quest_name.strip(),
                "apiClearResult": clear_result,
                "apiShipId": cleaned,
            },
        }
