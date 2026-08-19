import unittest

import agentmesh
import agentops
from agentops.routing_evidence import evaluate as routing_evaluate


class AgentMeshAbsorptionTests(unittest.TestCase):
    def test_legacy_and_agentops_surfaces_share_evaluator(self):
        self.assertIs(agentmesh.evaluate, routing_evaluate)
        self.assertTrue(hasattr(agentops, "ControlPlane"))

    def test_pass_and_fail_contract_is_preserved(self):
        passed = agentmesh.evaluate({"agent_count": 2, "healthy_agents": 2, "route_count": 1})
        failed = agentmesh.evaluate({"agent_count": 2, "healthy_agents": 1, "route_count": 1})
        blocked = agentmesh.evaluate({"agent_count": 2})
        self.assertEqual("passed", passed["status"])
        self.assertEqual("failed", failed["status"])
        self.assertEqual("blocked", blocked["status"])
        self.assertIn("evidence_sha256", passed)


if __name__ == "__main__":
    unittest.main()
