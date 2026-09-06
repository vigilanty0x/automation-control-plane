import unittest

from agent_productivity_dashboard import evaluate

GOOD = {"agent": "agent-a", "completed": 10, "failed": 1, "retries": 2, "elapsed_ms": 5000}


class ContractTests(unittest.TestCase):
    def test_valid_metrics_are_labeled_supplied(self):
        result = evaluate(GOOD)
        self.assertEqual(result["status"], "passed")
        self.assertFalse(result["metrics"]["observed_by_tool"])
        self.assertGreater(result["metrics"]["current"]["reliability"], 0.8)

    def test_boolean_and_negative_counts_fail(self):
        self.assertEqual(evaluate({**GOOD, "completed": True})["status"], "failed")
        self.assertEqual(evaluate({**GOOD, "failed": -1})["status"], "failed")

    def test_elapsed_and_total_bounds_fail(self):
        self.assertEqual(evaluate({**GOOD, "elapsed_ms": 0})["status"], "failed")
        self.assertEqual(evaluate({**GOOD, "completed": 1_000_000_000, "failed": 1})["status"], "failed")

    def test_optional_trend_is_calculated(self):
        point = {"as_of": "2026-08-15T00:00:00Z", "completed": 5, "failed": 1, "retries": 1, "elapsed_ms": 1000}
        result = evaluate({**GOOD, "trend": [point]})
        self.assertEqual(result["status"], "passed")
        self.assertEqual(len(result["metrics"]["trend"]), 1)

    def test_trend_requires_ordered_aware_timestamps(self):
        point = {"as_of": "2026-08-15T00:00:00", "completed": 5, "failed": 1, "retries": 1, "elapsed_ms": 1000}
        self.assertEqual(evaluate({**GOOD, "trend": [point]})["status"], "failed")
        aware = {**point, "as_of": "2026-08-15T00:00:00Z"}
        self.assertEqual(evaluate({**GOOD, "trend": [aware, aware]})["status"], "failed")

    def test_non_object_and_missing_field_fail_closed(self):
        self.assertEqual(evaluate([])["status"], "failed")
        self.assertEqual(evaluate({})["status"], "blocked")

    def test_result_is_deterministic(self):
        self.assertEqual(evaluate(GOOD), evaluate(dict(reversed(list(GOOD.items())))))


if __name__ == "__main__":
    unittest.main()
