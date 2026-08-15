from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from agent_handoff.cli import main
from agent_handoff.probes import functional_probe, liveness_probe, readiness_probe


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "handoff.json"


class ProbeTests(unittest.TestCase):
    def test_liveness(self):
        self.assertTrue(liveness_probe()["ok"])

    def test_readiness_has_zero_runtime_dependencies(self):
        self.assertEqual(readiness_probe()["runtime_dependencies"], [])

    def test_functional_requires_counter_failure_and_replay(self):
        result = functional_probe()
        self.assertTrue(result["ok"])
        self.assertTrue(result["counter_example_failed"])
        self.assertTrue(result["idempotent_replay"])
        self.assertTrue(result["ledger_verified"])


class CliTests(unittest.TestCase):
    def invoke(self, args):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(args)
        return code, out.getvalue(), err.getvalue()

    def test_validate(self):
        code, out, _ = self.invoke(["validate", "--input", str(EXAMPLE)])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["valid"])

    def test_render_markdown(self):
        code, out, _ = self.invoke(["render", "--input", str(EXAMPLE), "--format", "markdown"])
        self.assertEqual(code, 0)
        self.assertIn("Agent Handoff", out)

    def test_render_json_to_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "handoff.json"
            code, _, _ = self.invoke(["render", "--input", str(EXAMPLE), "--format", "json", "--output", str(target)])
            self.assertEqual(code, 0)
            self.assertIn("logical_sha256", json.loads(target.read_text()))

    def test_append_and_verify(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.jsonl"
            code1, out1, _ = self.invoke(["append", "--input", str(EXAMPLE), "--ledger", str(ledger)])
            code2, out2, _ = self.invoke(["append", "--input", str(EXAMPLE), "--ledger", str(ledger)])
            code3, out3, _ = self.invoke(["verify-ledger", "--ledger", str(ledger)])
            self.assertEqual((code1, code2, code3), (0, 0, 0))
            self.assertTrue(json.loads(out1)["appended"])
            self.assertFalse(json.loads(out2)["appended"])
            self.assertTrue(json.loads(out3)["valid"])

    def test_functional_probe_command(self):
        code, out, _ = self.invoke(["probe", "--level", "functional"])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["counter_example_failed"])

    def test_missing_input_is_bounded_error(self):
        code, _, err = self.invoke(["validate", "--input", "/tmp/no-handoff.json"])
        self.assertEqual(code, 2)
        self.assertIn("does not exist", err)

