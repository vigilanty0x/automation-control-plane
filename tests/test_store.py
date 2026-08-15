from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_worktrees.models import EvidenceBundle, MissionRequest, MissionState
from agent_worktrees.state_machine import InvalidTransition
from agent_worktrees.store import MissionNotFound, SQLiteMissionStore


def request(
    *,
    key: str = "idem-1",
    agent: str = "agent-a",
    paths: tuple[str, ...] = ("src/api",),
) -> MissionRequest:
    return MissionRequest(
        task_id=f"task-{key}",
        idempotency_key=key,
        agent_id=agent,
        owner=f"team-{agent}",
        owned_paths=paths,
        acceptance_criteria=("tests pass",),
        max_attempts=2,
    )


def proof() -> EvidenceBundle:
    return EvidenceBundle(
        commit_sha="a" * 40,
        tests=("unit:pass",),
        artifacts=("artifacts/report.json",),
        criteria={"tests pass": True},
        produced_by="agent-a",
    )


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.store = SQLiteMissionStore(root / "state.sqlite3")
        self.repo = root / "repo"
        self.worktrees = root / "worktrees"

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def register(self, item: MissionRequest | None = None):
        return self.store.register(
            item or request(),
            repo_root=self.repo,
            worktree_root=self.worktrees,
        )

    def test_register_is_idempotent_and_branch_is_safe(self) -> None:
        first, first_created = self.register()
        second, second_created = self.register()
        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first.mission_id, second.mission_id)
        self.assertEqual(first.branch, second.branch)
        self.assertTrue(first.branch.startswith("agent/agent-a/"))
        self.assertEqual(Path(first.worktree_path).parent.parent, self.worktrees)

    def test_duplicate_key_with_changed_payload_is_rejected(self) -> None:
        self.register()
        with self.assertRaisesRegex(ValueError, "idempotency conflict"):
            self.register(request(paths=("docs",)))

    def test_overlapping_path_is_persisted_as_rejected(self) -> None:
        accepted, _ = self.register(request(key="one", paths=("src/api",)))
        rejected, created = self.register(request(key="two", paths=("src/api/client.py",)))
        self.assertTrue(created)
        self.assertEqual(rejected.state, MissionState.REJECTED)
        self.assertIn(accepted.mission_id, rejected.last_error or "")
        self.assertIn("src/api", rejected.last_error or "")

    def test_sibling_paths_do_not_conflict(self) -> None:
        first, _ = self.register(request(key="one", paths=("src/api",)))
        second, _ = self.register(request(key="two", paths=("src/web",)))
        self.assertEqual(first.state, MissionState.QUEUED)
        self.assertEqual(second.state, MissionState.QUEUED)

    def test_retry_preserves_mission_branch_and_worktree(self) -> None:
        mission, _ = self.register()
        self.store.transition(mission.mission_id, MissionState.RUNNING, actor="engine", reason="provisioned")
        failed = self.store.transition(
            mission.mission_id,
            MissionState.FAILED,
            actor="agent-a",
            reason="tests failed",
        )
        retried = self.store.retry(mission.mission_id, actor="scheduler")
        self.assertEqual(retried.state, MissionState.QUEUED)
        self.assertEqual(retried.attempt, 2)
        self.assertEqual(retried.branch, failed.branch)
        self.assertEqual(retried.worktree_path, failed.worktree_path)

    def test_done_requires_declared_evidence(self) -> None:
        mission, _ = self.register()
        self.store.transition(mission.mission_id, MissionState.RUNNING, actor="engine", reason="provisioned")
        done = self.store.transition(
            mission.mission_id,
            MissionState.DONE,
            actor="agent-a",
            reason="all gates passed",
            evidence=proof(),
        )
        self.assertEqual(done.evidence, proof())
        self.assertEqual(self.store.events(mission.mission_id)[-1]["to_state"], "done")

    def test_mark_cleaned_releases_ownership(self) -> None:
        mission, _ = self.register(request(key="one"))
        self.store.transition(mission.mission_id, MissionState.RUNNING, actor="engine", reason="provisioned")
        self.store.transition(
            mission.mission_id,
            MissionState.DONE,
            actor="agent-a",
            reason="done",
            evidence=proof(),
        )
        cleaned = self.store.mark_cleaned(mission.mission_id, actor="engine")
        self.assertTrue(cleaned.cleaned)
        replacement, _ = self.register(request(key="two", paths=("src/api/client.py",)))
        self.assertEqual(replacement.state, MissionState.QUEUED)

    def test_mark_cleaned_requires_done(self) -> None:
        mission, _ = self.register()
        with self.assertRaises(InvalidTransition):
            self.store.mark_cleaned(mission.mission_id, actor="engine")

    def test_intervention_and_metrics_are_durable(self) -> None:
        mission, _ = self.register()
        self.store.record_intervention(mission.mission_id, actor="reviewer", reason="scope approved")
        metrics = self.store.metrics()
        self.assertEqual(metrics["total_missions"], 1)
        self.assertEqual(metrics["human_interventions"], 1)
        self.store.close()
        self.store = SQLiteMissionStore(Path(self.tmp.name) / "state.sqlite3")
        self.assertEqual(self.store.metrics()["human_interventions"], 1)

    def test_unknown_mission_is_explicit(self) -> None:
        with self.assertRaises(MissionNotFound):
            self.store.get("missing")


if __name__ == "__main__":
    unittest.main()
