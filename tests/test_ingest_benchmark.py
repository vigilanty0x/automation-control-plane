from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
from pathlib import Path

from apprentice_ai.benchmark import _scan_file_for_canaries, _verified_receipt, run_benchmarks
from apprentice_ai.errors import ValidationError
from apprentice_ai.ingest import ingest_jsonl
from apprentice_ai.store import EventStore
from apprentice_ai.strictjson import loads_bytes
from apprentice_ai.synthetic import canary_receipt


def event(event_id: str, label: str = "Safe label") -> bytes:
    return (
        '{"event_id":"%s","timestamp":"2026-08-16T10:00:00Z",'
        '"application":{"id":"fixture-app"},'
        '"action":{"kind":"task_start","target_role":"button",'
        '"target_label":"%s"},"context":{"synthetic":true}}\n'
        % (event_id, label)
    ).encode()


class IngestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = EventStore(self.root / "apprentice.sqlite")
        self.profile = self.store.create_profile("Import test")

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def test_jsonl_import_is_filtered_and_sealed(self) -> None:
        source = self.root / "events.jsonl"
        source.write_bytes(event("evt_import_001", "api_key=TEST-SECRET-ONLY-12345"))
        report = ingest_jsonl(
            self.store,
            self.profile,
            source,
            metadata={"goal": "fixture_goal", "effect": "fixture_effect", "synthetic": True},
        )
        self.assertEqual(report["events_accepted"], 1)
        self.assertTrue(self.store.verify_chain(self.profile, report["session_id"])["sealed"])
        raw = b"".join(path.read_bytes() for path in self.root.glob("apprentice.sqlite*"))
        self.assertNotIn(b"TEST-SECRET-ONLY-12345", raw)

    def test_invalid_line_marks_session_incomplete(self) -> None:
        source = self.root / "bad.jsonl"
        source.write_bytes(event("evt_import_002") + b"{not-json}\n")
        with self.assertRaises(ValidationError):
            ingest_jsonl(self.store, self.profile, source)
        sessions = self.store.list_sessions(self.profile)
        self.assertEqual(sessions[0]["status"], "incomplete")
        self.assertTrue(self.store.verify_chain(self.profile, sessions[0]["session_id"])["sealed"])

    def test_empty_import_is_rejected_and_closed(self) -> None:
        source = self.root / "empty.jsonl"
        source.write_bytes(b"")
        with self.assertRaises(ValidationError):
            ingest_jsonl(self.store, self.profile, source)
        self.assertEqual(self.store.list_sessions(self.profile)[0]["status"], "incomplete")

    def test_stream_growth_is_bounded_by_actual_bytes_read(self) -> None:
        source = self.root / "grown.jsonl"
        first = event("evt_import_003")
        source.write_bytes(first + event("evt_import_004"))
        opened = os.stat(source)
        stable_open_snapshot = SimpleNamespace(st_mode=opened.st_mode, st_size=len(first))
        with mock.patch("apprentice_ai.ingest._open_fstat", return_value=stable_open_snapshot), mock.patch(
            "apprentice_ai.ingest.MAX_JSONL_BYTES", len(first) + 2
        ):
            with self.assertRaisesRegex(ValidationError, "stream exceeds"):
                ingest_jsonl(self.store, self.profile, source)
        self.assertEqual(self.store.list_sessions(self.profile)[0]["status"], "incomplete")

    def test_symlink_source_is_rejected_before_session_creation(self) -> None:
        target = self.root / "target.jsonl"
        target.write_bytes(event("evt_import_005"))
        link = self.root / "link.jsonl"
        try:
            link.symlink_to(target)
        except OSError:
            self.skipTest("symlinks are unavailable")
        with self.assertRaises(ValidationError):
            ingest_jsonl(self.store, self.profile, link)
        self.assertEqual(self.store.list_sessions(self.profile), [])

    def test_duplicate_event_id_is_stable_error_and_session_is_sealed_incomplete(self) -> None:
        source = self.root / "duplicate.jsonl"
        source.write_bytes(event("evt_duplicate_001") + event("evt_duplicate_001"))
        with self.assertRaisesRegex(Exception, "integrity constraint") as context:
            ingest_jsonl(self.store, self.profile, source)
        self.assertEqual(context.exception.code, "STORE_CONFLICT")
        session = self.store.list_sessions(self.profile)[0]
        self.assertEqual(session["status"], "incomplete")
        self.assertTrue(self.store.verify_chain(self.profile, session["session_id"])["sealed"])


class StreamingScanTests(unittest.TestCase):
    def test_extreme_json_nesting_is_a_stable_validation_error(self) -> None:
        payload = (b"[" * 10_000) + (b"]" * 10_000)
        with self.assertRaisesRegex(ValidationError, "nesting limit"):
            loads_bytes(payload)

    def test_nonfinite_and_huge_json_numbers_are_stable_validation_errors(self) -> None:
        for payload in (b"1e999", b"-1e999", b"9" * 5_000):
            with self.subTest(prefix=payload[:8]):
                with self.assertRaises(ValidationError):
                    loads_bytes(payload)

    def test_scanner_finds_token_across_chunk_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "db"
            path.write_bytes(b"1234567" + b"SECRET-CANARY" + b"tail")
            found, oversized = _scan_file_for_canaries(
                path, (b"SECRET-CANARY",), max_file_bytes=100, chunk_bytes=8
            )
            self.assertEqual(found, {b"SECRET-CANARY"})
            self.assertFalse(oversized)

    def test_scanner_reports_oversized_without_reading_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "db"
            path.write_bytes(b"123456789")
            found, oversized = _scan_file_for_canaries(
                path, (b"SECRET",), max_file_bytes=8, chunk_bytes=4
            )
            self.assertEqual(found, set())
            self.assertTrue(oversized)

    def test_absent_canary_is_not_counted_as_attempted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with EventStore(Path(directory) / "apprentice.sqlite") as store:
                profile = store.create_profile("Receipt test")
                store.record_audit(
                    profile,
                    component="benchmark",
                    action="canary_attempt",
                    reason_code="SYNTHETIC_CANARY",
                    details=canary_receipt("redacted-target-label"),
                )
                report = run_benchmarks(store, profile)
                self.assertEqual(report["vector"]["privacy"]["attempted_canaries"], 1)
                self.assertEqual(
                    [item["canary_id"] for item in report["vector"]["privacy"]["receipt"]],
                    ["redacted-target-label"],
                )


if __name__ == "__main__":
    unittest.main()
