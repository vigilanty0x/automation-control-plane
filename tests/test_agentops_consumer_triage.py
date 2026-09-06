from __future__ import annotations

import unittest

from scripts import agentops_consumer_triage as triage


class EvidenceReferenceTests(unittest.TestCase):
    def test_parses_sha_bound_line_reference(self) -> None:
        parsed = triage.parse_evidence_ref(
            "github://owner/repo@" + "a" * 40 + "/src/app.py#L12"
        )
        self.assertEqual(parsed["owner"], "owner")
        self.assertEqual(parsed["repo"], "repo")
        self.assertEqual(parsed["path"], "src/app.py")
        self.assertEqual(parsed["line"], 12)

    def test_rejects_unbound_or_unsafe_reference(self) -> None:
        with self.assertRaises(triage.TriageError):
            triage.parse_evidence_ref("https://example.invalid/repo/src/app.py#L1")
        with self.assertRaises(triage.TriageError):
            triage.parse_evidence_ref(
                "github://owner/repo@" + "a" * 40 + "/../secret.py#L1"
            )


class ImportSyntaxTests(unittest.TestCase):
    def test_python_imports_are_verified(self) -> None:
        aliases = triage._aliases("automation-control-plane")
        self.assertTrue(
            triage.is_strong_import_reference(
                "src/app.py", "import automation_control_plane", aliases
            )
        )
        self.assertTrue(
            triage.is_strong_import_reference(
                "src/app.py", "from automation_control_plane import ControlPlane", aliases
            )
        )

    def test_static_sibling_registry_is_only_a_code_mention(self) -> None:
        aliases = triage._aliases("automation-control-plane")
        self.assertFalse(
            triage.is_strong_import_reference(
                "scripts/check.py", '    "automation_control_plane", "workflow_templates",', aliases
            )
        )

    def test_javascript_require_and_from_are_verified(self) -> None:
        aliases = triage._aliases("automation-control-plane")
        self.assertTrue(
            triage.is_strong_import_reference(
                "src/app.js", 'const cp = require("automation-control-plane")', aliases
            )
        )
        self.assertTrue(
            triage.is_strong_import_reference(
                "src/app.ts", 'import cp from "automation-control-plane"', aliases
            )
        )

    def test_comments_do_not_become_import_evidence(self) -> None:
        aliases = triage._aliases("automation-control-plane")
        self.assertFalse(
            triage.is_strong_import_reference(
                "src/app.py", "# import automation_control_plane", aliases
            )
        )
        self.assertFalse(
            triage.is_strong_import_reference(
                "src/app.js", "// import automation-control-plane", aliases
            )
        )


class InventoryTriageTests(unittest.TestCase):
    def inventory(self) -> dict:
        sha = "b" * 40
        return {
            "sources": [
                {
                    "repository": "automation-control-plane",
                    "references": [
                        {
                            "consumer": "consumer-a",
                            "kind": "import",
                            "evidence": f"github://owner/consumer-a@{sha}/src/app.py#L1",
                        },
                        {
                            "consumer": "consumer-b",
                            "kind": "import",
                            "evidence": f"github://owner/consumer-b@{sha}/scripts/check.py#L2",
                        },
                        {
                            "consumer": "docs-only",
                            "kind": "documentation",
                            "evidence": f"github://owner/docs-only@{sha}/README.md#L1",
                        },
                    ],
                }
            ]
        }

    def test_separates_verified_imports_from_code_mentions(self) -> None:
        files = {
            ("consumer-a", "src/app.py"): "import automation_control_plane\n",
            ("consumer-b", "scripts/check.py"): "SIBLING_MODULES = {\n    \"automation_control_plane\",\n}\n",
        }

        result = triage.triage_inventory(
            self.inventory(),
            fetch_file=lambda owner, repo, git_sha, path: files[(repo, path)],
        )
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["import_candidates"], 2)
        self.assertEqual(result["verified_imports"], 1)
        self.assertEqual(result["code_mentions"], 1)
        self.assertEqual(result["unresolved"], 0)
        self.assertFalse(result["mutation_performed"])
        self.assertFalse(result["migration_authorized"])

    def test_fetch_failure_stays_unresolved_and_fails_closed(self) -> None:
        def fail(owner: str, repo: str, git_sha: str, path: str) -> str:
            raise triage.TriageError("synthetic fetch failure")

        result = triage.triage_inventory(self.inventory(), fetch_file=fail)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["unresolved"], 2)
        self.assertEqual(result["verified_imports"], 0)


if __name__ == "__main__":
    unittest.main()
