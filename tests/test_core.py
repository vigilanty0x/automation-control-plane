import unittest
from handoff_markdown_cli import build,probe
S={"title":"t","summary":"s","completed":["c"],"pending":["p"],"evidence":["e"],"risks":["r"],"next_owner":"o"}
class T(unittest.TestCase):
 def test_build(self):self.assertTrue(build(S)["ok"])
 def test_evidence(self):self.assertFalse(build({**S,"evidence":[]})["ok"])
 def test_owner(self):self.assertFalse(build({**S,"next_owner":""})["ok"])
 def test_probe(self):self.assertTrue(probe()["ok"])
if __name__=="__main__":unittest.main()
