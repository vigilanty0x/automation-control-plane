import unittest
from agent_session_recorder import record,verify,probe
E=[{"sequence":1,"kind":"input","content":"x"}]
class T(unittest.TestCase):
 def test_record(self):self.assertTrue(record(E)["ok"])
 def test_sequence(self):self.assertFalse(record([{**E[0],"sequence":2}])["ok"])
 def test_verify_tamper(self):
  t=record(E);t["events"][0]["content"]="changed";self.assertFalse(verify(t)["ok"])
 def test_probe(self):self.assertTrue(probe()["ok"])
if __name__=="__main__":unittest.main()
