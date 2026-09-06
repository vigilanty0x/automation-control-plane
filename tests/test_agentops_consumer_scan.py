from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import agentops_consumer_scan as scan


class PathClassificationTests(unittest.TestCase):
    def test_workflow_wins_over_yaml_documentation(self) -> None:
        self.assertEqual(scan.classify_path(".github/workflows/ci.yml"), "workflow")

    def test_package_manifests_are_package_references(self) -> None:
        self.assertEqual(scan.classify_path("pyproject.toml"), "package")
        self.assertEqual(scan.classify_path("requirements-dev.txt"), "package")

    def test_code_and_docs_are_separated(self) -> None:
        self.assertEqual(scan.classify_path("src/example.py"), "import")
        self.assertEqual(scan.classify_path("docs/migration.md"), "documentation")

    def test_binary_extension_is_ignored(self) -> None:
        self.assertIsNone(scan.classify_path("assets/logo.png"))


class AliasTests(unittest.TestCase):
    def test_aliases_include_repo_and_import_style_names(self) -> None:
        aliases = scan._aliases("agent-budgeter")
        self.assertIn("agent-budgeter", aliases)
        self.assertIn("agent_budgeter", aliases)
        self.assertIn("agentbudgeter", aliases)


class SourceInventoryTests(unittest.TestCase):
    def test_requires_exactly_thirteen_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            path.write_text(
                json.dumps(
                    {
                        "sources": [
                            {
                                "repository": f"source-{index}",
                                "main_sha": f"{index:040x}"[-40:],
                            }
                            for index in range(12)
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(scan.ScanError):
                scan._load_source_inventory(str(path))


class PilotManifestTests(unittest.TestCase):
    def test_missing_manifest_is_incomplete_not_zero_claim(self) -> None:
        complete, entries = scan._pilot_manifest_from_env(None, {"agentmesh"})
        self.assertFalse(complete)
        self.assertEqual(entries, [])

    def test_explicit_complete_manifest_is_accepted(self) -> None:
        payload = {
            "schema_version": 1,
            "complete": True,
            "pilots": [
                {
                    "source": "agentmesh",
                    "consumer": "public-pilot",
                    "evidence": "https://example.invalid/public-evidence",
                }
            ],
        }
        with patch.dict(os.environ, {"PUBLIC_PILOTS": json.dumps(payload)}, clear=False):
            complete, entries = scan._pilot_manifest_from_env("PUBLIC_PILOTS", {"agentmesh"})
        self.assertTrue(complete)
        self.assertEqual(entries[0][0], "agentmesh")
        self.assertEqual(entries[0][1]["kind"], "pilot")

    def test_unknown_source_is_rejected(self) -> None:
        payload = {
            "schema_version": 1,
            "complete": True,
            "pilots": [
                {
                    "source": "unknown",
                    "consumer": "public-pilot",
                    "evidence": "https://example.invalid/public-evidence",
                }
            ],
        }
        with patch.dict(os.environ, {"PUBLIC_PILOTS": json.dumps(payload)}, clear=False):
            with self.assertRaises(scan.ScanError):
                scan._pilot_manifest_from_env("PUBLIC_PILOTS", {"agentmesh"})


class PublicEnumerationTests(unittest.TestCase):
    def test_private_repository_is_rejected_even_if_endpoint_misbehaves(self) -> None:
        responses = [
            [
                {"name": "public-repo", "default_branch": "main", "private": False},
                {"name": "private-repo", "default_branch": "main", "private": True},
            ]
        ]
        with patch.object(scan, "_request_json", side_effect=responses):
            with self.assertRaises(scan.ScanError):
                scan._public_repositories("owner", "")

    def test_public_repository_page_is_accepted(self) -> None:
        responses = [[{"name": "public-repo", "default_branch": "main", "private": False}]]
        with patch.object(scan, "_request_json", side_effect=responses):
            repos = scan._public_repositories("owner", "")
        self.assertEqual(repos, [{"name": "public-repo", "default_branch": "main"}])


class MarkdownTests(unittest.TestCase):
    def test_report_keeps_gate_semantics_explicit(self) -> None:
        receipt = {
            "status": "failed",
            "details": {
                "reference_count": 2,
                "unique_consumer_count": 1,
                "coverage_complete": False,
                "kind_counts": {"import": 2},
            },
        }
        report = scan._markdown_report(
            receipt,
            owner="owner",
            public_count=2,
            scanned_count=2,
            source_sha_drift=[],
            inventory_sha256="1" * 64,
            receipt_sha256="2" * 64,
        )
        self.assertIn("does not authorize migration", report)
        self.assertIn("rather than silently assuming zero pilots", report)


if __name__ == "__main__":
    unittest.main()
