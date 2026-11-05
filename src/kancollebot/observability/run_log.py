"""Three-day rolling JSONL run log: append, rotate by day, cap, prune, snapshot.

Design constraints, in the order they win when they conflict:

1. **Never lose a safety decision.** A prune never touches anything that is not
   a run-log part file inside the configured directory. The action journal, the
   dispatch SQLite databases, verification evidence and research reports are
   not run logs and must survive every cleanup.
2. **Never claim a write that did not happen.** ``append`` either wrote the line
   or raised. ``write_snapshot`` either replaced the snapshot or raised; a
   half-written temporary file is removed, never promoted.
3. **A broken line must not kill the file.** Every event is one self-contained
   JSON object, so a truncated tail costs one event, not the run.
4. **Bounded disk use.** One file never exceeds ``max_file_bytes`` (a new part is
   opened instead) and the directory never keeps more than
   ``max_total_bytes`` of run-log parts (oldest parts go first).

Directory layout::

    logs/run/run-2026-11-06.jsonl          # current part of the day
    logs/run/run-2026-11-06.001.jsonl      # next part of the same day
    logs/run/current_state.json            # atomically replaced snapshot

Only files matching ``run-YYYY-MM-DD[.NNN].jsonl`` are ever rotated or deleted.
Everything else in the directory - a verification report, a copied database, a
hand-written note - is invisible to cleanup by construction, not by a deny list.
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_MAX_FILE_BYTES",
    "DEFAULT_MAX_TOTAL_BYTES",
    "DEFAULT_RETENTION_DAYS",
    "PruneReport",
    "RunLogError",
    "RunLogFile",
    "RunLogPathError",
    "RunLogPolicy",
    "RunLogStore",
    "SnapshotWriteError",
]

DEFAULT_RETENTION_DAYS = 3
DEFAULT_MAX_FILE_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 64 * 1024 * 1024
DEFAULT_SNAPSHOT_NAME = "current_state.json"

_NAME_PATTERN = re.compile(
    r"^run-(?P<day>\d{4}-\d{2}-\d{2})(?:\.(?P<part>\d{3}))?\.jsonl$"
)
_PART_DIGITS = 3


class RunLogError(RuntimeError):
    """A run-log operation could not be completed."""


class RunLogPathError(RunLogError):
    """A path the log was asked to touch is outside its own directory."""


class SnapshotWriteError(RunLogError):
    """The current-state snapshot could not be replaced atomically."""


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


@dataclass(frozen=True, slots=True)
class RunLogPolicy:
    """Central capacity/retention policy for the run log.

    The defaults are deliberately small: this log exists to reconstruct a run,
    not to store telemetry for months. A single 8 MiB part holds tens of
    thousands of events, which is far more than one expedition run produces.
    """

    directory: str | Path
    retention_days: int = DEFAULT_RETENTION_DAYS
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES
    snapshot_name: str = DEFAULT_SNAPSHOT_NAME

    def __post_init__(self) -> None:
        if isinstance(self.retention_days, bool) or not isinstance(self.retention_days, int):
            raise ValueError("retention_days must be an int")
        if self.retention_days < 1:
            raise ValueError("retention_days must be at least 1")
        for name in ("max_file_bytes", "max_total_bytes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an int")
            if value < 1024:
                raise ValueError(f"{name} must be at least 1024 bytes")
        if self.max_total_bytes < self.max_file_bytes:
            raise ValueError("max_total_bytes must be at least max_file_bytes")
        name = self.snapshot_name
        if not isinstance(name, str) or not name:
            raise ValueError("snapshot_name must be a non-empty string")
        if any(separator in name for separator in ("/", "\\")) or name in (".", ".."):
            raise ValueError("snapshot_name must be a plain file name")
        if not name.endswith(".json"):
            raise ValueError("snapshot_name must end with .json")
        if not str(self.directory):
            raise ValueError("directory must not be empty")

    @property
    def path(self) -> Path:
        return Path(self.directory).expanduser()


@dataclass(frozen=True, slots=True)
class RunLogFile:
    """One run-log part file on disk."""

    path: Path
    day: date
    part: int
    size_bytes: int


@dataclass(slots=True)
class PruneReport:
    """What one cleanup pass actually did, including what it refused to do."""

    removed: list[str] = field(default_factory=list)
    kept: int = 0
    total_bytes: int = 0
    errors: list[str] = field(default_factory=list)
    ran_at_utc: str = ""

    @property
    def ok(self) -> bool:
        return not self.errors


class RunLogStore:
    """Append-only JSONL storage with rotation, retention and a snapshot.

    The store is intentionally dumb about *what* an event is; the envelope is
    built by :mod:`kancollebot.observability.event_envelope` and the failure
    semantics live in :mod:`kancollebot.observability.sink`. Keeping those
    apart is what lets the retention rules be tested without a sink and the
    sink be tested without real files.
    """

    def __init__(
        self,
        policy: RunLogPolicy,
        *,
        today: Any = None,
    ) -> None:
        if not isinstance(policy, RunLogPolicy):
            raise ValueError("policy must be a RunLogPolicy")
        self.policy = policy
        self._today = today if callable(today) else _utc_today
        self._last_written: Path | None = None

    # -- paths -------------------------------------------------------------

    @property
    def directory(self) -> Path:
        return self.policy.path

    @property
    def snapshot_path(self) -> Path:
        return self.directory / self.policy.snapshot_name

    def _assert_inside(self, path: Path) -> Path:
        """Resolve ``path`` and refuse anything that escapes the log directory.

        ``resolve()`` follows symlinks, so a symlink planted inside the log
        directory that points at the action journal (or at ``C:\\``) resolves
        outside and is rejected *before* any unlink happens.
        """
        root = self.directory.resolve()
        try:
            resolved = path.resolve()
        except OSError as exc:  # pragma: no cover - platform dependent
            raise RunLogPathError(f"cannot resolve {path}: {exc}") from exc
        if resolved == root or not resolved.is_relative_to(root):
            raise RunLogPathError(
                f"refusing to touch {resolved}: outside run-log directory {root}"
            )
        return resolved

    def log_files(self) -> tuple[RunLogFile, ...]:
        """Every run-log part file in the directory, oldest first."""
        directory = self.directory
        if not directory.is_dir():
            return ()
        files: list[RunLogFile] = []
        for entry in directory.iterdir():
            match = _NAME_PATTERN.match(entry.name)
            if match is None or not entry.is_file():
                continue
            try:
                day = date.fromisoformat(match.group("day"))
                size = entry.stat().st_size
            except (OSError, ValueError):
                continue
            part = int(match.group("part") or 0)
            files.append(RunLogFile(path=entry, day=day, part=part, size_bytes=size))
        files.sort(key=lambda item: (item.day, item.part))
        return tuple(files)

    def _part_path(self, day: date, part: int) -> Path:
        if part == 0:
            name = f"run-{day.isoformat()}.jsonl"
        else:
            name = f"run-{day.isoformat()}.{part:0{_PART_DIGITS}d}.jsonl"
        return self.directory / name

    def _target_for(self, day: date, payload_bytes: int) -> Path:
        """Pick the part file this payload belongs in, rotating if needed.

        Rotation happens *before* the write, so a single event can never end up
        split across two files: a record either fits in the current part or the
        next part starts with it.
        """
        same_day = [item for item in self.log_files() if item.day == day]
        if not same_day:
            return self._part_path(day, 0)
        current = same_day[-1]
        if current.size_bytes <= 0 or current.size_bytes + payload_bytes <= self.policy.max_file_bytes:
            return current.path
        next_part = current.part + 1
        if next_part >= 10**_PART_DIGITS:  # pragma: no cover - 1000 * 8 MiB per day
            raise RunLogError(
                f"run log for {day.isoformat()} exhausted {10**_PART_DIGITS} parts"
            )
        return self._part_path(day, next_part)

    # -- writes ------------------------------------------------------------

    @staticmethod
    def _serialise(record: Mapping[str, Any]) -> bytes:
        line = json.dumps(
            dict(record),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            default=str,
        )
        if "\n" in line or "\r" in line:  # pragma: no cover - json escapes these
            raise RunLogError("serialised event contains a raw newline")
        return (line + "\n").encode("utf-8")

    def append(self, record: Mapping[str, Any], *, durable: bool = False) -> Path:
        """Append one event. Returns the file it landed in, or raises."""
        if not isinstance(record, Mapping):
            raise TypeError("record must be a mapping")
        data = self._serialise(record)
        day = self._today()
        if not isinstance(day, date):  # pragma: no cover - defensive
            raise RunLogError("today() must return a date")
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self._assert_inside(self._target_for(day, len(data)))
        with open(target, "ab") as stream:
            stream.write(data)
            stream.flush()
            if durable:
                os.fsync(stream.fileno())
        self._last_written = target
        return target

    def probe(self) -> Path:
        """Prove the current part is writable without writing an event."""
        day = self._today()
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self._assert_inside(self._target_for(day, 1))
        with open(target, "ab") as stream:
            stream.flush()
            stream.close()
        return target

    # -- retention ---------------------------------------------------------

    def prune(self, *, now: date | None = None) -> PruneReport:
        """Delete run-log parts older than the retention window, then cap size.

        Never raises: a cleanup problem is reported and the caller decides.
        A failed prune must not be able to break the safety databases, so this
        method touches nothing but ``run-*.jsonl`` part files.
        """
        report = PruneReport(ran_at_utc=datetime.now(timezone.utc).isoformat())
        try:
            today = now if now is not None else self._today()
            cutoff = today - timedelta(days=self.policy.retention_days - 1)
        except Exception as exc:  # pragma: no cover - defensive
            report.errors.append(f"{type(exc).__name__}: {exc}")
            return report

        try:
            files = list(self.log_files())
        except OSError as exc:
            report.errors.append(f"list failed: {type(exc).__name__}: {exc}")
            return report

        survivors: list[RunLogFile] = []
        for item in files:
            if item.day < cutoff:
                if self._unlink(item.path, report):
                    continue
            survivors.append(item)

        total = sum(item.size_bytes for item in survivors)
        for item in survivors:
            if total <= self.policy.max_total_bytes:
                break
            if self._last_written is not None and item.path == self._last_written:
                # The part being appended to is never the sacrifice: deleting it
                # would lose the event that is about to be written.
                continue
            if self._unlink(item.path, report):
                total -= item.size_bytes

        report.kept = len([item for item in survivors if item.path.exists()])
        report.total_bytes = max(total, 0)
        return report

    def _unlink(self, path: Path, report: PruneReport) -> bool:
        try:
            resolved = self._assert_inside(path)
        except RunLogPathError as exc:
            report.errors.append(str(exc))
            return False
        match = _NAME_PATTERN.match(resolved.name)
        if match is None:
            report.errors.append(f"refusing to delete non-log file {resolved}")
            return False
        try:
            resolved.unlink()
        except FileNotFoundError:
            return True
        except OSError as exc:
            report.errors.append(f"unlink failed for {resolved.name}: {exc}")
            return False
        report.removed.append(resolved.name)
        return True

    # -- snapshot ----------------------------------------------------------

    def write_snapshot(self, payload: Mapping[str, Any]) -> Path:
        """Atomically replace the current-state snapshot.

        ``os.replace`` is atomic on the same filesystem, so a reader either sees
        the previous snapshot or the new one - never a half-written file. A
        failure removes the temporary file and raises; the previous snapshot
        stays intact and the caller must not report success.
        """
        if not isinstance(payload, Mapping):
            raise TypeError("snapshot payload must be a mapping")
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self._assert_inside(self.snapshot_path)
        temporary = target.with_name(target.name + ".tmp")
        data = json.dumps(
            dict(payload),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=2,
            default=str,
        ).encode("utf-8")
        try:
            with open(temporary, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise SnapshotWriteError(
                f"cannot replace snapshot {target}: {type(exc).__name__}: {exc}"
            ) from exc
        return target

    def read_snapshot(self) -> dict[str, Any] | None:
        try:
            text = self.snapshot_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        return json.loads(text)

    # -- reads -------------------------------------------------------------

    def read_events(
        self,
        *,
        run_id: str | None = None,
        include_corrupt: bool = False,
    ) -> tuple[dict[str, Any], ...]:
        """Read back every event, oldest first.

        A corrupt or truncated line is skipped (and optionally reported) rather
        than aborting the read: that is the whole point of one JSON object per
        line.
        """
        events: list[dict[str, Any]] = []
        corrupt: list[str] = []
        for item in self.log_files():
            try:
                text = item.path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line in text.splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    corrupt.append(line[:120])
                    continue
                if not isinstance(record, dict):
                    corrupt.append(line[:120])
                    continue
                if run_id is not None and record.get("run_id") != run_id:
                    continue
                events.append(record)
        if include_corrupt:
            events.append({"event": "_corrupt_lines", "count": len(corrupt), "samples": corrupt})
        return tuple(events)


def summarise_events(events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Cheap lifecycle summary used by tests and by a post-run report."""
    runs: dict[str, int] = {}
    dispatches: dict[str, int] = {}
    first: str | None = None
    last: str | None = None
    for record in events:
        run_id = record.get("run_id")
        if isinstance(run_id, str):
            runs[run_id] = runs.get(run_id, 0) + 1
        dispatch_id = record.get("dispatch_id")
        if isinstance(dispatch_id, str):
            dispatches[dispatch_id] = dispatches.get(dispatch_id, 0) + 1
        stamp = record.get("timestamp_utc")
        if isinstance(stamp, str):
            first = stamp if first is None or stamp < first else first
            last = stamp if last is None or stamp > last else last
    return {
        "runs": runs,
        "dispatches": dispatches,
        "first_timestamp_utc": first,
        "last_timestamp_utc": last,
    }
