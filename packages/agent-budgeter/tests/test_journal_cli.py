from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from agent_budgeter.cli import run
from agent_budgeter.journal import EvidenceJournal
from agent_budgeter.models import ContractError, Decision, OperationResult
from agent_budgeter.probes import functional,liveness,readiness


def call(args):
    output=StringIO()
    with redirect_stdout(output): code=run(args)
    return code,json.loads(output.getvalue())


class JournalTests(unittest.TestCase):
    def setUp(self): self.temp=tempfile.TemporaryDirectory(); self.path=Path(self.temp.name)/"events.jsonl"; self.journal=EvidenceJournal(self.path)
    def tearDown(self): self.temp.cleanup()
    def result(self): return OperationResult.create("op",Decision.ACCEPTED,"test","ok")
    def test_missing_empty(self): self.assertEqual(self.journal.read(),[])
    def test_append(self): self.assertTrue(self.journal.append(self.result())); self.assertEqual(len(self.journal.read()),1)
    def test_duplicate_idempotent(self): self.journal.append(self.result()); before=self.path.read_bytes(); self.assertFalse(self.journal.append(self.result())); self.assertEqual(before,self.path.read_bytes())
    def test_conflict_blocks(self):
        self.journal.append(self.result())
        with self.assertRaises(ContractError): self.journal.append(OperationResult.create("op",Decision.BLOCKED,"test","different"))
    def test_truncated_blocks(self):
        self.path.write_text("{}",encoding="utf-8")
        with self.assertRaises(ContractError): self.journal.read()
    def test_invalid_json_blocks(self):
        self.path.write_text("bad\n",encoding="utf-8")
        with self.assertRaises(ContractError): self.journal.read()
    def test_tamper_blocks(self):
        self.journal.append(self.result()); event=json.loads(self.path.read_text()); event["payload"]["reason"]="tampered"; self.path.write_text(json.dumps(event)+"\n",encoding="utf-8")
        with self.assertRaises(ContractError): self.journal.read()


class ProbeCliTests(unittest.TestCase):
    def test_liveness(self): self.assertTrue(liveness()["healthy"])
    def test_readiness(self): self.assertTrue(readiness()["healthy"])
    def test_counterproof(self):
        result=functional(); self.assertTrue(result["healthy"]); self.assertTrue(all(c["passed"] for c in result["checks"]))
    def test_probe_cli(self):
        code,result=call(["probe","functional"]); self.assertEqual(code,0); self.assertTrue(result["healthy"])
    def test_demo(self):
        code,result=call(["demo"]); self.assertEqual(code,0); self.assertEqual(result["results"][0]["decision"],"accepted")
    def test_demo_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"journal.jsonl"; code,_=call(["demo","--journal",str(path)]); self.assertEqual(code,0); self.assertEqual(len(EvidenceJournal(path).read()),1)
    def test_invalid_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"bad.json"; path.write_text("{}",encoding="utf-8"); code,result=call(["fixture",str(path)]); self.assertEqual(code,4); self.assertEqual(result["decision"],"blocked")


if __name__ == "__main__": unittest.main()

