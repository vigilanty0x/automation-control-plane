import copy
import unittest

from agent_session_recorder import probe, record, verify

EVENTS = [{"sequence": 1, "kind": "input", "content": "x"},
          {"sequence": 2, "kind": "output", "content": {"answer": 1}}]


class Tests(unittest.TestCase):
    def test_integrity_only_and_trusted_head(self):
        transcript = record(EVENTS)
        self.assertEqual(verify(transcript)["authenticity"], "not_established")
        self.assertEqual(verify(transcript, expected_head_sha256=transcript["head_sha256"])["authenticity"],
                         "trusted_head")

    def test_every_stored_chain_field_is_checked(self):
        transcript = record(EVENTS)
        mutations = [
            ("previous", lambda t: t["events"][1].__setitem__("previous_sha256", "0" * 64)),
            ("event", lambda t: t["events"][0].__setitem__("event_sha256", "0" * 64)),
            ("count", lambda t: t.__setitem__("count", 3)),
            ("head", lambda t: t.__setitem__("head_sha256", "0" * 64)),
        ]
        for label, mutate in mutations:
            with self.subTest(label=label):
                changed = copy.deepcopy(transcript)
                mutate(changed)
                self.assertFalse(verify(changed)["ok"])

    def test_full_rewrite_needs_external_anchor(self):
        original = record(EVENTS)
        rewritten = record([{**EVENTS[0], "content": "rewritten"}, EVENTS[1]])
        self.assertTrue(verify(rewritten)["integrity"])
        self.assertFalse(verify(rewritten, expected_head_sha256=original["head_sha256"])["ok"])

    def test_strict_event_schema_and_bounds(self):
        self.assertFalse(record([{**EVENTS[0], "extra": 1}])["ok"])
        self.assertFalse(record([{**EVENTS[0], "content": float("nan")}])["ok"])
        self.assertFalse(verify([])["ok"])

    def test_probe(self):
        self.assertTrue(probe()["ok"])


if __name__ == "__main__":
    unittest.main()
