from __future__ import annotations

import io
import tarfile
import unittest
from unittest.mock import patch

from scripts import agentops_consumer_scan as scan


def _tarball(files: dict[str, str]) -> io.BytesIO:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for path, text in files.items():
            payload = text.encode("utf-8")
            info = tarfile.TarInfo(name=f"synthetic-prefix/{path}")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    output.seek(0)
    return output


class PublicArchiveScannerTests(unittest.TestCase):
    def aliases(self) -> dict[str, tuple[str, ...]]:
        return {
            "agentmesh": scan._aliases("agentmesh"),
            "agent-budgeter": scan._aliases("agent-budgeter"),
        }

    def test_scans_workflow_package_import_and_documentation_references(self) -> None:
        archive = _tarball(
            {
                ".github/workflows/ci.yml": "run: agentmesh record.json\n",
                "pyproject.toml": 'dependency = "agent-budgeter"\n',
                "src/app.py": "import agentmesh\n",
                "docs/migration.md": "Migrate away from agent-budgeter.\n",
            }
        )
        with patch.object(scan, "_open_public_tarball", return_value=archive):
            found = scan._scan_archive(
                "public-owner",
                "consumer-repo",
                "1" * 40,
                self.aliases(),
                "",
            )

        agentmesh_kinds = sorted(item["kind"] for item in found["agentmesh"])
        budgeter_kinds = sorted(item["kind"] for item in found["agent-budgeter"])
        self.assertEqual(agentmesh_kinds, ["import", "workflow"])
        self.assertEqual(budgeter_kinds, ["documentation", "package"])
        for items in found.values():
            for item in items:
                self.assertIn("@" + "1" * 40 + "/", item["evidence"])
                self.assertEqual(item["consumer"], "consumer-repo")

    def test_source_repository_does_not_count_its_own_name_as_consumer(self) -> None:
        archive = _tarball({"README.md": "agentmesh agentmesh\n"})
        with patch.object(scan, "_open_public_tarball", return_value=archive):
            found = scan._scan_archive(
                "public-owner",
                "agentmesh",
                "2" * 40,
                self.aliases(),
                "",
            )
        self.assertNotIn("agentmesh", found)

    def test_duplicate_aliases_on_one_line_emit_one_reference(self) -> None:
        archive = _tarball({"src/app.py": "agent-budgeter agent_budgeter agentbudgeter\n"})
        with patch.object(scan, "_open_public_tarball", return_value=archive):
            found = scan._scan_archive(
                "public-owner",
                "consumer-repo",
                "3" * 40,
                self.aliases(),
                "",
            )
        self.assertEqual(len(found["agent-budgeter"]), 1)
        self.assertEqual(found["agent-budgeter"][0]["kind"], "import")


if __name__ == "__main__":
    unittest.main()
