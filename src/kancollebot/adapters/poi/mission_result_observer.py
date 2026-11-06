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
    if not isinstance(status, str) or status not in {"complete", "incomplete", "no_reward", "unknown"}:
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


class MissionResultObserverError(RuntimeError):
    """The passive result observer could not be armed safely."""


class MissionResultObserverSession:
    """One armed child process whose lifetime is owned by one settlement."""

    _ARMED_MARKER = (
        "Network observer armed; waiting for a natural expedition result response."
    )

    def __init__(self, observer: "PassiveMissionResultObserver", process: subprocess.Popen[str]):
        self._observer = observer
        self._process = process
        self._command = list(process.args) if isinstance(process.args, (list, tuple)) else [str(process.args)]
        self._stdout: list[str] = []
        self._stderr: list[str] = []
        self._lock = threading.Lock()
        self._armed = threading.Event()
        self._stdout_closed = threading.Event()
        self._consumed = False
        self._stdout_thread = threading.Thread(
            target=self._read_stdout, name="mission-result-observer-stdout", daemon=True
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stderr, name="mission-result-observer-stderr", daemon=True
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    @property
    def is_armed(self) -> bool:
        return self._armed.is_set()

    def wait_until_armed(self, timeout_seconds: float) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("arm timeout must be finite and positive")
        deadline = time.monotonic() + timeout_seconds
        while not self._armed.is_set():
            if self._process.poll() is not None:
                self._finish_readers()
                self._consume_output()
                raise MissionResultObserverError(
                    f"observer exited before arming ({self._observer.last_status})"
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.cancel()
                self._observer.last_status = "arm_timeout"
                raise MissionResultObserverError("observer did not prove Network.enable was armed")
            self._armed.wait(min(0.05, remaining))

    def wait(self, *, timeout_seconds: float | None = None) -> dict[str, Any] | None:
        if self._consumed:
            raise MissionResultObserverError("observer session result was already consumed")
        timeout = self._observer.timeout_seconds if timeout_seconds is None else timeout_seconds
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("result timeout must be finite and positive")
        deadline = time.monotonic() + timeout
        while self._process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.cancel()
                self._observer.last_status = "process_timeout"
                self._consumed = True
                return None
            self._stdout_closed.wait(min(0.05, remaining))
        self._process.wait()
        self._finish_readers()
        self._consumed = True
        return self._consume_output()

    def cancel(self) -> None:
        if self._process.poll() is None:
            try:
                self._process.terminate()
                self._process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
            except OSError:
                pass
        self._finish_readers()
        self._consumed = True
        self._observer.last_status = "cancelled"

    def _read_stdout(self) -> None:
        try:
            stream = self._process.stdout
            if stream is None:
                return
            for line in stream:
                with self._lock:
                    self._stdout.append(line)
                if self._ARMED_MARKER in line:
                    self._armed.set()
        finally:
            self._stdout_closed.set()

    def _read_stderr(self) -> None:
        stream = self._process.stderr
        if stream is None:
            return
        for line in stream:
            with self._lock:
                self._stderr.append(line)

    def _finish_readers(self) -> None:
        self._stdout_closed.wait(1.0)
        for thread in (self._stdout_thread, self._stderr_thread):
            if thread is not threading.current_thread():
                thread.join(timeout=1.0)
        for stream in (self._process.stdout, self._process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    def _consume_output(self) -> dict[str, Any] | None:
        with self._lock:
            stdout = "".join(self._stdout)
            stderr = "".join(self._stderr)
        result = subprocess.CompletedProcess(
            self._command, self._process.returncode, stdout, stderr
        )
        return self._observer._consume_result(result)


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

    def arm(self, *, arm_timeout_seconds: float = 10.0) -> MissionResultObserverSession:
        """Start the passive observer and return only after its Network listener is armed."""
        self.last_detail = None
        script = self.script_path.resolve()
        if not script.is_file():
            self.last_status = "script_missing"
            raise MissionResultObserverError("mission-result observer script is missing")

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
                bufsize=1,
            )
        except OSError as exc:
            self.last_status = "launch_failed"
            raise MissionResultObserverError("mission-result observer failed to launch") from exc

        session = MissionResultObserverSession(self, process)
        try:
            session.wait_until_armed(arm_timeout_seconds)
        except Exception:
            if process.poll() is None:
                session.cancel()
            raise
        self.last_status = "armed"
        return session

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
