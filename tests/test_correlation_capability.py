"""Stage 2: ``run_started`` states whether this run can bind correlation at all.

One observable value - ``dispatch_id: null`` - has at least four different
meanings: the event never belonged to a dispatch, the scope was dropped, the
event came from another thread, or this run's sink cannot correlate. Only the
last one is answerable once, globally, at the top of the log.

The tests below pin that the answer comes from **real capability**:

* a plain function sink cannot correlate;
* an object whose ``correlation`` attribute exists but is ``None`` cannot;
* a run-log sink can;
* a tee can only if at least one member can.
"""
from __future__ import annotations

import unittest
from contextlib import contextmanager

from kancollebot.observability.sink import (
    RunLogSink,
    TeeEventSink,
    build_run_log_sink,
    correlation_capability,
)


def plain_sink(rows: list) -> object:
    def sink(event, fields):
        rows.append((event, dict(fields)))

    return sink


class DeclaresCorrelation:
    def __call__(self, event, fields):  # a tee requires callable members
        pass

    @contextmanager
    def correlation(self, **updates):
        yield


class DeclaresNothing(DeclaresCorrelation):
    #: The attribute exists and is falsy - the exact shape a presence-only
    #: check would get wrong.
    correlation = None


class CapabilityProbeTests(unittest.TestCase):
    def test_a_plain_function_sink_is_not_capable(self):
        capability = correlation_capability(plain_sink([]))
        self.assertFalse(capability.capable)
        self.assertEqual(capability.sources, ())
        self.assertIn("no callable correlation scope", capability.reason)

    def test_an_attribute_that_exists_but_is_none_is_not_capable(self):
        capability = correlation_capability(DeclaresNothing())
        self.assertFalse(capability.capable)

    def test_a_sink_that_really_declares_correlation_is_capable(self):
        capability = correlation_capability(DeclaresCorrelation())
        self.assertTrue(capability.capable)
        self.assertEqual(capability.sources, ("DeclaresCorrelation",))

    def test_a_tee_is_capable_only_when_a_member_is(self):
        rows: list = []
        capable = TeeEventSink(plain_sink(rows), DeclaresCorrelation())
        incapable = TeeEventSink(plain_sink(rows), plain_sink(rows))

        self.assertTrue(correlation_capability(capable).capable)
        self.assertEqual(
            correlation_capability(capable).sources, ("DeclaresCorrelation",)
        )
        self.assertFalse(correlation_capability(incapable).capable)
        self.assertIn("no member sink", correlation_capability(incapable).reason)

    def test_the_fields_are_json_safe(self):
        fields = correlation_capability(plain_sink([])).as_fields()
        self.assertEqual(
            fields, {"correlation_capable": False, "correlation_sources": []}
        )


class RunStartedTests(unittest.TestCase):
    def _sink(self, directory) -> RunLogSink:
        return build_run_log_sink(
            directory=directory, task_kind="expedition", run_id="run-capability"
        )

    def _rows(self, directory):
        import json
        import pathlib

        rows = []
        for path in sorted(pathlib.Path(directory).glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rows.append(json.loads(line))
        return rows

    def test_run_started_records_its_own_capability(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            sink = self._sink(directory)
            sink.start({"database": "x"})
            row = next(r for r in self._rows(directory) if r["event"] == "run_started")
            self.assertIs(row["correlation_capable"], True)
            self.assertEqual(row["correlation_sources"], ["RunLogSink"])
            # The pre-existing lifecycle fields are untouched.
            self.assertEqual(row["run_id"], "run-capability")
            self.assertEqual(row["database"], "x")
            self.assertIn("pid", row)

    def test_the_composition_root_may_describe_the_emitting_sink(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            sink = self._sink(directory)
            # A tee whose only correlation-capable member is this run log: the
            # value the caller passes describes the object components hold.
            sink.start({"correlation_capable": False, "correlation_sources": []})
            row = next(r for r in self._rows(directory) if r["event"] == "run_started")
            self.assertIs(row["correlation_capable"], False)
            self.assertEqual(row["correlation_sources"], [])

    def test_a_capability_field_can_never_be_forged_into_the_envelope(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            sink = self._sink(directory)
            sink.start({"event": "something_else", "run_id": "forged"})
            row = next(r for r in self._rows(directory) if r["event"] == "run_started")
            # Reserved envelope keys still win over caller fields.
            self.assertEqual(row["run_id"], "run-capability")


class RunLogCapabilityTests(unittest.TestCase):
    def test_a_run_log_sink_reports_capable(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            capability = correlation_capability(
                build_run_log_sink(directory=directory, task_kind="expedition")
            )
        self.assertTrue(capability.capable)
        self.assertEqual(capability.sources, ("RunLogSink",))

    def test_the_production_tee_is_capable_because_its_run_log_member_is(self):
        import tempfile

        rows: list = []
        with tempfile.TemporaryDirectory() as directory:
            tee = TeeEventSink(
                plain_sink(rows),
                build_run_log_sink(directory=directory, task_kind="expedition"),
            )
            capability = correlation_capability(tee)
        self.assertTrue(capability.capable)
        # The SQLite member cannot bind anything; only the run log can, and the
        # record names it instead of claiming the tee itself is the source.
        self.assertEqual(capability.sources, ("RunLogSink",))

    def test_an_envelope_rejects_a_foreign_capability_field_name(self):
        # Sanity check on the merge contract: the field lands as a normal
        # diagnostic, so it can never collide with a reserved envelope key.
        from kancollebot.observability.event_envelope import RESERVED_FIELDS

        self.assertNotIn("correlation_capable", RESERVED_FIELDS)
        self.assertNotIn("correlation_sources", RESERVED_FIELDS)


if __name__ == "__main__":
    unittest.main()
