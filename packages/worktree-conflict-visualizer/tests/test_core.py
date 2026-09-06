import unittest

from worktree_conflict_visualizer import analyze, probe


class Tests(unittest.TestCase):
    def test_overlap_uses_unique_owner_sets_and_opaque_ids(self):
        result = analyze([{"name": "alpha", "files": ["x"]},
                          {"name": "beta", "files": ["x"]}])
        self.assertEqual(result["overlaps"], [{"path": "x", "worktrees": ["alpha", "beta"]}])
        self.assertNotIn("alpha ---", result["mermaid"])
        self.assertNotEqual(result["pairs"][0]["left"], result["pairs"][0]["right"])

    def test_repeated_path_in_one_worktree_is_rejected(self):
        result = analyze([{"name": "alpha", "files": ["x", "x"]}])
        self.assertFalse(result["ok"])
        self.assertIn("duplicate_path_in_worktree", result["errors"])

    def test_bounds_paths_and_names(self):
        for value in (None, [{"name": "bad name", "files": []}],
                      [{"name": "a", "files": ["../x"]}],
                      [{"name": "a", "files": "x"}]):
            self.assertFalse(analyze(value)["ok"])

    def test_clear(self):
        self.assertEqual(analyze([{"name": "a", "files": ["x"]},
                                  {"name": "b", "files": ["y"]}])["risk"], "low")

    def test_probe(self):
        self.assertTrue(probe()["ok"])


if __name__ == "__main__":
    unittest.main()
