from pathlib import Path
import tempfile
import unittest

from fixtures import evidence, graph, graph_dict
from taskgraph.engine import TaskGraphEngine
from taskgraph.models import ContractError, GraphSpec
from taskgraph.store import TaskStore


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = TaskStore(Path(self.temp.name) / "graph.db")
        self.engine = TaskGraphEngine(self.store)
        self.graph = graph()
        self.engine.register(self.graph)

    def tearDown(self):
        self.store.close(); self.temp.cleanup()

    def test_register_is_idempotent(self):
        self.assertFalse(self.engine.register(self.graph))
        self.assertEqual(len(self.store.task_rows(self.graph.graph_id)), 3)

    def test_register_conflict_fails(self):
        raw = graph_dict(); raw["version"] = "1.2.4"
        with self.assertRaisesRegex(ContractError, "different contract"):
            self.engine.register(GraphSpec.from_dict(raw))

    def test_claim_first_ready_task(self):
        claim = self.engine.claim(self.graph.graph_id, "worker", 100)
        self.assertEqual(claim["task"]["task_id"], "a-contract")
        self.assertEqual(claim["runtime"]["attempts"], 1)

    def test_downstream_not_claimable_before_dependency(self):
        self.engine.claim(self.graph.graph_id, "worker", 100)
        self.assertIsNone(self.engine.claim(self.graph.graph_id, "other", 101))

    def test_complete_requires_evidence(self):
        self.engine.claim(self.graph.graph_id, "worker", 100)
        with self.assertRaisesRegex(ContractError, "missing required evidence"):
            self.engine.complete(self.graph.graph_id, "a-contract", "worker", [], {}, "event")

    def test_complete_requires_lease_owner(self):
        self.engine.claim(self.graph.graph_id, "worker", 100)
        with self.assertRaisesRegex(ContractError, "not running"):
            self.engine.complete(self.graph.graph_id, "a-contract", "other", evidence("decision"), {}, "event")

    def test_complete_unlocks_dependency(self):
        self.engine.claim(self.graph.graph_id, "worker", 100)
        self.engine.complete(self.graph.graph_id, "a-contract", "worker", evidence("decision"), {"ok":True}, "complete-a")
        claim = self.engine.claim(self.graph.graph_id, "worker2", 101)
        self.assertEqual(claim["task"]["task_id"], "b-build")

    def test_complete_replay_idempotent(self):
        self.engine.claim(self.graph.graph_id, "worker", 100)
        ev = evidence("decision")
        self.assertTrue(self.engine.complete(self.graph.graph_id, "a-contract", "worker", ev, {}, "complete-a"))
        self.assertFalse(self.engine.complete(self.graph.graph_id, "a-contract", "worker", ev, {}, "complete-a"))

    def test_complete_event_conflict_fails(self):
        self.engine.claim(self.graph.graph_id, "worker", 100)
        ev = evidence("decision")
        self.engine.complete(self.graph.graph_id, "a-contract", "worker", ev, {}, "complete-a")
        with self.assertRaisesRegex(ContractError, "conflict"):
            self.engine.complete(self.graph.graph_id, "a-contract", "worker", ev, {"changed":True}, "complete-a")

    def test_failure_retries_then_terminal(self):
        self.engine.claim(self.graph.graph_id, "worker", 100)
        state1, _ = self.engine.fail(self.graph.graph_id, "a-contract", "worker", "boom", "fail-1")
        self.assertEqual(state1, "waiting")
        self.engine.claim(self.graph.graph_id, "worker", 101)
        state2, _ = self.engine.fail(self.graph.graph_id, "a-contract", "worker", "boom again", "fail-2")
        self.assertEqual(state2, "failed")
        snapshot = self.engine.snapshot(self.graph.graph_id)
        self.assertEqual(snapshot["counts"]["rejected"], 2)

    def test_failure_replay_idempotent(self):
        self.engine.claim(self.graph.graph_id, "worker", 100)
        first = self.engine.fail(self.graph.graph_id, "a-contract", "worker", "boom", "fail-1")
        second = self.engine.fail(self.graph.graph_id, "a-contract", "worker", "boom", "fail-1")
        self.assertEqual(first, ("waiting", True)); self.assertEqual(second, ("waiting", False))

    def test_failure_conflict_fails(self):
        self.engine.claim(self.graph.graph_id, "worker", 100)
        self.engine.fail(self.graph.graph_id, "a-contract", "worker", "boom", "fail-1")
        with self.assertRaisesRegex(ContractError, "conflict"):
            self.engine.fail(self.graph.graph_id, "a-contract", "worker", "changed", "fail-1")

    def test_expired_lease_resumes(self):
        self.engine.claim(self.graph.graph_id, "worker", 100, lease_seconds=5)
        self.assertEqual(self.engine.resume_expired(self.graph.graph_id, 105), 1)
        claim = self.engine.claim(self.graph.graph_id, "worker2", 106)
        self.assertEqual(claim["runtime"]["attempts"], 2)

    def test_expired_final_attempt_fails_and_rejects(self):
        self.engine.claim(self.graph.graph_id, "worker", 100, lease_seconds=1)
        self.engine.resume_expired(self.graph.graph_id, 101)
        self.engine.claim(self.graph.graph_id, "worker", 102, lease_seconds=1)
        self.engine.resume_expired(self.graph.graph_id, 103)
        snapshot = self.engine.snapshot(self.graph.graph_id)
        self.assertEqual(snapshot["counts"]["failed"], 1)
        self.assertEqual(snapshot["counts"]["rejected"], 2)

    def test_worker_and_lease_bounded(self):
        for worker, lease in (("", 10), ("w", 0), ("w", 3601)):
            with self.assertRaises(ContractError): self.engine.claim(self.graph.graph_id, worker, 1, lease)

    def test_snapshot_not_success_until_all_done(self):
        self.assertFalse(self.engine.snapshot(self.graph.graph_id)["success"])

    def test_full_graph_success_and_metrics(self):
        now = 1
        for task_id, kinds in (("a-contract",("decision",)),("b-build",("commit","test")),("c-review",("decision",))):
            claim = self.engine.claim(self.graph.graph_id, "worker", now)
            self.assertEqual(claim["task"]["task_id"], task_id)
            self.engine.complete(self.graph.graph_id, task_id, "worker", evidence(*kinds), {}, f"complete-{task_id}")
            now += 1
        snapshot = self.engine.snapshot(self.graph.graph_id)
        self.assertTrue(snapshot["success"]); self.assertEqual(snapshot["counts"]["done"], 3)

