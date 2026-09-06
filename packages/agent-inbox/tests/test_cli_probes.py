from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agent_inbox.cli import main
from agent_inbox.probes import functional_counter_proof, liveness, readiness

ROOT = Path(__file__).resolve().parents[1]


class ProbeTests(unittest.TestCase):
    def test_liveness(self): self.assertTrue(liveness()["ok"])
    def test_readiness(self):
        with TemporaryDirectory() as directory: self.assertTrue(readiness(Path(directory) / "db.sqlite3")["ok"])
    def test_functional(self):
        result = functional_counter_proof(); self.assertTrue(result["ok"]); self.assertTrue(result["idempotent_enqueue"])
        self.assertTrue(result["duplicate_claim_prevented"]); self.assertTrue(result["proofless_done_blocked"])
        self.assertTrue(result["done_persisted"]); self.assertTrue(result["lease_recovered_once"]); self.assertTrue(result["retry_exhaustion_failed"])


class CliTests(unittest.TestCase):
    def run_cli(self, args):
        output, error = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(error): code = main(args)
        return code, output.getvalue(), error.getvalue()

    def test_full_cli_lifecycle(self):
        with TemporaryDirectory() as directory:
            db = Path(directory) / "db.sqlite3"; base = ["--db", str(db)]
            self.assertEqual(self.run_cli(["init", *base])[0], 0)
            self.assertEqual(self.run_cli(["register", *base, "--input", str(ROOT / "examples/agent.json")])[0], 0)
            code, output, _ = self.run_cli(["enqueue", *base, "--input", str(ROOT / "examples/mission.json")]); self.assertEqual(code, 0); mission = json.loads(output)
            code, output, _ = self.run_cli(["claim", *base, "--agent", "worker", "--lease-seconds", "30"]); self.assertEqual(code, 0); claim = json.loads(output)
            code, output, error = self.run_cli(["complete", *base, "--mission", mission["mission_id"], "--lease-token", claim["lease_token"], "--evidence", str(ROOT / "examples/evidence.json")])
            self.assertEqual(code, 0, error); self.assertEqual(json.loads(output)["status"], "done")
            code, output, _ = self.run_cli(["inventory", *base]); self.assertEqual(code, 0); self.assertEqual(json.loads(output)["missions"]["done"], 1)
            code, output, _ = self.run_cli(["agents", *base]); self.assertEqual(code, 0); self.assertEqual(json.loads(output)[0]["agent_id"], "worker")

    def test_no_mission_returns_two(self):
        with TemporaryDirectory() as directory:
            db = Path(directory) / "db"; base = ["--db", str(db)]
            self.run_cli(["register", *base, "--input", str(ROOT / "examples/agent.json")])
            code, _, error = self.run_cli(["claim", *base, "--agent", "worker"])
            self.assertEqual(code, 2); self.assertEqual(json.loads(error)["error"], "NoMissionAvailable")

    def test_proofless_completion_structured_error(self):
        with TemporaryDirectory() as directory:
            db = Path(directory) / "db"; base = ["--db", str(db)]
            self.run_cli(["register", *base, "--input", str(ROOT / "examples/agent.json")]); mission = json.loads(self.run_cli(["enqueue", *base, "--input", str(ROOT / "examples/mission.json")])[1]); claim = json.loads(self.run_cli(["claim", *base, "--agent", "worker"])[1])
            bad = Path(directory) / "bad.json"; bad.write_text('{"summary":"no proof","tests":[],"commits":[],"artifacts":[]}')
            code, _, error = self.run_cli(["complete", *base, "--mission", mission["mission_id"], "--lease-token", claim["lease_token"], "--evidence", str(bad)])
            self.assertEqual(code, 1); self.assertEqual(json.loads(error)["error"], "EvidenceRequired")

    def test_signal_and_list(self):
        with TemporaryDirectory() as directory:
            db = Path(directory) / "db"; base = ["--db", str(db)]; mission = json.loads(self.run_cli(["enqueue", *base, "--input", str(ROOT / "examples/mission.json")])[1])
            code, output, _ = self.run_cli(["signal", *base, "--mission", mission["mission_id"], "--event-id", "d1", "--kind", "disagreement", "--actor", "reviewer", "--detail", str(ROOT / "examples/disagreement.json")])
            self.assertEqual(code, 0); self.assertEqual(json.loads(output)["kind"], "disagreement")
            code, output, _ = self.run_cli(["list", *base, "--status", "queued", "--limit", "10"]); self.assertEqual(code, 0); self.assertEqual(len(json.loads(output)), 1)

    def test_invalid_json_error(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "bad"; path.write_text("bad")
            code, _, error = self.run_cli(["enqueue", "--db", str(Path(directory) / "db"), "--input", str(path)])
            self.assertEqual(code, 1); self.assertFalse(json.loads(error)["success"])

    def test_non_object_error(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "bad"; path.write_text("[]")
            code, _, error = self.run_cli(["enqueue", "--db", str(Path(directory) / "db"), "--input", str(path)])
            self.assertEqual(code, 1); self.assertEqual(json.loads(error)["error"], "ContractError")

    def test_oversize_error(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "bad"; path.write_text(" " * 1_000_001)
            code, _, error = self.run_cli(["enqueue", "--db", str(Path(directory) / "db"), "--input", str(path)])
            self.assertEqual(code, 1); self.assertIn("exceeds", json.loads(error)["message"])

    def test_probe_cli(self):
        code, output, _ = self.run_cli(["probe", "functional"]); self.assertEqual(code, 0); self.assertTrue(json.loads(output)["ok"])


if __name__ == "__main__": unittest.main()
