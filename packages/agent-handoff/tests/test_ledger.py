import json
from pathlib import Path
import tempfile
import unittest

from fixtures import handoff_dict
from agent_handoff.ledger import HandoffLedger
from agent_handoff.models import ContractError, Handoff


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "nested" / "handoffs.jsonl"
        self.ledger = HandoffLedger(self.path)
        self.handoff = Handoff.from_dict(handoff_dict())

    def tearDown(self):
        self.directory.cleanup()

    def test_empty_ledger_verifies(self):
        self.assertEqual(self.ledger.verify()["entries"], 0)

    def test_append_creates_parent_and_one_line(self):
        _, appended = self.ledger.append(self.handoff)
        self.assertTrue(appended)
        self.assertEqual(len(self.path.read_text().splitlines()), 1)

    def test_exact_replay_is_idempotent(self):
        first, added1 = self.ledger.append(self.handoff)
        second, added2 = self.ledger.append(self.handoff)
        self.assertTrue(added1)
        self.assertFalse(added2)
        self.assertEqual(first, second)

    def test_conflicting_sequence_is_rejected(self):
        self.ledger.append(self.handoff)
        raw = handoff_dict(); raw["summary"] = "changed"
        with self.assertRaisesRegex(ContractError, "idempotency conflict"):
            self.ledger.append(Handoff.from_dict(raw))

    def test_two_sequences_form_chain(self):
        self.ledger.append(self.handoff)
        raw = handoff_dict(); raw["sequence"] = 2; raw["handoff_id"] = "probe-handoff-2"
        self.ledger.append(Handoff.from_dict(raw))
        rows = self.ledger.entries()
        self.assertEqual(rows[1]["previous_event_sha256"], rows[0]["event_sha256"])
        self.assertEqual(self.ledger.verify()["entries"], 2)

    def test_tampered_handoff_is_detected(self):
        self.ledger.append(self.handoff)
        row = json.loads(self.path.read_text())
        row["handoff"]["summary"] = "tampered"
        self.path.write_text(json.dumps(row) + "\n")
        with self.assertRaisesRegex(ContractError, "handoff SHA mismatch"):
            self.ledger.verify()

    def test_tampered_chain_is_detected(self):
        self.ledger.append(self.handoff)
        row = json.loads(self.path.read_text()); row["previous_event_sha256"] = "f" * 64
        self.path.write_text(json.dumps(row) + "\n")
        with self.assertRaisesRegex(ContractError, "chain mismatch"):
            self.ledger.verify()

    def test_invalid_json_is_detected(self):
        self.path.parent.mkdir(parents=True); self.path.write_text("{\n")
        with self.assertRaisesRegex(ContractError, "invalid JSON"):
            self.ledger.verify()

    def test_duplicate_event_lines_are_detected(self):
        self.ledger.append(self.handoff)
        line = self.path.read_text(); self.path.write_text(line + line)
        with self.assertRaises(ContractError): self.ledger.verify()

