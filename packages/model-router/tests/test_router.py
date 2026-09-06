from __future__ import annotations

import unittest

from model_router.models import AgentProfile, RiskLevel, TaskRequest
from model_router.registry import CapabilityRegistry
from model_router.router import ModelRouter


def agent(
    agent_id: str,
    *,
    capabilities: tuple[str, ...] = ("python",),
    permissions: tuple[str, ...] = ("read",),
    cost: float = 0.02,
    latency: int = 1_000,
    context: int = 64_000,
    success: float = 0.9,
    active: bool = True,
    load: int = 0,
    concurrency: int = 2,
) -> AgentProfile:
    return AgentProfile(
        agent_id=agent_id,
        owner=f"owner-{agent_id}",
        capabilities=frozenset(capabilities),
        permissions=frozenset(permissions),
        cost_per_1k_tokens_usd=cost,
        p95_latency_ms=latency,
        context_window_tokens=context,
        historical_success_rate=success,
        active=active,
        current_load=load,
        max_concurrency=concurrency,
    )


def task(**overrides: object) -> TaskRequest:
    values: dict[str, object] = {
        "task_id": "task-1",
        "idempotency_key": "idem-1",
        "title": "Implement bounded parser",
        "required_capabilities": frozenset({"python"}),
        "required_permissions": frozenset({"read"}),
        "budget_usd": 2.0,
        "max_latency_ms": 3_000,
        "context_tokens": 20_000,
        "scope": ("src/parser.py",),
        "acceptance_criteria": ("parser tests pass",),
        "risk": RiskLevel.LOW,
        "max_attempts": 2,
    }
    values.update(overrides)
    return TaskRequest(**values)


class RegistryTests(unittest.TestCase):
    def test_rejects_duplicate_agent_id(self) -> None:
        registry = CapabilityRegistry([agent("alpha")])
        with self.assertRaises(ValueError):
            registry.register(agent("alpha"))

    def test_snapshot_is_sorted_and_machine_readable(self) -> None:
        registry = CapabilityRegistry([agent("zeta"), agent("alpha")])
        self.assertEqual([item["agent_id"] for item in registry.to_dict()], ["alpha", "zeta"])

    def test_profile_validation_blocks_impossible_values(self) -> None:
        with self.assertRaises(ValueError):
            agent("bad", success=1.1)
        with self.assertRaises(ValueError):
            agent("bad", concurrency=0)


class RouterTests(unittest.TestCase):
    def test_routes_to_highest_evidence_backed_score(self) -> None:
        registry = CapabilityRegistry(
            [
                agent("cheap", cost=0.01, success=0.75, latency=900),
                agent("reliable", cost=0.02, success=0.98, latency=1_100),
            ]
        )
        decision = ModelRouter(registry).route(task())
        self.assertEqual(decision.selected_agent_id, "reliable")
        self.assertGreater(decision.score, 0.8)
        self.assertFalse(decision.rejected)
        self.assertTrue(decision.explanations)

    def test_ties_are_deterministic_by_agent_id(self) -> None:
        registry = CapabilityRegistry([agent("bravo"), agent("alpha")])
        decision = ModelRouter(registry).route(task())
        self.assertEqual(decision.selected_agent_id, "alpha")
        self.assertTrue(decision.disagreement)
        self.assertEqual(decision.escalation_reason, "top candidates are within 0.03 score")

    def test_capability_mismatch_is_visible(self) -> None:
        decision = ModelRouter(CapabilityRegistry([agent("alpha")])).route(
            task(required_capabilities=frozenset({"rust"}))
        )
        self.assertTrue(decision.rejected)
        self.assertIn("missing capabilities: rust", decision.rejections["alpha"])

    def test_permission_mismatch_is_visible(self) -> None:
        decision = ModelRouter(CapabilityRegistry([agent("alpha")])).route(
            task(required_permissions=frozenset({"write"}))
        )
        self.assertTrue(decision.rejected)
        self.assertIn("missing permissions: write", decision.rejections["alpha"])

    def test_budget_is_hard_boundary(self) -> None:
        decision = ModelRouter(CapabilityRegistry([agent("alpha", cost=0.2)])).route(
            task(budget_usd=1.0, context_tokens=10_000)
        )
        self.assertTrue(decision.rejected)
        self.assertIn("budget exceeded", decision.rejections["alpha"])

    def test_latency_is_hard_boundary(self) -> None:
        decision = ModelRouter(CapabilityRegistry([agent("alpha", latency=5_000)])).route(
            task(max_latency_ms=3_000)
        )
        self.assertTrue(decision.rejected)
        self.assertIn("latency limit exceeded", decision.rejections["alpha"])

    def test_context_window_is_hard_boundary(self) -> None:
        decision = ModelRouter(CapabilityRegistry([agent("alpha", context=8_000)])).route(
            task(context_tokens=10_000)
        )
        self.assertTrue(decision.rejected)
        self.assertIn("context window exceeded", decision.rejections["alpha"])

    def test_inactive_and_saturated_agents_are_rejected(self) -> None:
        registry = CapabilityRegistry(
            [agent("inactive", active=False), agent("busy", load=2, concurrency=2)]
        )
        decision = ModelRouter(registry).route(task())
        self.assertTrue(decision.rejected)
        self.assertEqual(decision.rejections["inactive"], ["agent is inactive"])
        self.assertEqual(decision.rejections["busy"], ["concurrency limit reached"])

    def test_high_risk_task_requires_explicit_permission(self) -> None:
        decision = ModelRouter(CapabilityRegistry([agent("alpha")])).route(
            task(risk=RiskLevel.HIGH)
        )
        self.assertTrue(decision.rejected)
        self.assertIn("missing permissions: high_risk", decision.rejections["alpha"])

    def test_high_risk_route_requires_human_escalation_even_when_eligible(self) -> None:
        profile = agent("alpha", permissions=("read", "high_risk"))
        decision = ModelRouter(CapabilityRegistry([profile])).route(task(risk=RiskLevel.HIGH))
        self.assertFalse(decision.rejected)
        self.assertTrue(decision.human_approval_required)
        self.assertEqual(decision.escalation_reason, "high-risk mission requires human approval")

    def test_empty_registry_returns_structured_rejection(self) -> None:
        decision = ModelRouter(CapabilityRegistry()).route(task())
        self.assertTrue(decision.rejected)
        self.assertEqual(decision.rejections, {"registry": ["no agents registered"]})

    def test_decision_serialization_preserves_candidate_evidence(self) -> None:
        decision = ModelRouter(CapabilityRegistry([agent("alpha")])).route(task())
        payload = decision.to_dict()
        self.assertEqual(payload["selected_agent_id"], "alpha")
        self.assertIn("alpha", payload["candidate_scores"])
        self.assertEqual(payload["task_id"], "task-1")


if __name__ == "__main__":
    unittest.main()
