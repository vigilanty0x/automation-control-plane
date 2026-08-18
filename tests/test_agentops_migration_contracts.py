from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import unittest

from automation_control_plane.agentops.migration_contracts import MIGRATION_CONTRACTS, migration_contract_inventory
from automation_control_plane.agentops.compatibility import SOURCE_INTERFACES
from automation_control_plane.agentops.cli import main


class MigrationContractTests(unittest.TestCase):
    def test_exact_twelve_satellite_sources_are_covered(self) -> None:
        expected = {
            item["repository"]: item["source_sha"]
            for item in SOURCE_INTERFACES
            if item["repository"] != "automation-control-plane"
        }
        observed = {item["repository"]: item["source_sha"] for item in MIGRATION_CONTRACTS}
        self.assertEqual(len(MIGRATION_CONTRACTS), 12)
        self.assertEqual(observed, expected)

    def test_every_contract_is_fail_closed_for_activation(self) -> None:
        allowed_strategies = {"candidate_adapter", "projection_only", "evidence_only", "incompatibility_contract"}
        for item in MIGRATION_CONTRACTS:
            self.assertEqual(item["contract_version"], "1.0")
            self.assertIn(item["strategy"], allowed_strategies)
            self.assertEqual(item["activation_state"], "blocked")
            self.assertTrue(item["source_surface"])
            self.assertTrue(item["target_surface"])
            self.assertTrue(item["preserved_invariants"])
            self.assertTrue(item["semantic_deltas"])
            self.assertTrue(item["adapter_requirements"])

    def test_security_sensitive_sources_are_never_drop_in_adapters(self) -> None:
        by_repository = {item["repository"]: item for item in MIGRATION_CONTRACTS}
        self.assertEqual(by_repository["human-in-the-loop-queue"]["strategy"], "evidence_only")
        self.assertEqual(by_repository["idempotency-kit"]["strategy"], "evidence_only")
        self.assertEqual(by_repository["agent-inbox"]["strategy"], "projection_only")
        self.assertEqual(by_repository["agent-budgeter"]["strategy"], "incompatibility_contract")
        self.assertEqual(by_repository["agent-retry-kit"]["strategy"], "incompatibility_contract")
        self.assertEqual(by_repository["taskgraph"]["strategy"], "incompatibility_contract")

    def test_contract_inventory_passes_without_migrating_or_activating(self) -> None:
        result = migration_contract_inventory()
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["details"]["exact_source_match"])
        self.assertEqual(result["details"]["contract_count"], 12)
        self.assertFalse(result["details"]["legacy_aliases_activated"])
        self.assertFalse(result["details"]["migration_performed"])
        self.assertFalse(result["details"]["irreversible_actions_allowed"])
        self.assertTrue(result["details"]["consumer_inventory_required_before_activation"])
        self.assertTrue(result["details"]["human_approval_required_before_activation"])

    def test_cli_is_deterministic_and_requires_no_input(self) -> None:
        first = io.StringIO()
        second = io.StringIO()
        with redirect_stdout(first):
            first_code = main(["migration-contracts"])
        with redirect_stdout(second):
            second_code = main(["migration-contracts"])
        self.assertEqual(first_code, 0)
        self.assertEqual(second_code, 0)
        self.assertEqual(first.getvalue(), second.getvalue())
        payload = json.loads(first.getvalue())
        self.assertEqual(payload["kind"], "migration_contract_inventory")
        self.assertEqual(payload["details"]["contract_count"], 12)
        self.assertFalse(payload["details"]["migration_performed"])


if __name__ == "__main__":
    unittest.main()
