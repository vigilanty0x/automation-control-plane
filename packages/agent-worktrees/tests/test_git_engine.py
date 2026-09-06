from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_worktrees.engine import AgentWorktreeService, GitCommandError, SafetyError
from agent_worktrees.models import EvidenceBundle, MissionRequest, MissionState
from agent_worktrees.store import SQLiteMissionStore


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def request(key: str = "idem-1") -> MissionRequest:
    return MissionRequest(
        task_id=f"task-{key}",
        idempotency_key=key,
        agent_id="agent-a",
        owner="team-a",
        owned_paths=("src/feature", "artifacts/report.json"),
        acceptance_criteria=("tests pass", "report exists"),
        max_attempts=2,
    )


class GitEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.repo = root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.name", "Synthetic Tester")
        git(self.repo, "config", "user.email", "tester@example.test")
        (self.repo / "README.md").write_text("# fixture\n", encoding="utf-8")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-m", "initial fixture")
        self.worktrees = root / "worktrees"
        self.store = SQLiteMissionStore(root / "state.sqlite3")
        self.service = AgentWorktreeService(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def register(self, item: MissionRequest | None = None):
        return self.service.register(
            item or request(),
            repo=self.repo,
            worktree_root=self.worktrees,
        )[0]

    def commit_feature(self, mission_id: str) -> tuple[str, EvidenceBundle]:
        record = self.store.get(mission_id)
        worktree = Path(record.worktree_path)
        artifact = worktree / "artifacts" / "report.json"
        artifact.parent.mkdir(parents=True)
        artifact.write_text(json.dumps({"result": "pass"}), encoding="utf-8")
        feature = worktree / "src" / "feature" / "result.txt"
        feature.parent.mkdir(parents=True)
        feature.write_text("complete\n", encoding="utf-8")
        git(worktree, "add", "artifacts/report.json", "src/feature/result.txt")
        git(worktree, "commit", "-m", "complete synthetic mission")
        sha = git(worktree, "rev-parse", "HEAD")
        evidence = EvidenceBundle(
            commit_sha=sha,
            tests=("python -m unittest:pass",),
            artifacts=("artifacts/report.json",),
            criteria={"tests pass": True, "report exists": True},
            produced_by="agent-a",
        )
        return sha, evidence

    def test_register_requires_a_git_repository(self) -> None:
        invalid = Path(self.tmp.name) / "not-a-repo"
        invalid.mkdir()
        with self.assertRaises(GitCommandError):
            self.service.register(request(), repo=invalid, worktree_root=self.worktrees)

    def test_register_rejects_worktree_root_inside_primary_repository(self) -> None:
        with self.assertRaisesRegex(SafetyError, "outside"):
            self.service.register(
                request(),
                repo=self.repo,
                worktree_root=self.repo / ".worktrees",
            )

    def test_provision_creates_isolated_branch_and_is_idempotent(self) -> None:
        mission = self.register()
        first = self.service.provision(mission.mission_id, actor="scheduler")
        second = self.service.provision(mission.mission_id, actor="scheduler")
        self.assertEqual(first.state, MissionState.RUNNING)
        self.assertEqual(second.worktree_path, first.worktree_path)
        self.assertTrue(Path(first.worktree_path).is_dir())
        self.assertEqual(git(Path(first.worktree_path), "branch", "--show-current"), first.branch)
        listed = git(self.repo, "worktree", "list", "--porcelain")
        self.assertEqual(listed.count(f"worktree {first.worktree_path}"), 1)

    def test_complete_rejects_dirty_tree_wrong_producer_and_missing_artifact(self) -> None:
        mission = self.register()
        running = self.service.provision(mission.mission_id, actor="scheduler")
        worktree = Path(running.worktree_path)
        (worktree / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        invalid = EvidenceBundle(
            commit_sha=git(worktree, "rev-parse", "HEAD"),
            tests=("unit:pass",),
            artifacts=("missing.json",),
            criteria={"tests pass": True, "report exists": True},
            produced_by="someone-else",
        )
        with self.assertRaisesRegex(SafetyError, "clean"):
            self.service.complete(mission.mission_id, invalid, actor="agent-a")
        (worktree / "dirty.txt").unlink()
        with self.assertRaisesRegex(SafetyError, "producer"):
            self.service.complete(mission.mission_id, invalid, actor="agent-a")
        corrected = EvidenceBundle.from_dict({**invalid.to_dict(), "produced_by": "agent-a"})
        with self.assertRaisesRegex(SafetyError, "artifact"):
            self.service.complete(mission.mission_id, corrected, actor="agent-a")

    def test_cleanup_refuses_unmerged_then_removes_merged_work(self) -> None:
        mission = self.register()
        running = self.service.provision(mission.mission_id, actor="scheduler")
        sha, evidence = self.commit_feature(mission.mission_id)
        done = self.service.complete(mission.mission_id, evidence, actor="agent-a")
        self.assertEqual(done.state, MissionState.DONE)
        with self.assertRaisesRegex(SafetyError, "not integrated"):
            self.service.cleanup(mission.mission_id, actor="integrator")
        git(self.repo, "merge", "--ff-only", running.branch)
        cleaned = self.service.cleanup(mission.mission_id, actor="integrator")
        self.assertTrue(cleaned.cleaned)
        self.assertFalse(Path(running.worktree_path).exists())
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), sha)
        branch_check = subprocess.run(
            ["git", "show-ref", "--verify", f"refs/heads/{running.branch}"],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(branch_check.returncode, 0)

    def test_complete_rejects_committed_changes_outside_owned_paths(self) -> None:
        mission = self.register()
        running = self.service.provision(mission.mission_id, actor="scheduler")
        worktree = Path(running.worktree_path)
        artifact = worktree / "artifacts" / "report.json"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("{}\n", encoding="utf-8")
        outside = worktree / "docs" / "outside.txt"
        outside.parent.mkdir(parents=True)
        outside.write_text("outside\n", encoding="utf-8")
        git(worktree, "add", "artifacts/report.json", "docs/outside.txt")
        git(worktree, "commit", "-m", "change outside ownership")
        evidence = EvidenceBundle(
            commit_sha=git(worktree, "rev-parse", "HEAD"),
            tests=("unit:pass",),
            artifacts=("artifacts/report.json",),
            criteria={"tests pass": True, "report exists": True},
            produced_by="agent-a",
        )
        with self.assertRaisesRegex(SafetyError, "outside mission ownership"):
            self.service.complete(mission.mission_id, evidence, actor="agent-a")

    def test_cleanup_accepts_squash_equivalent_owned_tree(self) -> None:
        mission = self.register()
        running = self.service.provision(mission.mission_id, actor="scheduler")
        _, evidence = self.commit_feature(mission.mission_id)
        self.service.complete(mission.mission_id, evidence, actor="agent-a")
        git(self.repo, "merge", "--squash", running.branch)
        git(self.repo, "commit", "-m", "integrate mission as squash")
        cleaned = self.service.cleanup(
            mission.mission_id,
            actor="integrator",
            keep_branch=True,
        )
        self.assertTrue(cleaned.cleaned)
        self.assertTrue(self.service.branch_exists(self.repo, running.branch))

    def test_retry_reuses_existing_branch_and_worktree(self) -> None:
        mission = self.register()
        running = self.service.provision(mission.mission_id, actor="scheduler")
        self.service.fail(mission.mission_id, actor="agent-a", reason="bounded test failure")
        retried = self.service.retry(mission.mission_id, actor="scheduler")
        resumed = self.service.provision(mission.mission_id, actor="scheduler")
        self.assertEqual(retried.attempt, 2)
        self.assertEqual(resumed.branch, running.branch)
        self.assertEqual(resumed.worktree_path, running.worktree_path)

    def test_recovery_exposes_missing_worktree_as_failure_once(self) -> None:
        mission = self.register()
        running = self.service.provision(mission.mission_id, actor="scheduler")
        git(self.repo, "worktree", "remove", running.worktree_path)
        recovered = self.service.recover(actor="reaper")
        self.assertEqual([item.mission_id for item in recovered], [mission.mission_id])
        self.assertEqual(recovered[0].state, MissionState.FAILED)
        self.assertEqual(self.service.recover(actor="reaper"), [])

    def test_audit_distinguishes_managed_and_unmanaged_worktrees(self) -> None:
        mission = self.register()
        running = self.service.provision(mission.mission_id, actor="scheduler")
        extra = Path(self.tmp.name) / "unmanaged"
        git(self.repo, "worktree", "add", "-b", "manual/unmanaged", str(extra), "main")
        audit = self.service.audit(repo=self.repo)
        self.assertEqual(audit["managed"][0]["mission_id"], mission.mission_id)
        self.assertIn(str(extra.resolve()), audit["unmanaged"])
        self.assertNotIn(str(Path(running.worktree_path).resolve()), audit["unmanaged"])


if __name__ == "__main__":
    unittest.main()
