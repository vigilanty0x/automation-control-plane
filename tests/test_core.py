import unittest
from context_window_budgeter import budget,probe
class T(unittest.TestCase):
 def test_ready(self):self.assertTrue(budget({"window_tokens":10,"output_reserve":2,"sections":[{"name":"a","tokens":2,"required":True}]})["ok"])
 def test_required_overflow(self):self.assertFalse(budget({"window_tokens":3,"output_reserve":1,"sections":[{"name":"a","tokens":3,"required":True}]})["ok"])
 def test_optional_drop(self):self.assertEqual(budget({"window_tokens":4,"output_reserve":1,"sections":[{"name":"a","tokens":2,"required":True},{"name":"b","tokens":2}]})["decision"],"degraded")
 def test_probe(self):self.assertTrue(probe()["ok"])
if __name__=="__main__":unittest.main()
