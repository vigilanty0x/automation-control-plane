from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agent_inbox import (
    AgentInbox, AgentProfile, CapabilityMismatch, CompletionEvidence, EvidenceRequired,
    IdempotencyConflict, LeaseConflict, MissionNotFound, MissionStatus,
    NoMissionAvailable, StateConflict,
)
from helpers import Clock, evidence, profile, spec


class InboxTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(); self.path = Path(self.directory.name) / "nested" / "inbox.sqlite3"
        self.clock = Clock(); self.counter = 0
        def token(): self.counter += 1; return f"token-{self.counter}"
        self.inbox = AgentInbox(self.path, clock=self.clock, token_factory=token)
        self.inbox.register_agent(profile())
    def tearDown(self): self.directory.cleanup()
    def enqueue(self, key="task-1", **changes): return self.inbox.enqueue(spec(key, **changes))
    def claim(self, **changes): return self.inbox.claim("worker", lease_seconds=changes.get("lease_seconds", 10))

    def test_initialize_creates_database(self): self.assertTrue(self.path.is_file())
    def test_register_is_upsert(self):
        updated = self.inbox.register_agent(profile(max_running=2)); self.assertEqual(updated["max_running"], 2)
    def test_enqueue_queued(self): self.assertEqual(self.enqueue()["status"], "queued")
    def test_claim_exposes_bounded_mission_spec(self):
        self.enqueue(payload={"instruction": "synthetic"}); self.assertEqual(self.claim()["spec"]["payload"], {"instruction": "synthetic"})
    def test_enqueue_deterministic_id(self):
        first = self.enqueue(); second = self.inbox.enqueue(spec()); self.assertEqual(first["mission_id"], second["mission_id"])
    def test_enqueue_idempotent_has_one_event(self):
        first = self.enqueue(); replay = self.inbox.enqueue(spec()); self.assertEqual(len(replay["events"]), 1); self.assertEqual(first["logical_sha256"], replay["logical_sha256"])
    def test_enqueue_conflict(self):
        self.enqueue()
        with self.assertRaises(IdempotencyConflict): self.inbox.enqueue(spec(title="different"))
    def test_priority_order(self):
        low = self.enqueue("low", priority=1); high = self.enqueue("high", priority=99)
        self.assertEqual(self.claim()["mission_id"], high["mission_id"]); self.assertNotEqual(low["mission_id"], high["mission_id"])
    def test_claim_running_and_token(self):
        self.enqueue(); claim = self.claim(); self.assertEqual(claim["status"], "running"); self.assertEqual(claim["lease_token"], "token-1"); self.assertEqual(claim["attempts"], 1)
    def test_claim_token_not_persisted_in_get(self):
        mission = self.enqueue(); self.claim(); self.assertNotIn("lease_token", self.inbox.get(mission["mission_id"]))
    def test_claim_requires_registered_active_agent(self):
        self.enqueue()
        with self.assertRaises(CapabilityMismatch): self.inbox.claim("missing")
        self.inbox.register_agent(profile(active=False))
        with self.assertRaises(CapabilityMismatch): self.inbox.claim("worker")
    def test_claim_requires_capability(self):
        self.enqueue(required_capabilities=("gpu",))
        with self.assertRaises(NoMissionAvailable): self.claim()
    def test_claim_requires_permission(self):
        self.enqueue(required_permissions=("deploy",))
        with self.assertRaises(NoMissionAvailable): self.claim()
    def test_claim_requires_ownership(self):
        self.enqueue(owner_scope="other")
        with self.assertRaises(NoMissionAvailable): self.claim()
    def test_wildcard_ownership(self):
        self.inbox.register_agent(profile(ownership=("*",))); self.enqueue(owner_scope="other"); self.assertEqual(self.claim()["status"], "running")
    def test_running_limit(self):
        self.enqueue("one"); self.enqueue("two"); self.claim()
        with self.assertRaisesRegex(NoMissionAvailable, "limit"): self.claim()
    def test_lease_limit(self):
        self.enqueue()
        with self.assertRaises(LeaseConflict): self.claim(lease_seconds=61)
    def test_heartbeat_extends_lease(self):
        mission = self.enqueue(); claim = self.claim(); before = claim["lease_expires_at"]
        self.clock.value += 1; after = self.inbox.heartbeat(mission["mission_id"], claim["lease_token"], lease_seconds=20)
        self.assertGreater(after["lease_expires_at"], before)
    def test_heartbeat_wrong_token(self):
        mission = self.enqueue(); self.claim()
        with self.assertRaises(LeaseConflict): self.inbox.heartbeat(mission["mission_id"], "bad")
    def test_heartbeat_expired(self):
        mission = self.enqueue(); claim = self.claim(); self.clock.value += 11
        with self.assertRaisesRegex(LeaseConflict, "expired"): self.inbox.heartbeat(mission["mission_id"], claim["lease_token"])
    def test_proofless_done_blocked(self):
        mission = self.enqueue(); claim = self.claim()
        with self.assertRaises(EvidenceRequired): self.inbox.complete(mission["mission_id"], claim["lease_token"], CompletionEvidence("none"))
        self.assertEqual(self.inbox.get(mission["mission_id"])["status"], "running")
    def test_complete_records_evidence(self):
        mission = self.enqueue(); claim = self.claim(); result = self.inbox.complete(mission["mission_id"], claim["lease_token"], evidence())
        self.assertEqual(result["status"], "done"); self.assertEqual(result["evidence_sha256"], evidence().sha256); self.assertIsNone(result["lease_owner"])
    def test_complete_twice_rejected(self):
        mission = self.enqueue(); claim = self.claim(); self.inbox.complete(mission["mission_id"], claim["lease_token"], evidence())
        with self.assertRaises(StateConflict): self.inbox.complete(mission["mission_id"], claim["lease_token"], evidence())
    def test_wait_and_retry(self):
        mission = self.enqueue(); claim = self.claim(); waiting = self.inbox.wait(mission["mission_id"], claim["lease_token"], "dependency")
        self.assertEqual(waiting["status"], "waiting"); self.assertEqual(waiting["waiting_reason"], "dependency")
        self.assertEqual(self.inbox.retry(mission["mission_id"], actor="owner", reason="resolved")["status"], "queued")
    def test_reject_terminal(self):
        mission = self.enqueue(); claim = self.claim(); self.assertEqual(self.inbox.reject(mission["mission_id"], claim["lease_token"], "unsafe")["status"], "rejected")
    def test_retryable_failure_requeues(self):
        mission = self.enqueue(max_retries=1); claim = self.claim(); result = self.inbox.fail(mission["mission_id"], claim["lease_token"], "transient")
        self.assertEqual(result["status"], "queued"); self.assertEqual(result["failure_reason"], "transient")
    def test_nonretryable_failure_terminal(self):
        mission = self.enqueue(); claim = self.claim(); self.assertEqual(self.inbox.fail(mission["mission_id"], claim["lease_token"], "fatal", retryable=False)["status"], "failed")
    def test_retry_budget_exhaustion(self):
        mission = self.enqueue(max_retries=0); claim = self.claim(); result = self.inbox.fail(mission["mission_id"], claim["lease_token"], "fail")
        self.assertEqual(result["status"], "failed")
    def test_expired_lease_recovered_once(self):
        mission = self.enqueue(max_retries=1); self.claim(); self.clock.value += 11
        self.assertEqual(self.inbox.recover_expired(), {"recovered": 1, "failed": 0}); self.assertEqual(self.inbox.recover_expired(), {"recovered": 0, "failed": 0})
        self.assertEqual(self.inbox.get(mission["mission_id"])["status"], "queued")
    def test_expired_lease_exhaustion_failed(self):
        mission = self.enqueue(max_retries=0); self.claim(); self.clock.value += 11
        self.assertEqual(self.inbox.recover_expired(), {"recovered": 0, "failed": 1}); self.assertEqual(self.inbox.get(mission["mission_id"])["status"], "failed")
    def test_old_token_cannot_complete_reclaimed_work(self):
        mission = self.enqueue(max_retries=1); first = self.claim(); self.clock.value += 11; self.inbox.recover_expired(); second = self.claim()
        with self.assertRaises(LeaseConflict): self.inbox.complete(mission["mission_id"], first["lease_token"], evidence())
        self.assertEqual(self.inbox.complete(mission["mission_id"], second["lease_token"], evidence())["status"], "done")
    def test_missing_mission(self):
        with self.assertRaises(MissionNotFound): self.inbox.get("missing")
    def test_disagreement_and_escalation_visible(self):
        mission = self.enqueue(); self.inbox.record_signal(mission["mission_id"], event_id="d1", kind="disagreement", actor="reviewer", detail={"reason": "scope"}); self.inbox.record_signal(mission["mission_id"], event_id="e1", kind="escalation", actor="reviewer", detail={"to": "owner"})
        kinds = [event["kind"] for event in self.inbox.get(mission["mission_id"])["events"]]
        self.assertIn("disagreement", kinds); self.assertIn("escalation", kinds)
    def test_signal_idempotent(self):
        mission = self.enqueue(); first = self.inbox.record_signal(mission["mission_id"], event_id="d1", kind="disagreement", actor="r", detail={"x": 1}); second = self.inbox.record_signal(mission["mission_id"], event_id="d1", kind="disagreement", actor="r", detail={"x": 1}); self.assertEqual(first, second)
    def test_signal_id_conflict(self):
        mission = self.enqueue(); self.inbox.record_signal(mission["mission_id"], event_id="d1", kind="disagreement", actor="r", detail={"x": 1})
        with self.assertRaises(IdempotencyConflict): self.inbox.record_signal(mission["mission_id"], event_id="d1", kind="escalation", actor="r", detail={"x": 1})
    def test_list_filters(self):
        self.enqueue("a", owner_scope="demo"); self.enqueue("b", owner_scope="other")
        self.assertEqual(len(self.inbox.list(owner_scope="demo")), 1); self.assertEqual(len(self.inbox.list(status=MissionStatus.QUEUED)), 2)
    def test_list_limit_bounded(self):
        with self.assertRaises(StateConflict): self.inbox.list(limit=201)
    def test_persistence_across_instances(self):
        mission = self.enqueue(); self.assertEqual(AgentInbox(self.path).get(mission["mission_id"])["status"], "queued")
    def test_inventory(self):
        self.enqueue(); inventory = self.inbox.inventory(); self.assertEqual(inventory["missions"]["queued"], 1); self.assertEqual(inventory["agents"], {"total": 1, "active": 1}); self.assertEqual(len(inventory["inventory_sha256"]), 64)
    def test_agent_registry_visible(self):
        registry = self.inbox.list_agents(); self.assertEqual(registry[0]["agent_id"], "worker"); self.assertEqual(registry[0]["capabilities"], ["python", "review"]); self.assertEqual(len(registry[0]["profile_sha256"]), 64)
    def test_agent_registry_limit_bounded(self):
        with self.assertRaises(StateConflict): self.inbox.list_agents(limit=201)


if __name__ == "__main__": unittest.main()
