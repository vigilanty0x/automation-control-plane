from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from model_router.models import (
    EvidenceBundle,
    MissionState,
    RiskLevel,
    RouteDecision,
    TaskRequest,
)
from model_router.state_machine import InvalidTransition
from model_router.store import MissionNotFound, SQLiteMissionStore


def task(*, key: str = "idem-1", risk: RiskLevel = RiskLevel.LOW) -> TaskRequest:
    return TaskRequest(
        task_id=f"task-{key}",
        idempotency_key=key,
        title="Implement persistent queue",
        required_capabilities=frozenset({"python"}),
        required_permissions=frozenset({"read", "write"}),
        budget_usd=3.0,
        max_latency_ms=4_000,
        context_tokens=12_000,
        scope=("src/model_router/store.py", "tests/test_store.py"),
        acceptance_criteria=("queue tests pass", "events are durable"),
        risk=risk,
        max_attempts=2,
    )


def decision(
    request: TaskRequest,
    *,
    rejected: bool = False,
    approval: bool = False,
) -> RouteDecision:
    return RouteDecision(
        task_id=request.task_id,
        selected_agent_id=None if rejected else "alpha",
        selected_owner=None if rejected else "team-alpha",
        score=0 if rejected else 0.91,
        estimated_cost_usd=None if rejected else 0.4,
        candidate_scores={} if rejected else {"alpha": 0.91},
        rejections={"alpha": ["missing permissions: write"]} if rejected else {},
        explanations=("test decision",),
        human_approval_required=approval,
        escalation_reason="high-risk mission requires human approval" if approval else None,
    )


def proof() -> EvidenceBundle:
    return EvidenceBundle(
        commit_sha="b" * 40,
        tests=("python -m unittest:pass",),
        artifacts=("artifacts/store-report.json",),
        criteria={"queue tests pass": True, "events are durable": True},
        produced_by="worker-a",
    )


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "router.sqlite3"
        self.store = SQLiteMissionStore(self.db_path)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def test_enqueue_is_idempotent(self) -> None:
        request = task()
        first, created_first = self.store.enqueue(request, decision(request))
        second, created_second = self.store.enqueue(request, decision(request))
        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.mission_id, second.mission_id)
        self.assertEqual(self.store.metrics()["total_missions"], 1)

    def test_duplicate_key_with_different_payload_is_rejected(self) -> None:
        request = task()
        self.store.enqueue(request, decision(request))
        changed = TaskRequest.from_dict({**request.to_dict(), "title": "Different work"})
        with self.assertRaisesRegex(ValueError, "idempotency conflict"):
            self.store.enqueue(changed, decision(changed))

    def test_rejected_route_is_persisted_with_reasons(self) -> None:
        request = task()
        mission, created = self.store.enqueue(request, decision(request, rejected=True))
        self.assertTrue(created)
        self.assertEqual(mission.state, MissionState.REJECTED)
        self.assertIsNone(mission.owner)
        self.assertEqual(mission.decision.rejections["alpha"], ["missing permissions: write"])

    def test_claim_assigns_owner_and_is_single_consumer(self) -> None:
        request = task()
        queued, _ = self.store.enqueue(request, decision(request))
        claimed = self.store.claim("worker-a", now="2026-08-15T10:00:00+00:00")
        self.assertIsNotNone(claimed)
        assert claimed is not None
        self.assertEqual(claimed.mission_id, queued.mission_id)
        self.assertEqual(claimed.state, MissionState.RUNNING)
        self.assertEqual(claimed.lease_owner, "worker-a")
        self.assertIsNone(self.store.claim("worker-b", now="2026-08-15T10:00:01+00:00"))

    def test_claim_order_is_fifo(self) -> None:
        first, _ = self.store.enqueue(task(key="a"), decision(task(key="a")), now="2026-08-15T10:00:00+00:00")
        self.store.enqueue(task(key="b"), decision(task(key="b")), now="2026-08-15T10:00:01+00:00")
        claimed = self.store.claim("worker-a", now="2026-08-15T10:00:02+00:00")
        assert claimed is not None
        self.assertEqual(claimed.mission_id, first.mission_id)

    def test_high_risk_mission_cannot_be_claimed_before_approval(self) -> None:
        request = task(risk=RiskLevel.HIGH)
        mission, _ = self.store.enqueue(request, decision(request, approval=True))
        self.assertIsNone(self.store.claim("worker-a"))
        approved = self.store.approve(mission.mission_id, actor="reviewer@example.test")
        self.assertTrue(approved.approved)
        claimed = self.store.claim("worker-a")
        self.assertIsNotNone(claimed)

    def test_approval_is_idempotent_and_counted_once(self) -> None:
        request = task(risk=RiskLevel.HIGH)
        mission, _ = self.store.enqueue(request, decision(request, approval=True))
        first = self.store.approve(mission.mission_id, actor="reviewer")
        second = self.store.approve(mission.mission_id, actor="reviewer")
        self.assertEqual(first.human_interventions, 1)
        self.assertEqual(second.human_interventions, 1)

    def test_transition_records_wait_and_failure_without_hiding_reason(self) -> None:
        request = task()
        mission, _ = self.store.enqueue(request, decision(request))
        running = self.store.claim("worker-a")
        assert running is not None
        waiting = self.store.transition(
            mission.mission_id,
            MissionState.WAITING,
            actor="worker-a",
            reason="approval needed for public change",
        )
        self.assertEqual(waiting.state, MissionState.WAITING)
        resumed = self.store.transition(
            mission.mission_id,
            MissionState.RUNNING,
            actor="worker-a",
            reason="approval recorded",
        )
        failed = self.store.transition(
            mission.mission_id,
            MissionState.FAILED,
            actor="worker-a",
            reason="test command exited 1",
        )
        self.assertEqual(resumed.state, MissionState.RUNNING)
        self.assertEqual(failed.last_error, "test command exited 1")
        reasons = [event.reason for event in self.store.events(mission.mission_id)]
        self.assertIn("approval needed for public change", reasons)
        self.assertIn("test command exited 1", reasons)

    def test_failure_requires_a_visible_reason(self) -> None:
        request = task()
        mission, _ = self.store.enqueue(request, decision(request))
        self.store.claim("worker-a")
        with self.assertRaisesRegex(ValueError, "reason"):
            self.store.transition(
                mission.mission_id,
                MissionState.FAILED,
                actor="worker-a",
                reason="",
            )

    def test_retry_increments_attempt_and_never_duplicates_mission(self) -> None:
        request = task()
        mission, _ = self.store.enqueue(request, decision(request))
        self.store.claim("worker-a")
        self.store.transition(
            mission.mission_id,
            MissionState.FAILED,
            actor="worker-a",
            reason="bounded failure",
        )
        retried = self.store.retry(mission.mission_id, actor="scheduler")
        self.assertEqual(retried.state, MissionState.QUEUED)
        self.assertEqual(retried.attempt, 2)
        self.assertEqual(retried.mission_id, mission.mission_id)
        self.assertEqual(self.store.metrics()["total_missions"], 1)

    def test_retry_budget_is_enforced(self) -> None:
        request = task()
        mission, _ = self.store.enqueue(request, decision(request))
        self.store.claim("worker-a")
        self.store.transition(mission.mission_id, MissionState.FAILED, actor="worker-a", reason="one")
        self.store.retry(mission.mission_id, actor="scheduler")
        self.store.claim("worker-b")
        self.store.transition(mission.mission_id, MissionState.FAILED, actor="worker-b", reason="two")
        with self.assertRaises(InvalidTransition):
            self.store.retry(mission.mission_id, actor="scheduler")

    def test_expired_lease_is_recovered_as_visible_failure(self) -> None:
        request = task()
        mission, _ = self.store.enqueue(
            request,
            decision(request),
            now="2026-08-15T10:00:00+00:00",
        )
        self.store.claim(
            "worker-a",
            lease_seconds=10,
            now="2026-08-15T10:00:01+00:00",
        )
        self.assertEqual(
            self.store.recover_expired(
                actor="lease-reaper",
                now="2026-08-15T10:00:05+00:00",
            ),
            [],
        )
        recovered = self.store.recover_expired(
            actor="lease-reaper",
            now="2026-08-15T10:00:12+00:00",
        )
        self.assertEqual([item.mission_id for item in recovered], [mission.mission_id])
        self.assertEqual(recovered[0].state, MissionState.FAILED)
        self.assertEqual(recovered[0].last_error, "worker lease expired")
        self.assertEqual(
            self.store.events(mission.mission_id)[-1].reason,
            "worker lease expired",
        )
        self.assertEqual(self.store.recover_expired(actor="lease-reaper"), [])

    def test_done_requires_evidence_for_every_declared_criterion(self) -> None:
        request = task()
        mission, _ = self.store.enqueue(request, decision(request))
        self.store.claim("worker-a")
        incomplete = EvidenceBundle(
            commit_sha="c" * 40,
            tests=("tests:pass",),
            artifacts=("report.json",),
            criteria={"queue tests pass": True},
            produced_by="worker-a",
        )
        with self.assertRaisesRegex(InvalidTransition, "declared acceptance criteria"):
            self.store.transition(
                mission.mission_id,
                MissionState.DONE,
                actor="worker-a",
                reason="done",
                evidence=incomplete,
            )

    def test_done_persists_proof_and_event(self) -> None:
        request = task()
        mission, _ = self.store.enqueue(request, decision(request))
        self.store.claim("worker-a")
        done = self.store.transition(
            mission.mission_id,
            MissionState.DONE,
            actor="worker-a",
            reason="all gates passed",
            evidence=proof(),
        )
        self.assertEqual(done.state, MissionState.DONE)
        self.assertEqual(done.evidence, proof())
        last_event = self.store.events(mission.mission_id)[-1]
        self.assertEqual(last_event.to_state, MissionState.DONE)
        self.assertEqual(last_event.evidence, proof())

    def test_reopening_store_preserves_queue_and_events(self) -> None:
        request = task()
        mission, _ = self.store.enqueue(request, decision(request))
        self.store.close()
        self.store = SQLiteMissionStore(self.db_path)
        restored = self.store.get(mission.mission_id)
        self.assertEqual(restored.task, request)
        self.assertEqual(len(self.store.events(mission.mission_id)), 1)

    def test_unknown_mission_is_explicit(self) -> None:
        with self.assertRaises(MissionNotFound):
            self.store.get("missing")

    def test_metrics_preserve_retries_rejections_and_human_actions(self) -> None:
        rejected_request = task(key="rejected")
        self.store.enqueue(rejected_request, decision(rejected_request, rejected=True))
        approved_request = task(key="approved", risk=RiskLevel.HIGH)
        approved, _ = self.store.enqueue(
            approved_request,
            decision(approved_request, approval=True),
        )
        self.store.approve(approved.mission_id, actor="reviewer")
        self.store.claim("worker-a")
        metrics = self.store.metrics()
        self.assertEqual(metrics["total_missions"], 2)
        self.assertEqual(metrics["rejected_missions"], 1)
        self.assertEqual(metrics["human_interventions"], 1)
        self.assertEqual(metrics["rejection_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
