from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from taskgraph.cli import main
from taskgraph.probes import functional_probe, liveness_probe, readiness_probe


ROOT = Path(__file__).resolve().parents[1]
GRAPH = ROOT / "examples" / "graph.json"
EVIDENCE = ROOT / "examples" / "evidence.json"


class ProbeTests(unittest.TestCase):
    def test_liveness(self): self.assertTrue(liveness_probe()["ok"])
    def test_readiness(self): self.assertEqual(readiness_probe()["runtime_dependencies"], [])
    def test_functional_counterproof(self):
        result = functional_probe(); self.assertTrue(result["ok"]); self.assertTrue(result["counter_example_failed"]); self.assertTrue(result["dependency_unlocked"])


class CliTests(unittest.TestCase):
    def invoke(self, args):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err): code = main(args)
        return code, out.getvalue(), err.getvalue()

    def test_validate(self):
        code, out, _ = self.invoke(["validate","--graph",str(GRAPH)])
        self.assertEqual(code,0); self.assertTrue(json.loads(out)["valid"])

    def test_init_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            db = str(Path(directory)/"graph.db")
            first = self.invoke(["init","--graph",str(GRAPH),"--db",db])
            second = self.invoke(["init","--graph",str(GRAPH),"--db",db])
            self.assertTrue(json.loads(first[1])["created"]); self.assertFalse(json.loads(second[1])["created"])

    def test_claim_complete_and_status(self):
        with tempfile.TemporaryDirectory() as directory:
            db = str(Path(directory)/"graph.db")
            self.invoke(["init","--graph",str(GRAPH),"--db",db])
            code, out, _ = self.invoke(["claim","--db",db,"--graph-id","public-example","--worker","w","--now","1"])
            self.assertEqual(code,0); self.assertEqual(json.loads(out)["claimed"]["task"]["task_id"],"contract")
            code2, out2, _ = self.invoke(["complete","--db",db,"--graph-id","public-example","--task-id","contract","--worker","w","--evidence",str(EVIDENCE),"--event-id","complete-contract"])
            self.assertEqual(code2,0); self.assertTrue(json.loads(out2)["recorded"])
            status_code, status_out, _ = self.invoke(["status","--db",db,"--graph-id","public-example"])
            self.assertEqual(status_code,1); self.assertEqual(json.loads(status_out)["counts"]["done"],1)

    def test_fail_command(self):
        with tempfile.TemporaryDirectory() as directory:
            db = str(Path(directory)/"graph.db")
            self.invoke(["init","--graph",str(GRAPH),"--db",db]); self.invoke(["claim","--db",db,"--graph-id","public-example","--worker","w","--now","1"])
            code,out,_=self.invoke(["fail","--db",db,"--graph-id","public-example","--task-id","contract","--worker","w","--error","synthetic","--event-id","fail-contract"])
            self.assertEqual(code,0); self.assertEqual(json.loads(out)["state"],"waiting")

    def test_resume_command(self):
        with tempfile.TemporaryDirectory() as directory:
            db=str(Path(directory)/"graph.db")
            self.invoke(["init","--graph",str(GRAPH),"--db",db]); self.invoke(["claim","--db",db,"--graph-id","public-example","--worker","w","--now","1","--lease-seconds","1"])
            code,out,_=self.invoke(["resume","--db",db,"--graph-id","public-example","--now","2"])
            self.assertEqual(code,0); self.assertEqual(json.loads(out)["resumed"],1)

    def test_functional_probe_command(self):
        code,out,_=self.invoke(["probe","--level","functional"]); self.assertEqual(code,0); self.assertTrue(json.loads(out)["counter_example_failed"])

    def test_demo_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            first=self.invoke(["demo","--workspace",directory]); second=self.invoke(["demo","--workspace",directory])
            self.assertEqual((first[0],second[0]),(0,0)); self.assertTrue(json.loads(first[1])["created"]); self.assertFalse(json.loads(second[1])["created"])

    def test_missing_graph_bounded_error(self):
        code,_,err=self.invoke(["validate","--graph","/tmp/no-taskgraph.json"]); self.assertEqual(code,2); self.assertIn("does not exist",err)

