from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import threading
import unittest

from automation_control_plane.engine import ConflictError, ControlPlane, KillSwitchError, LeaseLostError
from automation_control_plane.handlers import HandlerRegistry, HandlerResult, builtin_registry
from automation_control_plane.storage import ControlPlaneStore, StorageError
from tests.support import MutableClock, step, workflow


class DurableEngineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = ControlPlaneStore(Path(self.temporary.name) / "control.db")
        self.store.initialize()
        self.clock = MutableClock()
        self.control = ControlPlane(self.store, clock=self.clock)
        self.control.assign_role("worker-a", "worker", principal="admin")
        self.control.assign_role("worker-b", "worker", principal="admin")
        self.control.assign_role("approver", "approver", principal="admin")

    def register(self, value=None):
        return self.control.register_workflow(value or workflow(), principal="admin")

    def submit(self, key="key-1", **kwargs):
        return self.control.submit("test-flow", principal="admin", trigger={"type": "manual"}, idempotency_key=key, **kwargs)

    def test_end_to_end_dag_promotes_dependencies(self):
        self.register(workflow(steps=[step("a"), step("b", depends_on=["a"]), step("c", depends_on=["a", "b"])]))
        job = self.submit()
        self.assertEqual([item["state"] for item in job["steps"]], ["ready", "blocked", "blocked"])
        for expected in ("a", "b", "c"):
            result = self.control.execute_once(worker="worker-a")
            self.assertEqual(result["step_id"], expected)
        self.assertEqual(self.control.show_job(job["job_id"], principal="admin")["state"], "completed")

    def test_submission_is_idempotent(self):
        self.register()
        first = self.submit(payload={"same": True})
        second = self.submit(payload={"same": True})
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertTrue(second["idempotent_replay"])
        self.assertEqual(len(self.control.list_jobs(principal="admin")), 1)

    def test_idempotency_key_is_bound_to_the_complete_request(self):
        self.register(); self.submit(payload={"revision": 1})
        with self.assertRaises(ConflictError): self.submit(payload={"revision": 2})

    def test_workflow_versions_are_immutable_and_inactive_can_be_activated(self):
        value = workflow()
        inactive = self.control.register_workflow(value, principal="admin", activate=False)
        self.assertFalse(inactive["active"])
        activated = self.control.register_workflow(value, principal="admin", activate=True)
        self.assertTrue(activated["active"]); self.assertFalse(activated["created"])
        changed = workflow(); changed["description"] = "Different content."
        with self.assertRaises(ConflictError): self.control.register_workflow(changed, principal="admin")

    def test_concurrent_submission_has_one_winner(self):
        self.register()
        barrier = threading.Barrier(2)
        def submit_once():
            barrier.wait()
            return self.control.submit("test-flow", principal="admin", trigger={"type": "manual"}, idempotency_key="same")
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = list(pool.map(lambda _: submit_once(), range(2)))
        self.assertEqual(jobs[0]["job_id"], jobs[1]["job_id"])
        self.assertEqual(len(self.control.list_jobs(principal="admin")), 1)

    def test_atomic_claim_allows_only_one_worker(self):
        self.register(); self.submit()
        barrier = threading.Barrier(2)
        def claim(name):
            barrier.wait(); return self.control.claim_step(worker=name)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, ("worker-a", "worker-b")))
        self.assertEqual(sum(item is not None for item in results), 1)
        shown = self.control.show_job(self.control.list_jobs(principal="admin")[0]["job_id"], principal="admin")
        self.assertNotIn("lease_token", shown["steps"][0]); self.assertTrue(shown["steps"][0]["lease_active"])

    def test_approval_is_bound_and_required(self):
        self.register(workflow(steps=[step(approval="required")]))
        job = self.submit()
        self.assertIsNone(self.control.claim_step(worker="worker-a"))
        current = self.control.decide_approval(job["job_id"], "step-a", principal="approver", decision="approved", reason="reviewed")
        self.assertEqual(current["approvals"][0]["workflow_digest"], current["workflow_digest"])
        self.assertIsNotNone(self.control.claim_step(worker="worker-a"))

    def test_rejected_approval_fails_job_and_skips_dependents(self):
        self.register(workflow(steps=[step("a", approval="required"), step("b", depends_on=["a"])]))
        job = self.submit()
        result = self.control.decide_approval(job["job_id"], "a", principal="approver", decision="rejected", reason="unsafe")
        self.assertEqual(result["state"], "failed")
        self.assertEqual([item["state"] for item in result["steps"]], ["failed", "skipped"])

    def test_self_approval_is_denied_without_override(self):
        self.control.assign_role("dual", "operator", principal="admin")
        self.control.assign_role("dual", "approver", principal="admin")
        self.register(workflow(steps=[step(approval="required")]))
        job = self.control.submit("test-flow", principal="dual", trigger={"type": "manual"}, idempotency_key="dual")
        with self.assertRaises(PermissionError):
            self.control.decide_approval(job["job_id"], "step-a", principal="dual", decision="approved", reason="self")

    def test_optimistic_job_version_prevents_stale_cancel(self):
        self.register(); job = self.submit()
        with self.assertRaises(ConflictError):
            self.control.cancel_job(job["job_id"], principal="admin", reason="stale", expected_version=99)
        cancelled = self.control.cancel_job(job["job_id"], principal="admin", reason="requested", expected_version=job["version"])
        self.assertEqual(cancelled["state"], "cancelled")

    def test_retry_then_success(self):
        calls = {"count": 0}
        registry = builtin_registry()
        def flaky(context):
            calls["count"] += 1
            if calls["count"] == 1: raise RuntimeError("synthetic failure")
            return HandlerResult({"ok": True}, 0)
        registry.register("flaky", flaky)
        self.control = ControlPlane(self.store, registry=registry, clock=self.clock)
        with self.store.transaction() as connection:
            connection.execute("INSERT INTO role_capabilities(role_name, capability) VALUES ('worker', 'handler:flaky')")
        self.register(workflow(steps=[step(handler="flaky", attempts=2)]))
        job = self.submit()
        self.assertEqual(self.control.execute_once(worker="worker-a")["status"], "retry_scheduled")
        self.assertEqual(self.control.execute_once(worker="worker-a")["status"], "succeeded")
        self.assertEqual(self.control.show_job(job["job_id"], principal="admin")["state"], "completed")

    def test_multibyte_handler_error_is_bounded_without_orphaning_lease(self):
        registry = builtin_registry()

        def fail_with_multibyte_message(context):
            raise RuntimeError("é" * 2_000)

        registry.register("unicode.failure", fail_with_multibyte_message)
        self.control = ControlPlane(self.store, registry=registry, clock=self.clock)
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT INTO role_capabilities(role_name, capability) VALUES ('worker', 'handler:unicode.failure')"
            )
        self.register(workflow(steps=[step(handler="unicode.failure", attempts=1)]))
        job = self.submit()
        outcome = self.control.execute_once(worker="worker-a")
        shown = self.control.show_job(job["job_id"], principal="admin")
        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(shown["state"], "failed")
        self.assertLessEqual(len(shown["steps"][0]["error"].encode("utf-8")), 1_024)
        self.assertFalse(shown["steps"][0]["lease_active"])

    def test_bound_approval_survives_a_retry_of_unchanged_input(self):
        calls = {"count": 0}; registry = builtin_registry()
        def approved_flaky(context):
            calls["count"] += 1
            if calls["count"] == 1: raise RuntimeError("synthetic failure")
            return HandlerResult({"ok": True}, 0)
        registry.register("approved.flaky", approved_flaky); self.control = ControlPlane(self.store, registry=registry, clock=self.clock)
        with self.store.transaction() as connection:
            connection.execute("INSERT INTO role_capabilities(role_name, capability) VALUES ('worker', 'handler:approved.flaky')")
        self.register(workflow(steps=[step(handler="approved.flaky", approval="required", attempts=2)])); job = self.submit()
        self.control.decide_approval(job["job_id"], "step-a", principal="approver", decision="approved", reason="reviewed input")
        self.assertEqual(self.control.execute_once(worker="worker-a")["status"], "retry_scheduled")
        self.assertEqual(self.control.execute_once(worker="worker-a")["status"], "succeeded")

    def test_crash_lease_is_recovered_and_old_token_is_rejected(self):
        self.register(workflow(steps=[step(attempts=2)])); job = self.submit()
        first = self.control.claim_step(worker="worker-a", lease_seconds=5)
        self.clock.advance(6)
        recovered = self.control.recover(principal="admin")
        self.assertEqual(recovered["leases_retried"], 1)
        second = self.control.claim_step(worker="worker-b", lease_seconds=5)
        self.assertNotEqual(first["lease_token"], second["lease_token"])
        with self.assertRaises(LeaseLostError):
            self.control.complete_step(first, worker="worker-a", result=HandlerResult({}, 0))
        self.control.complete_step(second, worker="worker-b", result=HandlerResult({}, 0))
        self.assertEqual(self.control.show_job(job["job_id"], principal="admin")["state"], "completed")

    def test_retry_limit_fails_after_repeated_crash(self):
        self.register(workflow(steps=[step(attempts=1)])); job = self.submit()
        self.control.claim_step(worker="worker-a", lease_seconds=2); self.clock.advance(3)
        result = self.control.recover(principal="admin")
        self.assertEqual(result["leases_failed"], 1)
        self.assertEqual(self.control.show_job(job["job_id"], principal="admin")["state"], "failed")

    def test_recovery_snapshot_cannot_process_sibling_after_job_terminalizes(self):
        self.register(workflow(steps=[step("a", attempts=1), step("b", attempts=1)]))
        job = self.submit()
        self.control.claim_step(worker="worker-a", lease_seconds=2)
        self.control.claim_step(worker="worker-b", lease_seconds=2)
        self.clock.advance(3)
        result = self.control.recover(principal="admin")
        shown = self.control.show_job(job["job_id"], principal="admin")
        self.assertEqual(result["leases_failed"], 1)
        self.assertEqual(shown["state"], "failed")
        self.assertEqual(shown["budget_reserved"], 0)
        self.assertTrue(all(item["state"] in {"failed", "cancelled"} for item in shown["steps"]))
        self.assertTrue(all(not item["lease_active"] and item["reserved_cost"] == 0 for item in shown["steps"]))

    def test_budget_override_blocks_estimated_step(self):
        self.register(workflow(steps=[step(estimated_cost=2)], budget=2)); job = self.submit(budget_units=1)
        self.assertIsNone(self.control.claim_step(worker="worker-a"))
        current = self.control.show_job(job["job_id"], principal="admin")
        self.assertEqual(current["state"], "failed")
        self.assertEqual(current["steps"][0]["error"], "budget_exhausted")

    def test_actual_cost_cannot_cross_budget(self):
        registry = builtin_registry()
        registry.register("costly", lambda context: HandlerResult({"done": True}, 2))
        self.control = ControlPlane(self.store, registry=registry, clock=self.clock)
        with self.store.transaction() as connection:
            connection.execute("INSERT INTO role_capabilities(role_name, capability) VALUES ('worker', 'handler:costly')")
        self.register(workflow(steps=[step(handler="costly", estimated_cost=1, attempts=1)], budget=1)); job = self.submit()
        self.assertEqual(self.control.execute_once(worker="worker-a")["status"], "failed")
        current = self.control.show_job(job["job_id"], principal="admin")
        self.assertEqual(current["budget_spent"], 0)

    def test_dry_run_never_invokes_handler(self):
        calls = {"count": 0}; registry = builtin_registry()
        def observed(context): calls["count"] += 1; return HandlerResult({}, 0)
        registry.register("observed", observed); self.control = ControlPlane(self.store, registry=registry, clock=self.clock)
        with self.store.transaction() as connection:
            connection.execute("INSERT INTO role_capabilities(role_name, capability) VALUES ('worker', 'handler:observed')")
        self.register(workflow(steps=[step(handler="observed")])); self.submit(dry_run=True)
        self.assertEqual(self.control.execute_once(worker="worker-a")["status"], "succeeded")
        self.assertEqual(calls["count"], 0)

    def test_global_kill_blocks_submission(self):
        self.register(); self.control.set_kill_switch(scope="global", scope_id="", enabled=True, reason="incident", principal="admin")
        self.assertTrue(self.control.list_kill_switches(principal="admin")[0]["enabled"])
        with self.assertRaises(KillSwitchError): self.submit()

    def test_workflow_kill_blocks_claim_and_discards_late_completion(self):
        self.register(); job = self.submit(); lease = self.control.claim_step(worker="worker-a")
        self.control.set_kill_switch(scope="workflow", scope_id="test-flow", enabled=True, reason="incident", principal="admin")
        with self.assertRaises(LeaseLostError):
            self.control.complete_step(lease, worker="worker-a", result=HandlerResult({}, 0))
        self.assertEqual(self.control.show_job(job["job_id"], principal="admin")["state"], "cancelled")

    def test_kill_fence_survives_disable_and_rejects_old_lease(self):
        self.register(); job = self.submit(); lease = self.control.claim_step(worker="worker-a")
        enabled = self.control.set_kill_switch(
            scope="workflow", scope_id="test-flow", enabled=True, reason="incident", principal="admin"
        )
        self.control.set_kill_switch(
            scope="workflow", scope_id="test-flow", enabled=False, reason="resolved",
            principal="admin", expected_version=enabled["version"],
        )
        with self.assertRaises(LeaseLostError):
            self.control.complete_step(lease, worker="worker-a", result=HandlerResult({}, 0))
        shown = self.control.show_job(job["job_id"], principal="admin")
        self.assertEqual(shown["state"], "cancelled")
        self.assertEqual(shown["fence_generation"], 1)
        self.assertTrue(all(item["state"] in {"succeeded", "failed", "cancelled", "skipped"} for item in shown["steps"]))

    def test_deadline_recovery_fails_job(self):
        self.register(workflow(deadline=5)); job = self.submit(deadline_seconds=5)
        self.clock.advance(6); recovery = self.control.recover(principal="admin")
        self.assertEqual(recovery["deadlines_failed"], 1)
        self.assertEqual(self.control.show_job(job["job_id"], principal="admin")["state"], "failed")

    def test_undeclared_trigger_is_rejected(self):
        self.register()
        with self.assertRaises(ValueError):
            self.control.submit("test-flow", principal="admin", trigger={"type": "webhook", "event": "x"}, idempotency_key="x")

    def test_webhook_and_schedule_are_persisted(self):
        triggers = [{"type": "webhook", "event": "test.ready"}, {"type": "scheduled", "interval_seconds": 60}]
        self.register(workflow(triggers=triggers))
        hook = self.control.submit("test-flow", principal="admin", trigger=triggers[0], idempotency_key="hook")
        scheduled = self.control.submit("test-flow", principal="admin", trigger=triggers[1], idempotency_key="schedule")
        self.assertEqual(hook["trigger_type"], "webhook"); self.assertEqual(scheduled["trigger_type"], "scheduled")

    def test_audit_detects_tampering(self):
        self.register(); self.submit(); self.assertTrue(self.store.verify_audit()["valid"])
        with sqlite3.connect(self.store.path) as connection:
            connection.execute("UPDATE events SET payload_json = ? WHERE sequence = 1", ('{"tampered":true}',))
        result = self.store.verify_audit()
        self.assertFalse(result["valid"]); self.assertTrue(result["errors"])

    def test_audit_anchor_detects_tail_deletion(self):
        self.register(); self.submit(); before = self.store.verify_audit()
        with sqlite3.connect(self.store.path) as connection:
            tail = connection.execute("SELECT MAX(sequence) FROM events").fetchone()[0]
            connection.execute("DELETE FROM outbox WHERE event_sequence = ?", (tail,))
            connection.execute("DELETE FROM events WHERE sequence = ?", (tail,))
        after = self.store.verify_audit()
        self.assertFalse(after["valid"])
        self.assertTrue(any(item["reason"] == "event_count_anchor_mismatch" for item in after["errors"]))

    def test_result_receipt_is_bound_to_anchored_success_event(self):
        self.register(); job = self.submit(); self.control.execute_once(worker="worker-a")
        with sqlite3.connect(self.store.path) as connection:
            connection.execute(
                "UPDATE step_runs SET result_json = ? WHERE job_id = ?",
                ('{"forged":true}', job["job_id"]),
            )
        audit = self.store.verify_audit()
        self.assertFalse(audit["valid"])
        self.assertTrue(any(item["reason"] == "result_event_mismatch" for item in audit["errors"]))
        with self.assertRaisesRegex(StorageError, "result integrity"):
            self.store.get_job(job["job_id"])

    def test_outbox_is_atomic_and_reconciles_missing_record(self):
        self.register(); self.submit()
        events = self.store.list_events(limit=100); outbox = self.store.list_outbox(limit=100)
        self.assertEqual(len(events), len(outbox))
        with sqlite3.connect(self.store.path) as connection: connection.execute("DELETE FROM outbox WHERE sequence = 1")
        result = self.control.reconcile(principal="admin")
        self.assertEqual(result["outbox_created"], 1)

    def test_audit_binds_outbox_envelopes_to_their_events(self):
        self.register(); self.submit()
        with sqlite3.connect(self.store.path) as connection:
            connection.execute("UPDATE outbox SET payload_json = '{}' WHERE event_sequence = 1")
        result = self.store.verify_audit()
        self.assertFalse(result["valid"])
        self.assertTrue(any(item["reason"] == "outbox_payload_mismatch" for item in result["errors"]))

    def test_outbox_delivery_uses_an_atomic_expiring_lease(self):
        self.control.assign_role("dispatcher", "dispatcher", principal="admin")
        self.register()
        item = self.control.claim_outbox(worker="dispatcher", lease_seconds=5)
        self.assertIsNotNone(item)
        self.control.acknowledge_outbox(item["sequence"], item["lease_token"], worker="dispatcher")
        with self.assertRaises(LeaseLostError):
            self.control.acknowledge_outbox(item["sequence"], item["lease_token"], worker="dispatcher")
        self.assertEqual(self.store.list_outbox(state="delivered", limit=100)[0]["sequence"], item["sequence"])

    def test_backup_restore_preserves_audit_and_jobs(self):
        self.register(); job = self.submit(); backup = Path(self.temporary.name) / "backup.db"
        self.store.backup(backup); restored_path = Path(self.temporary.name) / "restored.db"
        restored = ControlPlaneStore.restore(backup, restored_path)
        self.assertTrue(restored.verify_audit()["valid"])
        self.assertEqual(restored.get_job(job["job_id"])["job_id"], job["job_id"])

    def test_restore_rejects_an_invalid_audit_chain(self):
        self.register(); backup = Path(self.temporary.name) / "tampered-backup.db"; self.store.backup(backup)
        with sqlite3.connect(backup) as connection:
            connection.execute("UPDATE events SET payload_json = ? WHERE sequence = 1", ('{"tampered":true}',))
        with self.assertRaises(StorageError):
            ControlPlaneStore.restore(backup, Path(self.temporary.name) / "must-not-exist.db")

    def test_restore_rejects_incomplete_schema_before_replacement(self):
        malformed = Path(self.temporary.name) / "malformed.db"
        with sqlite3.connect(malformed) as connection:
            connection.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute("INSERT INTO meta VALUES ('schema_version', '1')")
            connection.execute(
                """CREATE TABLE events(sequence INTEGER PRIMARY KEY, event_id TEXT, event_type TEXT,
                   entity_type TEXT, entity_id TEXT, principal TEXT, occurred_at TEXT,
                   payload_json TEXT, previous_hash TEXT, event_hash TEXT)"""
            )
        target = Path(self.temporary.name) / "must-remain-absent.db"
        with self.assertRaisesRegex(StorageError, "incomplete"):
            ControlPlaneStore.restore(malformed, target)
        self.assertFalse(target.exists())

    def test_restore_rejects_unexpected_executable_schema_object(self):
        source = Path(self.temporary.name) / "triggered.db"
        target = Path(self.temporary.name) / "restored-triggered.db"
        self.store.backup(source)
        with sqlite3.connect(source) as connection:
            connection.execute(
                """CREATE TRIGGER forged_after_job_insert AFTER INSERT ON jobs
                   BEGIN UPDATE meta SET value = value WHERE key = 'schema_version'; END"""
            )
        with self.assertRaisesRegex(StorageError, "executable objects"):
            ControlPlaneStore.restore(source, target)
        self.assertFalse(target.exists())

    def test_initialize_refuses_future_schema_without_mutation(self):
        future = Path(self.temporary.name) / "future.db"
        with sqlite3.connect(future) as connection:
            connection.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute("INSERT INTO meta VALUES ('schema_version', '999')")
        with self.assertRaisesRegex(StorageError, "unsupported"):
            ControlPlaneStore(future).initialize()
        with sqlite3.connect(future) as connection:
            tables = [row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )]
        self.assertEqual(tables, ["meta"])

    def test_initialize_transactionally_migrates_schema_v1(self):
        with sqlite3.connect(self.store.path) as connection:
            jobs_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'jobs'"
            ).fetchone()[0]
            steps_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'step_runs'"
            ).fetchone()[0]
            jobs_v1 = jobs_sql.replace("CREATE TABLE jobs", "CREATE TABLE jobs_v1")
            jobs_v1 = jobs_v1.replace(
                "                    budget_reserved INTEGER NOT NULL CHECK(budget_reserved >= 0 AND budget_spent + budget_reserved <= budget_limit),\n",
                "",
            ).replace(
                "                    fence_generation INTEGER NOT NULL CHECK(fence_generation >= 0),\n",
                "",
            )
            steps_v1 = steps_sql.replace("CREATE TABLE step_runs", "CREATE TABLE step_runs_v1")
            for column in (
                "                    lease_fence_generation INTEGER,\n",
                "                    result_digest TEXT,\n",
                "                    reserved_cost INTEGER NOT NULL CHECK(reserved_cost >= 0),\n",
            ):
                steps_v1 = steps_v1.replace(column, "")
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute(jobs_v1)
            connection.execute(steps_v1)
            connection.execute("DROP TABLE step_runs")
            connection.execute("DROP TABLE jobs")
            connection.execute("ALTER TABLE jobs_v1 RENAME TO jobs")
            connection.execute("ALTER TABLE step_runs_v1 RENAME TO step_runs")
            connection.execute("CREATE INDEX jobs_state_idx ON jobs(state, deadline_at)")
            connection.execute(
                "CREATE INDEX claim_step_idx ON step_runs(state, available_at, lease_expires_at)"
            )
            connection.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")
            connection.execute("DELETE FROM meta WHERE key IN ('audit_event_count', 'audit_head_hash')")
        self.store.initialize()
        with sqlite3.connect(self.store.path) as connection:
            version = connection.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()[0]
            job_columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
            step_columns = {row[1] for row in connection.execute("PRAGMA table_info(step_runs)")}
        self.assertEqual(version, "2")
        self.assertTrue({"budget_reserved", "fence_generation"} <= job_columns)
        self.assertTrue({"lease_fence_generation", "result_digest", "reserved_cost"} <= step_columns)
        self.assertTrue(self.store.verify_audit()["valid"])

    def test_backup_refuses_dangling_symlink_without_touching_target(self):
        redirected = Path(self.temporary.name) / "redirected.db"
        requested = Path(self.temporary.name) / "backup-link.db"
        requested.symlink_to(redirected)
        with self.assertRaises(StorageError):
            self.store.backup(requested)
        self.assertTrue(requested.is_symlink())
        self.assertFalse(redirected.exists())

    def test_unknown_principal_is_denied(self):
        self.register()
        with self.assertRaises(PermissionError):
            self.control.submit("test-flow", principal="unknown", trigger={"type": "manual"}, idempotency_key="x")
        with self.assertRaises(PermissionError): self.control.assign_role("unknown", "admin", principal="unknown")

    def test_unregistered_handler_is_never_executed(self):
        self.register(workflow(steps=[step(handler="missing")]))
        with self.store.transaction() as connection: connection.execute("INSERT INTO role_capabilities(role_name, capability) VALUES ('worker', 'handler:missing')")
        self.submit(); self.assertIsNone(self.control.claim_step(worker="worker-a"))

    def test_registry_capability_cannot_be_weakened_by_workflow_author(self):
        calls: list[str] = []
        registry = HandlerRegistry()
        registry.register(
            "privileged.action",
            lambda context: (calls.append(context.step_id) or HandlerResult({"privileged": True}, 0)),
        )
        self.control = ControlPlane(self.store, registry=registry, clock=self.clock)
        self.control.assign_role("author", "operator", principal="admin")
        crafted = step("escalate", handler="privileged.action")
        crafted["required_capability"] = "handler:noop"
        self.control.register_workflow(workflow(workflow_id="rbac-flow", steps=[crafted]), principal="author")
        self.control.submit(
            "rbac-flow", principal="author", trigger={"type": "manual"}, idempotency_key="rbac"
        )
        self.assertNotIn("handler:privileged.action", self.store.capabilities("worker-a"))
        self.assertEqual(self.control.execute_once(worker="worker-a")["status"], "idle")
        self.assertEqual(calls, [])

    def test_terminal_failure_fences_parallel_lease_and_recovery_cannot_resurrect(self):
        self.register(workflow(steps=[step("a", attempts=1), step("b", attempts=2)])); job = self.submit()
        first = self.control.claim_step(worker="worker-a", lease_seconds=5)
        second = self.control.claim_step(worker="worker-b", lease_seconds=5)
        self.control.fail_step(first, worker="worker-a", error="terminal failure")
        shown = self.control.show_job(job["job_id"], principal="admin")
        self.assertEqual(shown["state"], "failed")
        self.assertEqual({item["step_id"]: item["state"] for item in shown["steps"]}, {"a": "failed", "b": "cancelled"})
        self.assertEqual(shown["budget_reserved"], 0)
        with self.assertRaises(LeaseLostError):
            self.control.complete_step(second, worker="worker-b", result=HandlerResult({}, 0))
        self.clock.advance(6)
        self.assertEqual(self.control.recover(principal="admin")["leases_retried"], 0)
        self.assertEqual(self.control.show_job(job["job_id"], principal="admin")["state"], "failed")

    def test_budget_is_reserved_before_effect_and_settled_atomically(self):
        self.register(
            workflow(
                budget=12,
                steps=[step("a", estimated_cost=6, attempts=1), step("b", estimated_cost=6, attempts=1)],
            )
        )
        job = self.submit(budget_units=10)
        first = self.control.claim_step(worker="worker-a")
        self.assertIsNone(self.control.claim_step(worker="worker-b"))
        during = self.control.show_job(job["job_id"], principal="admin")
        self.assertEqual(during["budget_reserved"], 6)
        self.assertEqual({item["step_id"]: item["state"] for item in during["steps"]}, {"a": "leased", "b": "ready"})
        self.control.complete_step(first, worker="worker-a", result=HandlerResult({}, 0))
        second = self.control.claim_step(worker="worker-b")
        self.assertIsNotNone(second)
        settled = self.control.show_job(job["job_id"], principal="admin")
        self.assertEqual(settled["budget_spent"], 0)
        self.assertEqual(settled["budget_reserved"], 6)

    def test_clock_is_sampled_after_database_lock_acquisition(self):
        self.register(); self.submit(); lease = self.control.claim_step(worker="worker-a", lease_seconds=5)
        requested = threading.Event()
        original_transaction = self.store.transaction

        @contextmanager
        def observed_transaction():
            requested.set()
            with original_transaction() as connection:
                yield connection

        self.store.transaction = observed_transaction  # type: ignore[method-assign]
        with ThreadPoolExecutor(max_workers=1) as pool:
            with original_transaction():
                future = pool.submit(
                    self.control.complete_step,
                    lease,
                    worker="worker-a",
                    result=HandlerResult({}, 0),
                )
                self.assertTrue(requested.wait(2))
                self.clock.advance(10)
            with self.assertRaises(LeaseLostError):
                future.result(timeout=5)

    def test_pending_approval_tracks_job_version_and_preserves_running_state(self):
        self.register(workflow(steps=[step("gate", approval="required"), step("parallel")]))
        job = self.submit(); self.control.claim_step(worker="worker-a")
        before = self.control.show_job(job["job_id"], principal="admin")
        self.assertEqual(before["state"], "running")
        self.assertEqual(before["approvals"][0]["job_version"], before["version"])
        decided = self.control.decide_approval(
            job["job_id"], "gate", principal="approver", decision="approved", reason="reviewed"
        )
        self.assertEqual(decided["state"], "running")
        self.assertEqual(decided["approvals"][0]["job_version"], decided["version"])

    def test_stale_stored_approval_job_version_is_rejected(self):
        self.register(workflow(steps=[step(approval="required")]))
        job = self.submit()
        with self.store.transaction() as connection:
            connection.execute("UPDATE approvals SET job_version = job_version + 1 WHERE job_id = ?", (job["job_id"],))
        with self.assertRaisesRegex(ConflictError, "binding"):
            self.control.decide_approval(
                job["job_id"], "step-a", principal="approver", decision="approved", reason="stale"
            )

    def test_claim_scans_past_ineligible_prefix_without_starvation(self):
        self.control.register_workflow(
            workflow(workflow_id="ineligible", steps=[step(handler="not.registered")]), principal="admin"
        )
        self.control.register_workflow(workflow(workflow_id="eligible"), principal="admin")
        for index in range(100):
            self.control.submit(
                "ineligible", principal="admin", trigger={"type": "manual"}, idempotency_key=f"bad-{index}"
            )
        eligible = self.control.submit(
            "eligible", principal="admin", trigger={"type": "manual"}, idempotency_key="good"
        )
        lease = self.control.claim_step(worker="worker-a")
        self.assertIsNotNone(lease)
        self.assertEqual(lease["job_id"], eligible["job_id"])

    def test_waiting_approval_state_wins_over_only_blocked_descendants(self):
        self.register(workflow(steps=[step("gate", approval="required"), step("after", depends_on=["gate"])]))
        job = self.submit()
        self.assertEqual(job["state"], "waiting_approval")
        self.assertFalse(any(item["state"] == "ready" for item in job["steps"]))


if __name__ == "__main__":
    unittest.main()
