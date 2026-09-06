from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "model_router", *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "router.sqlite3")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_route_outputs_explainable_json_without_database(self) -> None:
        result = run_cli(
            "route",
            "--agents",
            "examples/agents.json",
            "--task",
            "examples/task.json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["selected_agent_id"], "forge")
        self.assertFalse(payload["rejected"])
        self.assertTrue(payload["explanations"])

    def test_enqueue_and_inspect_round_trip(self) -> None:
        enqueued = run_cli(
            "enqueue",
            "--db",
            self.db,
            "--agents",
            "examples/agents.json",
            "--task",
            "examples/task.json",
        )
        self.assertEqual(enqueued.returncode, 0, enqueued.stderr)
        mission_id = json.loads(enqueued.stdout)["mission"]["mission_id"]
        inspected = run_cli("inspect", "--db", self.db, "--mission", mission_id)
        self.assertEqual(inspected.returncode, 0, inspected.stderr)
        payload = json.loads(inspected.stdout)
        self.assertEqual(payload["mission"]["mission_id"], mission_id)
        self.assertEqual(payload["events"][0]["to_state"], "queued")

    def test_duplicate_enqueue_returns_same_mission(self) -> None:
        command = (
            "enqueue",
            "--db",
            self.db,
            "--agents",
            "examples/agents.json",
            "--task",
            "examples/task.json",
        )
        first = json.loads(run_cli(*command).stdout)
        second = json.loads(run_cli(*command).stdout)
        self.assertEqual(first["mission"]["mission_id"], second["mission"]["mission_id"])
        self.assertFalse(second["created"])

    def test_claim_then_complete_with_evidence(self) -> None:
        enqueued = json.loads(
            run_cli(
                "enqueue",
                "--db",
                self.db,
                "--agents",
                "examples/agents.json",
                "--task",
                "examples/task.json",
            ).stdout
        )
        mission_id = enqueued["mission"]["mission_id"]
        claimed = run_cli("claim", "--db", self.db, "--worker", "cli-worker")
        self.assertEqual(json.loads(claimed.stdout)["state"], "running")
        completed = run_cli(
            "transition",
            "--db",
            self.db,
            "--mission",
            mission_id,
            "--to",
            "done",
            "--actor",
            "cli-worker",
            "--reason",
            "all gates passed",
            "--evidence",
            "examples/evidence.json",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["state"], "done")

    def test_done_without_evidence_returns_bounded_error(self) -> None:
        enqueued = json.loads(
            run_cli(
                "enqueue",
                "--db",
                self.db,
                "--agents",
                "examples/agents.json",
                "--task",
                "examples/task.json",
            ).stdout
        )
        mission_id = enqueued["mission"]["mission_id"]
        run_cli("claim", "--db", self.db, "--worker", "cli-worker")
        result = run_cli(
            "transition",
            "--db",
            self.db,
            "--mission",
            mission_id,
            "--to",
            "done",
            "--actor",
            "cli-worker",
            "--reason",
            "claiming done",
        )
        self.assertEqual(result.returncode, 4)
        self.assertEqual(json.loads(result.stderr)["error"], "invalid_transition")

    def test_unknown_mission_returns_not_found_exit_code(self) -> None:
        result = run_cli("inspect", "--db", self.db, "--mission", "missing")
        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stderr)["error"], "mission_not_found")

    def test_invalid_input_file_returns_validation_error(self) -> None:
        bad = Path(self.tmp.name) / "bad.json"
        bad.write_text("not json", encoding="utf-8")
        result = run_cli("route", "--agents", str(bad), "--task", "examples/task.json")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)["error"], "invalid_input")

    def test_metrics_command_reports_operational_measures(self) -> None:
        run_cli(
            "enqueue",
            "--db",
            self.db,
            "--agents",
            "examples/agents.json",
            "--task",
            "examples/task.json",
        )
        result = run_cli("metrics", "--db", self.db)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["total_missions"], 1)
        self.assertIn("pass_at_1", payload)
        self.assertIn("average_retries_per_task", payload)
        self.assertIn("average_wall_time_seconds", payload)

    def test_recover_command_exposes_expired_worker_lease(self) -> None:
        enqueued = json.loads(
            run_cli(
                "enqueue",
                "--db",
                self.db,
                "--agents",
                "examples/agents.json",
                "--task",
                "examples/task.json",
            ).stdout
        )
        mission_id = enqueued["mission"]["mission_id"]
        run_cli(
            "claim",
            "--db",
            self.db,
            "--worker",
            "lost-worker",
            "--lease-seconds",
            "1",
        )
        result = run_cli(
            "recover",
            "--db",
            self.db,
            "--actor",
            "lease-reaper",
            "--now",
            "2999-01-01T00:00:00+00:00",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload[0]["mission_id"], mission_id)
        self.assertEqual(payload[0]["state"], "failed")

    def test_demo_is_reproducible_and_completes_one_mission(self) -> None:
        result = run_cli("demo", "--db", self.db)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["mission"]["state"], "done")
        self.assertEqual(payload["metrics"]["done_missions"], 1)
        self.assertGreaterEqual(len(payload["events"]), 3)

    def test_list_can_filter_by_state(self) -> None:
        run_cli(
            "enqueue",
            "--db",
            self.db,
            "--agents",
            "examples/agents.json",
            "--task",
            "examples/task.json",
        )
        result = run_cli("list", "--db", self.db, "--state", "queued")
        payload = json.loads(result.stdout)
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["state"], "queued")


if __name__ == "__main__":
    unittest.main()
