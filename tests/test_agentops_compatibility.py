from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import unittest

from automation_control_plane.agentops import compatibility_inventory
from automation_control_plane.agentops.cli import main
from automation_control_plane.agentops.compatibility import SOURCE_INTERFACES
from automation_control_plane.agentops.inventory import SOURCE_INVENTORY


class CompatibilityInventoryTests(unittest.TestCase):
    def test_exact_thirteen_source_interfaces_are_sha_bound(self) -> None:
        expected = {item["repository"]: item["main_sha"] for item in SOURCE_INVENTORY}
        observed = {item["repository"]: item["source_sha"] for item in SOURCE_INTERFACES}
        self.assertEqual(len(SOURCE_INTERFACES), 13)
        self.assertEqual(observed, expected)

    def test_source_identity_fields_are_unique_and_nonempty(self) -> None:
        repositories = [item["repository"] for item in SOURCE_INTERFACES]
        source_clis = [item["source_cli"] for item in SOURCE_INTERFACES]
        self.assertEqual(len(repositories), len(set(repositories)))
        self.assertEqual(len(source_clis), len(set(source_clis)))
        for item in SOURCE_INTERFACES:
            self.assertEqual(len(item["source_sha"]), 40)
            self.assertEqual(len(item["pyproject_blob_sha"]), 40)
            self.assertTrue(item["project_name"])
            self.assertTrue(item["version"])
            self.assertTrue(item["requires_python"])
            self.assertTrue(item["source_entrypoint"])
            self.assertTrue(item["import_root"])
            self.assertTrue(item["target_module"])

    def test_import_root_is_bound_to_observed_entrypoint(self) -> None:
        for item in SOURCE_INTERFACES:
            entry_module = item["source_entrypoint"].split(":", 1)[0]
            self.assertEqual(item["import_root"], entry_module.split(".", 1)[0])

    def test_no_legacy_alias_is_activated(self) -> None:
        states = {item["legacy_alias_state"] for item in SOURCE_INTERFACES}
        self.assertEqual(states, {"not_activated", "not_applicable"})
        self.assertFalse(compatibility_inventory()["details"]["legacy_aliases_activated"])

    def test_taskgraph_distribution_name_is_preserved_verbatim(self) -> None:
        record = next(item for item in SOURCE_INTERFACES if item["repository"] == "taskgraph")
        self.assertEqual(record["project_name"], "taskgraph-agents")
        self.assertEqual(record["source_cli"], "taskgraph")
        self.assertEqual(record["import_root"], "taskgraph")

    def test_context_budgeter_cli_is_not_guessed_from_repository_name(self) -> None:
        record = next(item for item in SOURCE_INTERFACES if item["repository"] == "context-window-budgeter")
        self.assertEqual(record["source_cli"], "context-budget")
        self.assertEqual(record["source_entrypoint"], "context_window_budgeter:main")

    def test_inventory_passes_but_migration_gate_remains_not_run(self) -> None:
        result = compatibility_inventory()
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["details"]["source_sha_match"])
        self.assertFalse(result["details"]["migration_performed"])
        self.assertEqual(result["details"]["compatibility_gate"], "not_run")

    def test_cli_is_deterministic_and_has_no_input(self) -> None:
        first = io.StringIO()
        second = io.StringIO()
        with redirect_stdout(first):
            first_code = main(["compatibility"])
        with redirect_stdout(second):
            second_code = main(["compatibility"])
        self.assertEqual(first_code, 0)
        self.assertEqual(second_code, 0)
        self.assertEqual(first.getvalue(), second.getvalue())
        payload = json.loads(first.getvalue())
        self.assertEqual(payload["kind"], "compatibility_inventory")
        self.assertEqual(payload["details"]["interface_count"], 13)
        self.assertFalse(payload["details"]["legacy_aliases_activated"])


if __name__ == "__main__":
    unittest.main()
