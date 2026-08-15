import unittest
from worktree_conflict_visualizer import analyze,probe
class T(unittest.TestCase):
 def test_overlap(self):self.assertEqual(len(analyze([{"name":"a","files":["x"]},{"name":"b","files":["x"]}])["overlaps"]),1)
 def test_clear(self):self.assertEqual(analyze([{"name":"a","files":["x"]},{"name":"b","files":["y"]}])["risk"],"low")
 def test_traversal(self):self.assertFalse(analyze([{"name":"a","files":["../x"]}])["ok"])
 def test_probe(self):self.assertTrue(probe()["ok"])
if __name__=="__main__":unittest.main()
