"""Run the passive CDP observer and receive its current witness in memory.

The observer waits for a naturally occurring port response. It does not
refresh the page, send game input, or initiate a game API request. The witness
is delivered over this child process's captured stdout rather than re-read
from the persistent snapshot file.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
from pathlib import Path
from typing import Any


_WITNESS_PREFIX = "[PortObserver] WITNESS_JSON:"
_STATUS_PREFIX = "[PortObserver] STATUS:"
_WITNESS_ENV = "KCB_POI_FRESHNESS_WITNESS_STDOUT"
_DEFAULT_TIMEOUT_SECONDS = 130.0
_MAX_STDOUT_CHARS = 1_000_000
_MAX_WITNESS_CHARS = 500_000


class PassivePortResponseObserver:
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

    def capture(self) -> dict[str, Any] | None:
        """Wait for one natural response; return its compact witness or None."""
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
        self.last_status = "captured"
        return witness
