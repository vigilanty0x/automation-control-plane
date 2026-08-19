import unittest

import agentops
import automation_control_plane


class AgentOpsCompatibilityTests(unittest.TestCase):
    def test_public_api_is_preserved(self):
        self.assertEqual(agentops.__version__, automation_control_plane.__version__)
        self.assertEqual(set(agentops.__all__), set(automation_control_plane.__all__))
        for name in automation_control_plane.__all__:
            self.assertIs(getattr(agentops, name), getattr(automation_control_plane, name))

    def test_cli_alias_uses_same_main(self):
        from agentops.cli import main as canonical_main
        from automation_control_plane.cli import main as legacy_main

        self.assertIs(canonical_main, legacy_main)


if __name__ == "__main__":
    unittest.main()
