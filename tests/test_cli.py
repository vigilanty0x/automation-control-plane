from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class CLITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = dict(os.environ)
        source = str(Path(__file__).parents[1] / "src")
        self.env["PYTHONPATH"] = source + os.pathsep + self.env.get("PYTHONPATH", "")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "apprentice_ai", "--data-dir", str(self.root), *arguments],
            capture_output=True,
            text=True,
            env=self.env,
            timeout=30,
            check=False,
        )

    def test_version_and_usage_errors_are_json(self) -> None:
        version = self.run_cli("version")
        self.assertEqual(version.returncode, 0, version.stderr)
        self.assertEqual(json.loads(version.stdout)["version"], "0.1.0")
        invalid = self.run_cli("timeline")
        self.assertEqual(invalid.returncode, 3)
        self.assertEqual(json.loads(invalid.stderr)["error"]["code"], "CLI_USAGE")

    def test_init_ingest_and_verify_are_observable(self) -> None:
        initialized = self.run_cli("init", "--name", "CLI fixture")
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        profile = json.loads(initialized.stdout)["profile_id"]
        source = self.root / "event.jsonl"
        source.write_text(
            '{"event_id":"evt_cli_001","timestamp":"2026-08-16T10:00:00Z",'
            '"application":{"id":"fixture-app"},'
            '"action":{"kind":"task_start","target_role":"button"},'
            '"context":{"synthetic":true}}\n',
            encoding="utf-8",
        )
        imported = self.run_cli("ingest", profile, str(source), "--synthetic")
        self.assertEqual(imported.returncode, 0, imported.stderr)
        session = json.loads(imported.stdout)["session_id"]
        verified = self.run_cli("timeline", "verify", profile, session)
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertTrue(json.loads(verified.stdout)["sealed"])

    def test_demo_proves_preview_and_can_be_replayed_in_same_data_dir(self) -> None:
        first_pack = self.root / "first.learnpack"
        second_pack = self.root / "second.learnpack"
        first = self.run_cli("demo", "--output", str(first_pack))
        second = self.run_cli("demo", "--output", str(second_pack))
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        one = json.loads(first.stdout)
        two = json.loads(second.stdout)
        self.assertNotEqual(one["profile_id"], two["profile_id"])
        self.assertEqual(one["status"], "success_proved")
        self.assertFalse(one["preview"]["execution_allowed"])
        self.assertEqual(one["routine"]["status"], "compilable")
        self.assertEqual(one["question"]["status"], "answered")
        self.assertEqual(one["benchmark"]["vector"]["privacy"]["attempted_canaries"], 2)
        self.assertEqual(one["export"]["digest"], two["export"]["digest"])
        self.assertEqual(first_pack.read_bytes(), second_pack.read_bytes())


if __name__ == "__main__":
    unittest.main()
