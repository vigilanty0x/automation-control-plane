from __future__ import annotations

import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from apprentice_ai.errors import IntegrityError, ValidationError
from apprentice_ai.privacy import PrivacyGuard, PrivacyPolicy
from apprentice_ai.localfs import secure_directory
from apprentice_ai.service import ensure_data_dir
from apprentice_ai.store import EventStore


class PrivacyGuardTests(unittest.TestCase):
    def test_denied_application_is_never_returned(self) -> None:
        decision = PrivacyGuard().sanitize_event(
            {
                "application": {"id": "synthetic-secret-vault"},
                "action": {"kind": "type", "value": "must-not-survive"},
            }
        )
        self.assertFalse(decision.allowed)
        self.assertIsNone(decision.event)
        self.assertEqual(decision.reason_code, "DENY_APPLICATION")

    def test_secrets_and_personal_identifiers_are_redacted_recursively(self) -> None:
        source = {
            "context": {
                "email": "alice@example.test",
                "path": "/home/alice/private/data.csv",
                "nested": ["ghp_abcdefghijklmnopqrstuvwxyz"],
            },
        }
        sanitized, categories = PrivacyGuard().sanitize_payload(source)
        rendered = repr(sanitized)
        self.assertNotIn("secret-value-123", rendered)
        self.assertNotIn("alice@example.test", rendered)
        self.assertNotIn("/home/alice", rendered)
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz", rendered)
        self.assertGreaterEqual(len(categories), 3)
        self.assertEqual(source["context"]["email"], "alice@example.test")

    def test_non_string_keys_are_rejected_instead_of_colliding(self) -> None:
        with self.assertRaisesRegex(ValidationError, "keys must be strings"):
            PrivacyGuard().sanitize_event(
                {
                    "application": {"id": "synthetic-sheet"},
                    "action": {"kind": "note"},
                    "context": {1: "one", "1": "string-one"},
                }
            )

    def test_denied_parent_domain_covers_subdomains(self) -> None:
        guard = PrivacyGuard(PrivacyPolicy(denied_domains=frozenset({"private.test"})))
        decision = guard.sanitize_event(
            {
                "application": {"id": "browser"},
                "action": {"kind": "navigate"},
                "context": {"url": "https://vault.private.test/login"},
            }
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason_code, "DENY_DOMAIN")

    def test_denied_domain_trailing_dot_cannot_bypass_policy(self) -> None:
        guard = PrivacyGuard(PrivacyPolicy(denied_domains=frozenset({"private.test"})))
        decision = guard.sanitize_event(
            {
                "application": {"id": "browser"},
                "action": {"kind": "navigate"},
                "context": {"url": "https://private.test./login"},
            }
        )
        self.assertFalse(decision.allowed)

    def test_sensitive_semantic_key_is_redacted_even_without_regex_match(self) -> None:
        decision = PrivacyGuard().sanitize_event(
            {
                "application": {"id": "synthetic-sheet"},
                "action": {"kind": "inspect"},
                "context": {"password": "shortvalue", "demo_id": "visible"},
            }
        )
        self.assertEqual(decision.event["context"]["password"], "[REDACTED:SENSITIVE_KEY]")
        self.assertEqual(decision.event["context"]["demo_id"], "visible")

    def test_unapproved_context_field_is_rejected_not_persisted_as_private_free_text(self) -> None:
        with self.assertRaisesRegex(ValidationError, "context contains unsupported"):
            PrivacyGuard().sanitize_event(
                {
                    "application": {"id": "synthetic-sheet"},
                    "action": {"kind": "inspect"},
                    "context": {"note": "UNLABELLED-PRIVATE-VALUE"},
                }
            )


class EventStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "apprentice.sqlite"
        self.store = EventStore(self.db)
        self.profile = self.store.create_profile("Test", "pro_test")
        self.session = self.store.create_session(
            self.profile, session_id="ses_test", metadata={"synthetic": True}
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def event(self, number: int) -> dict[str, object]:
        return {
            "event_id": f"evt_test_{number:03d}",
            "timestamp": f"2026-08-16T10:{number:02d}:00Z",
            "source": "test",
            "application": {"id": "synthetic-sheet"},
            "action": {"kind": "click", "target_role": "button"},
        }

    def test_append_and_verify_hash_chain(self) -> None:
        for number in range(3):
            self.store.append_event(self.profile, self.session, self.event(number), PrivacyGuard())
        receipt = self.store.verify_chain(self.profile, self.session)
        self.assertEqual(receipt["events"], 3)
        self.assertTrue(receipt["valid"])
        self.assertNotEqual(receipt["head"], "0" * 64)

    def test_tampering_is_detected(self) -> None:
        self.store.append_event(self.profile, self.session, self.event(1), PrivacyGuard())
        self.store.connection.execute(
            "UPDATE events SET payload_json=replace(payload_json,'click','clickx')"
        )
        with self.assertRaises(IntegrityError):
            self.store.verify_chain(self.profile, self.session)

    def test_closed_session_is_immutable(self) -> None:
        self.store.append_event(self.profile, self.session, self.event(0), PrivacyGuard())
        self.store.end_session(self.profile, self.session)
        with self.assertRaisesRegex(IntegrityError, "closed"):
            self.store.append_event(self.profile, self.session, self.event(1), PrivacyGuard())

    def test_closed_session_tail_truncation_is_detected(self) -> None:
        self.store.append_event(self.profile, self.session, self.event(0), PrivacyGuard())
        self.store.append_event(self.profile, self.session, self.event(1), PrivacyGuard())
        self.store.end_session(self.profile, self.session)
        self.store.connection.execute(
            "DELETE FROM events WHERE session_id=? AND sequence=2", (self.session,)
        )
        with self.assertRaisesRegex(IntegrityError, "tail"):
            self.store.verify_chain(self.profile, self.session)

    def test_active_session_tail_truncation_is_detected_by_incremental_anchor(self) -> None:
        self.store.append_event(self.profile, self.session, self.event(0), PrivacyGuard())
        self.store.append_event(self.profile, self.session, self.event(1), PrivacyGuard())
        self.store.connection.execute(
            "DELETE FROM events WHERE session_id=? AND sequence=2", (self.session,)
        )
        with self.assertRaisesRegex(IntegrityError, "tail"):
            self.store.verify_chain(self.profile, self.session)

    def test_missing_session_never_verifies_as_empty_valid_chain(self) -> None:
        with self.assertRaisesRegex(Exception, "not found"):
            self.store.verify_chain(self.profile, "ses_missing")

    def test_non_utc_timestamp_is_rejected(self) -> None:
        event = self.event(1)
        event["timestamp"] = "2026-08-16T10:01:00+02:00"
        with self.assertRaisesRegex(ValidationError, "timestamp"):
            self.store.append_event(self.profile, self.session, event, PrivacyGuard())

    def test_blocked_event_writes_only_category_to_audit(self) -> None:
        raw = {
            "event_id": "evt_secret_001",
            "timestamp": "2026-08-16T10:01:00Z",
            "application": {"id": "synthetic-secret-vault"},
            "action": {"kind": "type", "value": "ABSOLUTELY-NOT-IN-DB"},
        }
        self.assertIsNone(self.store.append_event(self.profile, self.session, raw, PrivacyGuard()))
        database_bytes = self.db.read_bytes()
        self.assertNotIn(b"ABSOLUTELY-NOT-IN-DB", database_bytes)
        self.assertEqual(self.store.audit_events(self.profile)[0]["reason_code"], "DENY_APPLICATION")

    def test_cross_profile_routine_lookup_is_rejected(self) -> None:
        other = self.store.create_profile("Other", "pro_other")
        routine = {"routine_id": "rou_private", "status": "observed"}
        self.store.put_routine(self.profile, routine)
        with self.assertRaisesRegex(Exception, "not found"):
            self.store.get_routine(other, "rou_private")

    def test_cross_profile_episode_session_relation_is_rejected(self) -> None:
        other = self.store.create_profile("Other", "pro_other")
        with self.assertRaisesRegex(Exception, "session not found"):
            self.store.put_episode(other, self.session, {"episode_id": "epi_cross"})

    def test_session_metadata_and_audit_details_are_sanitized_before_sqlite(self) -> None:
        secret_one = "api_key=metadata-secret-999"
        secret_two = "ghp_abcdefghijklmnopqrstuvwxyz"
        with self.assertRaisesRegex(ValidationError, "unsupported fields"):
            self.store.create_session(
                self.profile,
                mode="import",
                source="test",
                metadata={"note": secret_one},
                session_id="ses_safe_metadata",
            )
        self.store.record_audit(
            self.profile,
            component="test",
            action="test",
            reason_code="TEST",
            details={"credential": "tiny", "note": secret_two, "session": self.session},
        )
        self.store.connection.execute("PRAGMA wal_checkpoint(FULL)")
        raw = b"".join(path.read_bytes() for path in Path(self.temp.name).glob("apprentice.sqlite*"))
        self.assertNotIn(secret_one.encode(), raw)
        self.assertNotIn(secret_two.encode(), raw)
        self.assertNotIn(b'"tiny"', raw)

    def test_session_source_is_a_closed_adapter_identifier_not_free_text(self) -> None:
        with self.assertRaisesRegex(ValidationError, "registered adapter"):
            self.store.create_session(
                self.profile,
                mode="import",
                source="api_key=LEAKED-SECRET-12345",
                session_id="ses_bad_source",
            )

    def test_audit_action_cannot_be_used_as_secret_storage(self) -> None:
        canary = "ghp_abcdefghijklmnopqrstuvwxyz"
        with self.assertRaisesRegex(ValidationError, "sensitive material"):
            self.store.record_audit(
                self.profile,
                component="test",
                action=canary,
                reason_code="TEST",
                details={},
            )
        self.store.connection.execute("PRAGMA wal_checkpoint(FULL)")
        raw = b"".join(path.read_bytes() for path in Path(self.temp.name).glob("apprentice.sqlite*"))
        self.assertNotIn(canary.encode(), raw)

    def test_full_profile_purge_removes_name_and_leaves_only_tombstone(self) -> None:
        private_name = "ERASE-ME-PRIVATE-PROFILE"
        profile = self.store.create_profile(private_name)
        receipt = self.store.purge_profile_data(profile, confirmation=profile)
        self.assertTrue(receipt["complete"])
        row = next(item for item in self.store.list_profiles() if item["profile_id"] == profile)
        self.assertEqual(row["name"], "[deleted]")
        self.assertEqual(row["status"], "deleted")
        raw = b"".join(path.read_bytes() for path in Path(self.temp.name).glob("apprentice.sqlite*"))
        self.assertNotIn(private_name.encode(), raw)
        with self.assertRaisesRegex(Exception, "not active"):
            self.store.create_session(profile, session_id="ses_after_purge")

    def test_symlink_database_is_rejected(self) -> None:
        target = Path(self.temp.name) / "target.sqlite"
        target.write_bytes(b"not-a-db")
        link = Path(self.temp.name) / "link.sqlite"
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaisesRegex(ValidationError, "non-symlink"):
            EventStore(link)

    def test_existing_unknown_database_is_rejected_before_schema_creation(self) -> None:
        candidate = Path(self.temp.name) / "unknown.sqlite"
        connection = sqlite3.connect(candidate)
        connection.execute("CREATE TABLE unrelated(value TEXT)")
        connection.commit()
        connection.close()
        before = candidate.read_bytes()
        with self.assertRaisesRegex(ValidationError, "schema metadata"):
            EventStore(candidate)
        self.assertEqual(candidate.read_bytes(), before)


class LocalStatePermissionTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "POSIX permission contract")
    def test_umask_022_still_produces_private_directory_and_sqlite_family(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary) / "state"
            previous = os.umask(0o022)
            try:
                root = ensure_data_dir(data_dir)
                with EventStore(root / "apprentice.sqlite") as store:
                    store.create_profile("Modes", "pro_modes")
                    self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
                    family = list(root.glob("apprentice.sqlite*"))
                    self.assertTrue(any(path.name.endswith("-wal") for path in family))
                    self.assertTrue(any(path.name.endswith("-shm") for path in family))
                    for path in family:
                        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, path.name)
            finally:
                os.umask(previous)

    @unittest.skipUnless(os.name == "posix", "POSIX permission contract")
    def test_owned_existing_state_with_open_modes_is_restricted_on_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "state"
            database = root / "apprentice.sqlite"
            with EventStore(database) as store:
                store.create_profile("Existing modes", "pro_existing_modes")
            os.chmod(root, 0o755)
            os.chmod(database, 0o644)
            with EventStore(database):
                self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(database.stat().st_mode), 0o600)

    @unittest.skipUnless(os.name == "posix", "POSIX ownership contract")
    def test_non_owned_directory_is_rejected_before_chmod(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / "state"
            candidate.mkdir()
            with (
                mock.patch("apprentice_ai.localfs.os.geteuid", return_value=os.geteuid() + 1),
                mock.patch("apprentice_ai.localfs.os.fchmod") as change_mode,
            ):
                with self.assertRaisesRegex(ValidationError, "not owned"):
                    secure_directory(candidate)
                change_mode.assert_not_called()

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_symlink_directory_is_rejected_before_chmod(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "target"
            target.mkdir()
            link = Path(temporary) / "state-link"
            try:
                os.symlink(target, link, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            # Python 3.11 on Windows does not expose os.fchmod.  The test still
            # exercises the cross-platform pre-open symlink guard, so install a
            # harmless mock attribute when the platform does not provide one.
            with mock.patch("apprentice_ai.localfs.os.fchmod", create=True) as change_mode:
                with self.assertRaisesRegex(ValidationError, "non-symlink"):
                    secure_directory(link)
                change_mode.assert_not_called()


if __name__ == "__main__":
    unittest.main()
