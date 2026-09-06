from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_worktrees.cli import main


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.name", "Synthetic Tester")
        git(self.repo, "config", "user.email", "tester@example.test")
        (self.repo / "README.md").write_text("fixture\n", encoding="utf-8")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-m", "initial")
        self.db = self.root / "state.sqlite3"
        self.worktrees = self.root / "worktrees"
        self.request = self.root / "request.json"
        self.request.write_text(
            json.dumps(
                {
                    "task_id": "task-1",
                    "idempotency_key": "idem-1",
                    "agent_id": "agent-a",
                    "owner": "team-a",
                    "owned_paths": ["src/feature"],
                    "acceptance_criteria": ["tests pass"],
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_cli(self, *args: str) -> tuple[int, dict[str, object]]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(list(args))
        stream = stdout.getvalue() if code == 0 else stderr.getvalue()
        return code, json.loads(stream)

    def register(self) -> dict[str, object]:
        code, payload = self.run_cli(
            "register",
            "--db",
            str(self.db),
            "--repo",
            str(self.repo),
            "--worktree-root",
            str(self.worktrees),
            "--request",
            str(self.request),
        )
        self.assertEqual(code, 0)
        return payload

    def test_register_and_provision_round_trip(self) -> None:
        registered = self.register()
        code, provisioned = self.run_cli(
            "provision",
            "--db",
            str(self.db),
            "--mission",
            registered["mission"]["mission_id"],
            "--actor",
            "scheduler",
        )
        self.assertEqual(code, 0)
        self.assertEqual(provisioned["state"], "running")
        self.assertTrue(Path(provisioned["worktree_path"]).is_dir())

    def test_duplicate_register_reports_created_false(self) -> None:
        self.register()
        second = self.register()
        self.assertFalse(second["created"])

    def test_invalid_json_returns_bounded_error(self) -> None:
        malformed = self.root / "bad.json"
        malformed.write_text("{", encoding="utf-8")
        code, payload = self.run_cli(
            "register",
            "--db",
            str(self.db),
            "--repo",
            str(self.repo),
            "--worktree-root",
            str(self.worktrees),
            "--request",
            str(malformed),
        )
        self.assertEqual(code, 2)
        self.assertEqual(payload["error"], "invalid-input")

    def test_unknown_mission_has_distinct_exit_code(self) -> None:
        code, payload = self.run_cli(
            "inspect",
            "--db",
            str(self.db),
            "--mission",
            "missing",
        )
        self.assertEqual(code, 3)
        self.assertEqual(payload["error"], "mission-not-found")

    def test_demo_creates_merges_and_cleans_synthetic_worktree(self) -> None:
        workspace = self.root / "demo"
        code, payload = self.run_cli("demo", "--workspace", str(workspace))
        self.assertEqual(code, 0)
        self.assertEqual(payload["mission"]["state"], "done")
        self.assertTrue(payload["mission"]["cleaned_at"])
        self.assertEqual(payload["audit"]["unmanaged"], [])
        self.assertEqual(payload["metrics"]["cleaned_missions"], 1)


if __name__ == "__main__":
    unittest.main()
