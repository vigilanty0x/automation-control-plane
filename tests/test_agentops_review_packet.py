from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from scripts import agentops_review_packet as packet


DIGEST = "a" * 64
RAW = "b" * 64
CANDIDATE = "c" * 40


def _migration_plan(*, pilot_complete: bool = False) -> dict:
    value = {
        "status": "passed",
        "details": {
            "planning_evidence_ready": True,
            "formal_migration_gate": "blocked",
            "legacy_aliases_activated": False,
            "migration_performed": False,
            "irreversible_actions_allowed": False,
            "named_human_approval_required": True,
            "default_branch_live_evidence_required": True,
            "pilot_coverage_complete": pilot_complete,
            "observed_runtime_reference_count": 0,
            "adapter_candidates": ["agentmesh"],
        },
        "evidence_sha256": DIGEST,
    }
    return value


def _proof(*, adapter: bool = False) -> dict:
    value = {
        "status": "passed",
        "evidence_sha256": DIGEST,
        "migration_performed": False,
        "legacy_aliases_activated": False,
    }
    if adapter:
        value.update(
            {
                "consumer_mutation_performed": False,
                "source_retirement_authorized": False,
            }
        )
    return value


class TechnicalReviewPacketTests(unittest.TestCase):
    def build(self, *, pilot_complete: bool = False) -> dict:
        return packet.build_packet(
            candidate_sha=CANDIDATE,
            migration_plan=_migration_plan(pilot_complete=pilot_complete),
            migration_plan_raw_sha256=RAW,
            compatibility=_proof(),
            compatibility_raw_sha256=RAW,
            adapters=_proof(adapter=True),
            adapters_raw_sha256=RAW,
            core=_proof(),
            core_raw_sha256=RAW,
        )

    def test_packet_is_technically_ready_but_never_authorizes_mutation(self) -> None:
        result = self.build()
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["technical_readiness"])
        self.assertEqual(result["formal_migration_gate"], "blocked")
        self.assertFalse(result["alias_activation_authorized"])
        self.assertFalse(result["consumer_mutation_authorized"])
        self.assertFalse(result["migration_authorized"])
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["source_retirement_authorized"])
        self.assertFalse(result["archive_authorized"])
        self.assertIn("pilot_adopter_completeness_attestation", result["human_inputs_required"])
        self.assertIn("named_human_approval", result["human_inputs_required"])

    def test_complete_pilot_scope_only_removes_that_missing_human_input(self) -> None:
        result = self.build(pilot_complete=True)
        self.assertNotIn("pilot_adopter_completeness_attestation", result["human_inputs_required"])
        self.assertIn("default_branch_live_evidence", result["human_inputs_required"])
        self.assertIn("named_human_approval", result["human_inputs_required"])
        self.assertEqual(result["formal_migration_gate"], "blocked")

    def test_packet_digest_is_deterministic(self) -> None:
        first = self.build()
        second = self.build()
        self.assertEqual(first, second)
        without_digest = dict(first)
        observed = without_digest.pop("evidence_sha256")
        expected = sha256(packet._canonical(without_digest).encode("utf-8")).hexdigest()
        self.assertEqual(observed, expected)

    def test_rejects_any_mutation_claim_in_adapter_proof(self) -> None:
        adapters = _proof(adapter=True)
        adapters["consumer_mutation_performed"] = True
        with self.assertRaises(packet.PacketError):
            packet.build_packet(
                candidate_sha=CANDIDATE,
                migration_plan=_migration_plan(),
                migration_plan_raw_sha256=RAW,
                compatibility=_proof(),
                compatibility_raw_sha256=RAW,
                adapters=adapters,
                adapters_raw_sha256=RAW,
                core=_proof(),
                core_raw_sha256=RAW,
            )

    def test_rejects_open_formal_gate(self) -> None:
        plan = _migration_plan()
        plan["details"]["formal_migration_gate"] = "open"
        with self.assertRaises(packet.PacketError):
            packet.build_packet(
                candidate_sha=CANDIDATE,
                migration_plan=plan,
                migration_plan_raw_sha256=RAW,
                compatibility=_proof(),
                compatibility_raw_sha256=RAW,
                adapters=_proof(adapter=True),
                adapters_raw_sha256=RAW,
                core=_proof(),
                core_raw_sha256=RAW,
            )

    def test_cli_writes_packet_from_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = {
                "migration.json": _migration_plan(),
                "compatibility.json": _proof(),
                "adapters.json": _proof(adapter=True),
                "core.json": _proof(),
            }
            for name, value in inputs.items():
                (root / name).write_text(json.dumps(value), encoding="utf-8")
            output = root / "packet.json"
            code = packet.main(
                [
                    "--candidate-sha",
                    CANDIDATE,
                    "--migration-plan",
                    str(root / "migration.json"),
                    "--compatibility",
                    str(root / "compatibility.json"),
                    "--adapters",
                    str(root / "adapters.json"),
                    "--core",
                    str(root / "core.json"),
                    "--output",
                    str(output),
                ]
            )
            self.assertEqual(code, 0)
            written = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(written["formal_migration_gate"], "blocked")
            self.assertFalse(written["migration_authorized"])


if __name__ == "__main__":
    unittest.main()
