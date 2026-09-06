from __future__ import annotations

import unittest

from scripts import check


class RootProductScanTests(unittest.TestCase):
    def test_root_scan_prunes_monorepo_boundaries(self):
        paths = {
            path.relative_to(check.ROOT).as_posix()
            for path in check._iter_root_product_paths()
        }

        self.assertNotIn("MONOREPO.json", paths)
        self.assertFalse(
            any(path == "packages" or path.startswith("packages/") for path in paths)
        )
        self.assertIn("scripts/check.py", paths)
        self.assertIn("src/automation_control_plane/engine.py", paths)


if __name__ == "__main__":
    unittest.main()
