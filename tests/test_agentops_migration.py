from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from automation_control_plane.agentops import inventory_consumers, rehearse_rollback
from automation_control_plane.agentops.cli import main
from automation_control_plane.agentops.inventory import SOURCE_INVENTORY


class ConsumerInventoryTests(unittest.TestCase):
    def payload(self) -> dict:
        sources = [
            {"repository": item["repository"], "references": []}
            for item in SOURCE_INVENTORY
        ]
        sources[0]["references"] = [
            {
                "consumer": "synthetic-consumer",
                "kind": "import",
                "evidence": "synthetic://consumer/import",
            }
        ]
        return {
            "scan_scope": {
                "observed_at": "2026-08-18T03:00:00Z",
                "expires_at": "2026-09-17T03:00:00Z",
                "repositories_expected": 3,
                "repositories_scanned": 3,
                "complete_kinds": [
                    "documentation",
                    "fork",
                    "import",
                    "package",
                    "pilot",
                    "workflow",
                ],
            },
            "sources": sources,
        }

    def test_complete_inventory_passes_without_mutation(self) -> None:
        result = inventory_consumers(self.payload())
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["details"]["coverage_complete"])
        self.assertEqual(result["details"]["source_count"], 13)
        self.assertEqual(result["details"]["reference_count"], 1)
        self.assertEqual(result["details"]["unique_consumer_count"], 1)
        self.assertFalse(result["details"]["mutation_performed"])
        self.assertEqual(result["details"]["portfolio_gate"], "not_run")

    def test_partial_repository_coverage_fails_closed(self) -> None:
        payload = self.payload()
        payload["scan_scope"]["repositories_scanned"] = 2
        result = inventory_consumers(payload)
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["details"]["coverage_complete"])

    def test_missing_kind_coverage_fails_closed(self) -> None:
        payload = self.payload()
        payload["scan_scope"]["complete_kinds"].remove("pilot")
        result = inventory_consumers(payload)
        self.assertEqual(result["status"], "failed")

    def test_duplicate_source_is_blocked(self) -> None:
        payload = self.payload()
        payload["sources"][-1] = dict(payload["sources"][0])
        result = inventory_consumers(payload)
        self.assertEqual(result["status"], "blocked")

    def test_unknown_source_is_blocked(self) -> None:
        payload = self.payload()
        payload["sources"][0]["repository"] = "unknown-source"
        result = inventory_consumers(payload)
        self.assertEqual(result["status"], "blocked")

    def test_duplicate_reference_is_blocked(self) -> None:
        payload = self.payload()
        reference = dict(payload["sources"][0]["references"][0])
        payload["sources"][0]["references"].append(reference)
        result = inventory_consumers(payload)
        self.assertEqual(result["status"], "blocked")


class RollbackRehearsalTests(unittest.TestCase):
    def payload(self) -> dict:
        return {
            "baseline": {
                "target_git_sha": "1" * 40,
                "source_support_state": "active",
            },
            "candidate": {
                "target_git_sha": "2" * 40,
                "target_state": "REHEARSAL",
                "redirects_active": False,
                "aliases_active": False,
                "consumers_migrated": 0,
                "consumers_total": 0,
            },
            "checks": {
                "baseline_reachable": True,
                "source_support_restorable": True,
                "target_disable_rehearsed": True,
                "redirect_reversal_rehearsed": True,
                "alias_reversal_rehearsed": True,
                "consumer_recovery_rehearsed": True,
            },
        }

    def test_complete_rehearsal_passes_but_does_not_claim_portfolio_gate(self) -> None:
        result = rehearse_rollback(self.payload())
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["details"]["rehearsal_only"])
        self.assertFalse(result["details"]["mutation_performed"])
        self.assertEqual(result["details"]["portfolio_gate"], "not_run")
        self.assertEqual(result["details"]["failed_checks"], [])

    def test_failed_recovery_check_is_failed_not_blocked(self) -> None:
        payload = self.payload()
        payload["checks"]["source_support_restorable"] = False
        result = rehearse_rollback(payload)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["details"]["failed_checks"], ["source_support_restorable"])

    def test_active_redirects_aliases_and_consumers_add_reversal_steps(self) -> None:
        payload = self.payload()
        payload["candidate"].update(
            {
                "redirects_active": True,
                "aliases_active": True,
                "consumers_migrated": 2,
                "consumers_total": 3,
            }
        )
        result = rehearse_rollback(payload)
        self.assertEqual(result["status"], "passed")
        self.assertIn("reverse_redirects", result["details"]["ordered_steps"])
        self.assertIn("reverse_aliases", result["details"]["ordered_steps"])
        self.assertIn("restore_consumer_configuration", result["details"]["ordered_steps"])

    def test_same_baseline_and_candidate_is_blocked(self) -> None:
        payload = self.payload()
        payload["candidate"]["target_git_sha"] = payload["baseline"]["target_git_sha"]
        self.assertEqual(rehearse_rollback(payload)["status"], "blocked")

    def test_migrated_consumers_cannot_exceed_total(self) -> None:
        payload = self.payload()
        payload["candidate"]["consumers_migrated"] = 2
        payload["candidate"]["consumers_total"] = 1
        self.assertEqual(rehearse_rollback(payload)["status"], "blocked")


class MigrationCliTests(unittest.TestCase):
    def _run_file_command(self, command: str, payload: dict) -> tuple[int, dict]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main([command, "--input", str(path)])
        return exit_code, json.loads(output.getvalue())

    def test_consumers_cli(self) -> None:
        payload = ConsumerInventoryTests().payload()
        exit_code, result = self._run_file_command("consumers", payload)
        self.assertEqual(exit_code, 0)
        self.assertEqual(result["kind"], "consumer_inventory")

    def test_rollback_cli(self) -> None:
        payload = RollbackRehearsalTests().payload()
        exit_code, result = self._run_file_command("rollback", payload)
        self.assertEqual(exit_code, 0)
        self.assertEqual(result["kind"], "rollback_rehearsal")


if __name__ == "__main__":
    unittest.main()
