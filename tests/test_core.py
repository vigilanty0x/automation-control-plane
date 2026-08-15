import unittest

from context_window_budgeter import budget, probe

BASE = {"window_tokens": 10, "output_reserve": 2,
        "sections": [{"name": "a", "tokens": 2, "required": True}]}


class Tests(unittest.TestCase):
    def test_ready_overflow_and_optional_drop(self):
        self.assertTrue(budget(BASE)["ok"])
        self.assertFalse(budget({**BASE, "window_tokens": 3, "output_reserve": 1,
                                 "sections": [{"name": "a", "tokens": 3, "required": True}]})["ok"])
        data = {"window_tokens": 4, "output_reserve": 1, "sections": [
            {"name": "a", "tokens": 2, "required": True},
            {"name": "b", "tokens": 2, "required": False}]}
        self.assertEqual(budget(data)["decision"], "degraded")

    def test_strict_integers_booleans_and_names(self):
        for value in ("10", True, 10.0):
            self.assertFalse(budget({**BASE, "window_tokens": value})["ok"])
        self.assertFalse(budget({**BASE, "sections": [{"name": "a", "tokens": 2, "required": 1}]})["ok"])
        self.assertFalse(budget({**BASE, "sections": BASE["sections"] * 2})["ok"])
        self.assertFalse(budget(None)["ok"])

    def test_probe(self):
        self.assertTrue(probe()["ok"])


if __name__ == "__main__":
    unittest.main()
