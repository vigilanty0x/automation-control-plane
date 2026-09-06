from __future__ import annotations

import copy
import os
import stat
import tempfile
import unittest
import warnings
import zipfile
from unittest import mock
from pathlib import Path

from apprentice_ai.errors import IntegrityError, ValidationError
import apprentice_ai.learnpack as learnpack_module
from apprentice_ai.learning import apply_answer, discover_routine, generate_question, segment_sessions
from apprentice_ai.learnpack import (
    build_pack_files,
    export_learnpack,
    import_learnpack,
    inspect_learnpack,
    validate_learnpack,
)
from apprentice_ai.skills import compile_skill
from apprentice_ai.store import EventStore
from apprentice_ai.synthetic import seed_synthetic_office


class LearnPackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = EventStore(self.root / "apprentice.sqlite")
        self.profile = self.store.create_profile("Builder", "pro_builder")
        seed_synthetic_office(self.store, self.profile)
        segment_sessions(self.store, self.profile)
        routine = discover_routine(self.store, self.profile)
        question = generate_question(self.store, self.profile, routine["routine_id"])
        apply_answer(self.store, self.profile, question["id"], "yes", synthetic=True)
        self.skill = compile_skill(self.store, self.profile, routine["routine_id"])

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def test_export_is_deterministic_valid_and_privacy_clean(self) -> None:
        first = self.root / "first.learnpack"
        second = self.root / "second.learnpack"
        receipt = export_learnpack(
            self.store, self.profile, self.skill["skill_id"], self.skill["version"], first
        )
        export_learnpack(
            self.store, self.profile, self.skill["skill_id"], self.skill["version"], second
        )
        self.assertEqual(first.read_bytes(), second.read_bytes())
        report = validate_learnpack(first)
        self.assertTrue(report["valid"])
        self.assertEqual(report["digest"], receipt["digest"])
        self.assertFalse(report["execution_supported"])

    def test_import_into_blank_profile_preserves_inspectable_bundle_but_no_authority(self) -> None:
        pack = self.root / "reference.learnpack"
        export_learnpack(
            self.store, self.profile, self.skill["skill_id"], self.skill["version"], pack
        )
        blank = self.store.create_profile("Blank", "pro_blank")
        receipt = import_learnpack(self.store, blank, pack)
        persisted = self.store.get_import(blank, receipt["import_id"])
        self.assertEqual(persisted["import_id"], receipt["import_id"])
        self.assertEqual(persisted["trust_state"], "disabled_untrusted")
        self.assertFalse(persisted["execution_allowed"])
        self.assertEqual(persisted["bundle"]["skill_ir"]["skill_id"], self.skill["skill_id"])
        self.assertIn("preview-only", persisted["bundle"]["skill_md"])
        self.assertEqual(len(persisted["bundle"]["tests"]["holdout_cases"]), 2)
        with self.assertRaisesRegex(Exception, "not found"):
            self.store.get_import(self.profile, receipt["import_id"])

    def test_forged_holdout_aggregate_is_recomputed_and_rejected(self) -> None:
        forged = copy.deepcopy(self.skill)
        forged["verification"]["holdout_cases"][0]["passed"] = False
        forged["verification"]["all_holdout_passed"] = True
        with self.assertRaisesRegex(ValidationError, "HOLDOUT"):
            build_pack_files(forged)

    def test_secret_in_extra_skill_field_blocks_export(self) -> None:
        tainted = copy.deepcopy(self.skill)
        tainted["notes"] = "api_key=LEAK-ME-123456"
        with self.assertRaisesRegex(ValidationError, "privacy scan"):
            build_pack_files(tainted)

    def test_path_traversal_member_is_rejected(self) -> None:
        hostile = self.root / "traversal.learnpack"
        with zipfile.ZipFile(hostile, "w") as archive:
            archive.writestr("../escape", b"x")
        with self.assertRaisesRegex(ValidationError, "unsafe"):
            validate_learnpack(hostile)

    def test_duplicate_member_is_rejected(self) -> None:
        hostile = self.root / "duplicate.learnpack"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with zipfile.ZipFile(hostile, "w") as archive:
                archive.writestr("learnpack.json", b"{}")
                archive.writestr("learnpack.json", b"{}")
        with self.assertRaisesRegex(ValidationError, "duplicate"):
            validate_learnpack(hostile)

    def test_symlink_member_is_rejected(self) -> None:
        hostile = self.root / "symlink.learnpack"
        info = zipfile.ZipInfo("learnpack.json")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(hostile, "w") as archive:
            archive.writestr(info, b"target")
        with self.assertRaisesRegex(ValidationError, "non-regular"):
            validate_learnpack(hostile)

    def test_digest_mismatch_is_rejected(self) -> None:
        source = self.root / "valid.learnpack"
        hostile = self.root / "tampered.learnpack"
        export_learnpack(
            self.store, self.profile, self.skill["skill_id"], self.skill["version"], source
        )
        with zipfile.ZipFile(source, "r") as archive:
            entries = {info.filename: archive.read(info) for info in archive.infolist()}
        entries["README.md"] += b"tampered"
        with zipfile.ZipFile(hostile, "w") as archive:
            for name, payload in entries.items():
                archive.writestr(name, payload)
        with self.assertRaisesRegex(IntegrityError, "digest mismatch"):
            validate_learnpack(hostile)

    def test_compression_bomb_ratio_is_rejected_before_schema_processing(self) -> None:
        hostile = self.root / "bomb.learnpack"
        with zipfile.ZipFile(hostile, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("learnpack.json", b"A" * (1024 * 1024))
        with self.assertRaisesRegex(ValidationError, "compression ratio"):
            validate_learnpack(hostile)

    def test_inspection_rejects_archive_change_via_digest_contract(self) -> None:
        pack = self.root / "inspect.learnpack"
        export_learnpack(
            self.store, self.profile, self.skill["skill_id"], self.skill["version"], pack
        )
        inspection = inspect_learnpack(pack)
        self.assertEqual(inspection["skill_ir"]["skill_id"], self.skill["skill_id"])
        self.assertEqual(inspection["report"]["privacy_scan"], "passed")

    def test_inspection_uses_one_snapshot_even_if_path_is_replaced(self) -> None:
        target = self.root / "snapshot.learnpack"
        replacement = self.root / "replacement.learnpack"
        export_learnpack(
            self.store, self.profile, self.skill["skill_id"], self.skill["version"], target
        )
        alternate = copy.deepcopy(self.skill)
        alternate["version"] = "0.1.1"
        files = build_pack_files(alternate)
        with zipfile.ZipFile(replacement, "w", compression=zipfile.ZIP_STORED) as archive:
            for name in sorted(files):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_STORED
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(info, files[name])
        original_snapshot = learnpack_module._snapshot_archive

        def snapshot_then_replace(path):
            snapshot = original_snapshot(path)
            os.replace(replacement, target)
            return snapshot

        with mock.patch.object(
            learnpack_module, "_snapshot_archive", side_effect=snapshot_then_replace
        ) as snap:
            inspection = inspect_learnpack(target)
        self.assertEqual(snap.call_count, 1)
        self.assertEqual(inspection["report"]["version"], "0.1.0")
        self.assertEqual(validate_learnpack(target)["version"], "0.1.1")

    def test_tautological_fake_holdout_cannot_be_exported_as_store_verified(self) -> None:
        forged = copy.deepcopy(self.skill)
        forged["version"] = "0.1.1"
        forged["verification"]["induction_ids"] = ["epi_fake_1", "epi_fake_2", "epi_fake_3"]
        forged["verification"]["holdout_cases"] = [
            {
                "episode_id": "epi_fake_4",
                "demo_id": "D4",
                "split": "holdout",
                "passed": True,
                "checks": [{"branch_id": "branch_1", "expected": True, "observed": True}],
            },
            {
                "episode_id": "epi_fake_5",
                "demo_id": "D5",
                "split": "holdout",
                "passed": True,
                "checks": [{"branch_id": "branch_1", "expected": False, "observed": False}],
            },
        ]
        self.store.put_skill(self.profile, forged)
        with self.assertRaisesRegex(IntegrityError, "canonical stored routine"):
            export_learnpack(
                self.store,
                self.profile,
                forged["skill_id"],
                forged["version"],
                self.root / "fake-holdout.learnpack",
            )


if __name__ == "__main__":
    unittest.main()
