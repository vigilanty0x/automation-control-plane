import unittest

from agent_quota_simulator import probe, simulate

BUDGET = {"tokens": 10, "seconds": 10, "cost_micros": 10}
TASK = {"id": "a", "tokens": 1, "seconds": 1, "cost_micros": 1}


class Tests(unittest.TestCase):
    def test_admit_and_reject(self):
        self.assertEqual(simulate({"budget": BUDGET, "tasks": [TASK]})["admitted"], ["a"])
        self.assertEqual(len(simulate({"budget": BUDGET, "tasks": [{**TASK, "tokens": 11}]})["rejected"]), 1)

    def test_no_integer_coercion_or_bool_counts(self):
        for value in ("1", 1.0, True, None):
            self.assertFalse(simulate({"budget": {**BUDGET, "tokens": value}, "tasks": []})["ok"])
            self.assertFalse(simulate({"budget": BUDGET, "tasks": [{**TASK, "tokens": value}]})["ok"])

    def test_structured_unique_bounded_tasks(self):
        self.assertFalse(simulate({"budget": BUDGET, "tasks": [TASK, TASK]})["ok"])
        self.assertFalse(simulate({"budget": BUDGET, "tasks": ["bad"]})["ok"])
        self.assertFalse(simulate({"budget": BUDGET, "tasks": [{**TASK, "id": "bad id"}]})["ok"])
        self.assertFalse(simulate(None)["ok"])

    def test_probe(self):
        self.assertTrue(probe()["ok"])


if __name__ == "__main__":
    unittest.main()
