import unittest
from timeout_toolkit import evaluate

GOOD = {"timeout_ms":1000,"elapsed_ms":250,"operation":"index"}
BAD = {"timeout_ms":1000,"elapsed_ms":2000,"operation":"index"}

class CoreTests(unittest.TestCase):
    def test_good_record_passes_and_is_deterministic(self):
        first = evaluate(GOOD)
        self.assertEqual(first["status"], "passed")
        self.assertEqual(first, evaluate(dict(reversed(list(GOOD.items())))))
        self.assertEqual(len(first["evidence_sha256"]), 64)

    def test_bad_record_fails(self):
        self.assertEqual(evaluate(BAD)["status"], "failed")

    def test_missing_field_blocks(self):
        incomplete = dict(GOOD)
        incomplete.pop(next(iter(incomplete)))
        self.assertEqual(evaluate(incomplete)["status"], "blocked")

if __name__ == "__main__":
    unittest.main()

