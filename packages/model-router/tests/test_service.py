from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from model_router.models import AgentProfile, MissionState, TaskRequest
from model_router.registry import CapabilityRegistry
from model_router.router import ModelRouter
from model_router.service import ModelRouterService
from model_router.store import SQLiteMissionStore


def profile() -> AgentProfile:
    return AgentProfile(
        agent_id="alpha",
        owner="routing-team",
        capabilities=frozenset({"python", "testing"}),
        permissions=frozenset({"read", "write"}),
        cost_per_1k_tokens_usd=0.01,
        p95_latency_ms=900,
        context_window_tokens=64_000,
        historical_success_rate=0.94,
        max_concurrency=2,
    )


def request(capability: str = "python") -> TaskRequest:
    return TaskRequest(
        task_id=f"task-{capability}",
        idempotency_key=f"idem-{capability}",
        title="Build service layer",
        required_capabilities=frozenset({capability}),
        required_permissions=frozenset({"read"}),
        budget_usd=1.0,
        max_latency_ms=2_000,
        context_tokens=8_000,
        scope=("src/model_router/service.py",),
        acceptance_criteria=("service tests pass",),
    )


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SQLiteMissionStore(Path(self.tmp.name) / "router.sqlite3")
        self.service = ModelRouterService(ModelRouter(CapabilityRegistry([profile()])), self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def test_submit_routes_and_persists_mission(self) -> None:
        submission = self.service.submit(request())
        self.assertTrue(submission.created)
        self.assertEqual(submission.mission.owner, "routing-team")
        self.assertEqual(submission.mission.state, MissionState.QUEUED)

    def test_submit_is_idempotent_through_service_boundary(self) -> None:
        first = self.service.submit(request())
        second = self.service.submit(request())
        self.assertFalse(second.created)
        self.assertEqual(first.mission.mission_id, second.mission.mission_id)

    def test_unroutable_work_is_not_silently_dropped(self) -> None:
        submission = self.service.submit(request("rust"))
        self.assertEqual(submission.mission.state, MissionState.REJECTED)
        self.assertTrue(submission.mission.decision.rejections)

    def test_submission_serialization_contains_route_and_queue_evidence(self) -> None:
        payload = self.service.submit(request()).to_dict()
        self.assertTrue(payload["created"])
        self.assertEqual(payload["mission"]["decision"]["selected_agent_id"], "alpha")


if __name__ == "__main__":
    unittest.main()
