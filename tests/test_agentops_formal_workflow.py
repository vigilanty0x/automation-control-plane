from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "agentops-consumer-evidence.yml"
SOURCE_INVENTORY = ROOT / "docs" / "AGENTOPS_SOURCE_INVENTORY.json"


class FormalAgentOpsWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.inventory = json.loads(SOURCE_INVENTORY.read_text(encoding="utf-8"))

    def test_formal_evidence_is_manual_only(self) -> None:
        self.assertIn("on:\n  workflow_dispatch:", self.workflow)
        self.assertNotIn("\n  push:", self.workflow)
        self.assertNotIn("\n  pull_request:", self.workflow)

    def test_pilot_attestation_is_required_for_formal_run(self) -> None:
        self.assertRegex(
            self.workflow,
            r"pilot_manifest_json:\n(?:.*\n){0,3}\s+required: true",
        )
        self.assertIn("--pilot-manifest-env PUBLIC_PILOT_MANIFEST", self.workflow)

    def test_default_branch_lineage_is_fail_closed(self) -> None:
        self.assertIn("CURRENT_REF: ${{ github.ref_name }}", self.workflow)
        self.assertIn("DEFAULT_BRANCH: ${{ github.event.repository.default_branch }}", self.workflow)
        self.assertIn('if [[ "$CURRENT_REF" != "$DEFAULT_BRANCH" ]]', self.workflow)
        self.assertIn("Formal AgentOps evidence must run from the repository default branch.", self.workflow)

    def test_candidate_sha_is_bound_through_plan_and_packet(self) -> None:
        self.assertGreaterEqual(self.workflow.count("CANDIDATE_SHA: ${{ github.sha }}"), 2)
        self.assertIn('"candidate_sha": os.environ["CANDIDATE_SHA"]', self.workflow)
        self.assertIn('scripts/agentops_review_packet.py', self.workflow)
        self.assertIn('--candidate-sha "$CANDIDATE_SHA"', self.workflow)
        self.assertIn("technical-review-packet.json", self.workflow)

    def test_all_twelve_satellite_sources_are_sha_bound(self) -> None:
        sources = self.inventory.get("sources")
        self.assertIsInstance(sources, list)
        satellites = [item for item in sources if item.get("repository") != "automation-control-plane"]
        self.assertEqual(len(satellites), 12)
        for item in satellites:
            repository = item["repository"]
            git_sha = item["main_sha"]
            self.assertRegex(git_sha, r"^[0-9a-f]{40}$")
            self.assertIn(f"repository: ${{{{ github.repository_owner }}}}/{repository}", self.workflow)
            self.assertIn(f"ref: {git_sha}", self.workflow)

    def test_all_actions_are_commit_pinned(self) -> None:
        uses = re.findall(r"^\s*- uses:\s*([^\s]+)", self.workflow, flags=re.MULTILINE)
        self.assertGreater(len(uses), 0)
        for action in uses:
            self.assertRegex(action, r"^[^@]+@[0-9a-f]{40}$")

    def test_final_gate_requires_every_evidence_layer(self) -> None:
        for variable in (
            "SCAN_OUTCOME",
            "TRIAGE_OUTCOME",
            "PLAN_OUTCOME",
            "COMPATIBILITY_OUTCOME",
            "ADAPTER_OUTCOME",
            "CORE_OUTCOME",
            "PACKET_OUTCOME",
        ):
            self.assertIn(variable, self.workflow)
        self.assertIn(
            "No alias, consumer mutation, migration, release, retirement, or archive authorization is produced.",
            self.workflow,
        )


if __name__ == "__main__":
    unittest.main()
