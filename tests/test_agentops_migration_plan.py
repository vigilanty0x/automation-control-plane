from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from automation_control_plane.agentops.cli import main
from automation_control_plane.agentops.migration_contracts import MIGRATION_CONTRACTS
from automation_control_plane.agentops.migration_plan import plan_migration


CANDIDATE_SHA = "c" * 40


class MigrationPlanTests(unittest.TestCase):
    def payload(
        self,
        *,
        include_pilot: bool = False,
        verified_import_source: str | None = None,
        candidate_sha: str | None = CANDIDATE_SHA,
    ) -> dict:
        complete_kinds = ["documentation", "fork", "import", "package", "workflow"]
        if include_pilot:
            complete_kinds.append("pilot")
        inventory_sources = []
        triage_sources = []
        for contract in MIGRATION_CONTRACTS:
            repository = contract["repository"]
            inventory_sources.append({"repository": repository, "references": []})
            candidates = []
            if repository == verified_import_source:
                candidates.append(
                    {
                        "consumer": "public-consumer",
                        "evidence": "github://owner/public-consumer@" + "a" * 40 + "/src/app.py#L1",
                        "classification": "verified_import",
                    }
                )
            triage_sources.append({"repository": repository, "candidates": candidates})
        payload = {
            "consumer_inventory": {
                "scan_scope": {
                    "observed_at": "2026-08-18T16:47:05Z",
                    "expires_at": "2026-09-17T16:47:05Z",
                    "repositories_expected": 112,
                    "repositories_scanned": 112,
                    "complete_kinds": complete_kinds,
                },
                "sources": inventory_sources,
            },
            "triage": {
                "schema_version": 1,
                "status": "passed",
                "unresolved": 0,
                "sources": triage_sources,
            },
        }
        if candidate_sha is not None:
            payload["candidate_sha"] = candidate_sha
        return payload

    def test_public_static_evidence_can_prepare_but_never_authorize(self) -> None:
        result = plan_migration(self.payload())
        self.assertEqual(result["status"], "passed")
        details = result["details"]
        self.assertEqual(details["candidate_sha"], CANDIDATE_SHA)
        self.assertTrue(details["static_scope_complete"])
        self.assertTrue(details["triage_complete"])
        self.assertFalse(details["pilot_coverage_complete"])
        self.assertTrue(details["planning_evidence_ready"])
        self.assertEqual(details["formal_migration_gate"], "blocked")
        self.assertFalse(details["legacy_aliases_activated"])
        self.assertFalse(details["migration_performed"])
        self.assertFalse(details["irreversible_actions_allowed"])
        self.assertTrue(details["named_human_approval_required"])
        self.assertIn("obtain explicit pilot/adopter completeness attestation", details["next_actions"])

    def test_candidate_sha_is_bound_into_evidence(self) -> None:
        result = plan_migration(self.payload())
        self.assertEqual(result["details"]["candidate_sha"], CANDIDATE_SHA)
        self.assertEqual(result["input_sha256"], result["input_sha256"])

    def test_legacy_payload_without_candidate_sha_remains_supported(self) -> None:
        result = plan_migration(self.payload(candidate_sha=None))
        self.assertEqual(result["status"], "passed")
        self.assertIsNone(result["details"]["candidate_sha"])

    def test_invalid_candidate_sha_fails_closed(self) -> None:
        result = plan_migration(self.payload(candidate_sha="not-a-git-sha"))
        self.assertEqual(result["status"], "blocked")

    def test_only_candidate_adapters_enter_adapter_candidate_list(self) -> None:
        result = plan_migration(self.payload())
        expected = sorted(
            contract["repository"]
            for contract in MIGRATION_CONTRACTS
            if contract["strategy"] == "candidate_adapter"
        )
        self.assertEqual(sorted(result["details"]["adapter_candidates"]), expected)
        by_repo = {item["repository"]: item for item in result["details"]["source_plans"]}
        self.assertEqual(by_repo["agent-inbox"]["planning_state"], "projection_only_source_retained")
        self.assertEqual(by_repo["human-in-the-loop-queue"]["planning_state"], "evidence_only_source_retained")
        self.assertEqual(by_repo["taskgraph"]["planning_state"], "incompatible_source_retained")

    def test_verified_runtime_import_blocks_adapter_shortcut(self) -> None:
        result = plan_migration(self.payload(verified_import_source="agentmesh"))
        by_repo = {item["repository"]: item for item in result["details"]["source_plans"]}
        self.assertEqual(by_repo["agentmesh"]["planning_state"], "consumer_migration_required")
        self.assertEqual(by_repo["agentmesh"]["verified_import_count"], 1)
        self.assertNotIn("agentmesh", result["details"]["adapter_candidates"])

    def test_pilot_completeness_still_does_not_open_formal_gate(self) -> None:
        result = plan_migration(self.payload(include_pilot=True))
        self.assertTrue(result["details"]["pilot_coverage_complete"])
        self.assertEqual(result["details"]["formal_migration_gate"], "blocked")
        self.assertTrue(result["details"]["named_human_approval_required"])
        self.assertNotIn("obtain explicit pilot/adopter completeness attestation", result["details"]["next_actions"])

    def test_missing_satellite_source_fails_closed(self) -> None:
        payload = self.payload()
        payload["triage"]["sources"].pop()
        result = plan_migration(payload)
        self.assertEqual(result["status"], "blocked")

    def test_cli_accepts_bounded_evidence_file_and_still_blocks_formal_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "migration-plan.json"
            path.write_text(json.dumps(self.payload()), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["migration-plan", "--input", str(path)])
        self.assertEqual(code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["kind"], "migration_plan")
        self.assertEqual(result["details"]["candidate_sha"], CANDIDATE_SHA)
        self.assertEqual(result["details"]["formal_migration_gate"], "blocked")
        self.assertFalse(result["details"]["migration_performed"])


if __name__ == "__main__":
    unittest.main()
