import unittest
from agent_quota_simulator import simulate,probe
B={"tokens":10,"seconds":10,"cost_micros":10}
class T(unittest.TestCase):
 def test_admit(self):self.assertEqual(simulate({"budget":B,"tasks":[{"id":"a","tokens":1,"seconds":1,"cost_micros":1}]})["admitted"],["a"])
 def test_reject(self):self.assertEqual(len(simulate({"budget":B,"tasks":[{"id":"a","tokens":11,"seconds":1,"cost_micros":1}]})["rejected"]),1)
 def test_negative(self):self.assertFalse(simulate({"budget":{"tokens":-1,"seconds":1,"cost_micros":1},"tasks":[]})["ok"])
 def test_probe(self):self.assertTrue(probe()["ok"])
if __name__=="__main__":unittest.main()
