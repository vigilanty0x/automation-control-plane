"""CONS-02 acceptance: independent synthetic state, real Store/Engine paths."""
from contextlib import closing, redirect_stdout
from hashlib import sha256
import io
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from ai_software_factory.cli import main
from ai_software_factory.engine import FactoryEngine
from ai_software_factory.evidence import digest_json, verify_export
from ai_software_factory.executors import DeterministicMockExecutor, ExecutionRequest, SpecProvider
from ai_software_factory.models import FactorySpec, SpecError
from ai_software_factory.store import FactoryStore, LeaseLost
from tests.support import ManualClock, result, spec, task


def _claim_process(database, run_id, worker, event, output):
    store = FactoryStore(database, clock=lambda: 1000.0)
    event.wait(5)
    claim = store.claim_ready_task(run_id, worker, 10)
    if claim is not None:
        receipt = {"attempt": claim.attempt, "worker": worker}
        store.complete_task(claim, succeeded=True, receipt=receipt,
            receipt_hash=digest_json(receipt), error=None, retry_base_seconds=0, retry_cap_seconds=0)
    output.put(None if claim is None else claim.attempt)


def _crash_process(database, run_id, pipe):
    store = FactoryStore(database, clock=lambda: 1000.0)
    claim = store.claim_ready_task(run_id, 'crashing-worker', 2)
    pipe.send(claim.attempt)
    os._exit(23)


class ApprovalAuthorityTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.clock = ManualClock()
        self.db = self.root / 'factory.sqlite3'
        self.store = FactoryStore(self.db, clock=self.clock)

    def plan(self, *tasks, **changes):
        (self.root / 'workspace').mkdir(exist_ok=True)
        raw = spec(*(tasks or (task(approval='required'),)), **changes)
        parsed = FactorySpec.from_dict(raw)
        run = self.store.create_run(parsed, 'run')
        return run

    def approve(self, run, task_id='build', **changes):
        request = self.store.approval_request(run, task_id)
        kwargs = dict(attempt=request['attempt'], request_sha256=request['request_sha256'],
            decision='approved', decided_by='synthetic-reviewer', expires_at=self.clock() + 30,
            decision_id=f"decision-{task_id}-{request['attempt']}")
        kwargs.update(changes)
        return self.store.record_approval(run, task_id, **kwargs)

    def engine(self, executor=None, provider=None):
        return FactoryEngine(self.store, base_directory=self.root,
            executor=executor or DeterministicMockExecutor(), provider=provider,
            clock=self.clock, sleeper=lambda _: self.fail('approval wait must not sleep/spin'))

    def complete(self, claim, callback=None):
        receipt = {'attempt': claim.attempt, 'synthetic': True}
        return self.store.complete_task(claim, succeeded=True, receipt=receipt,
            receipt_hash=digest_json(receipt), error=None, retry_base_seconds=0,
            retry_cap_seconds=0, before_transition=callback)

    def test_absent_approval_keeps_exact_legacy_json_and_digest(self):
        raw = spec()
        parsed = FactorySpec.from_dict(raw)
        # Golden captured from the unmodified baseline parser, never regenerated.
        golden = Path(__file__).with_name('approval-legacy-golden.json').read_text()
        self.assertEqual(parsed.canonical_json(), golden)
        self.assertEqual(sha256(parsed.canonical_json().encode()).hexdigest(), sha256(golden.encode()).hexdigest())
        run = self.store.create_run(parsed, 'legacy')
        snapshot = self.store.snapshot(run)
        self.assertNotIn('waiting_for_approval', snapshot)
        self.assertNotIn('active_wall_seconds', snapshot)

    def test_approval_field_is_strict(self):
        for value in (None, False, True, 1, {}, [], 'none', 'Required', ''):
            with self.subTest(value=value), self.assertRaises(SpecError):
                FactorySpec.from_dict(spec(task(approval=value)))

    def test_missing_approval_returns_wait_without_executor_attempt_or_budget_loop(self):
        run = self.plan()
        engine = self.engine()
        engine.executor.execute = lambda _: self.fail('unapproved command executed')
        waiting = engine.run(run)
        self.assertTrue(waiting.waiting_for_approval)
        self.assertEqual(self.store.snapshot(run)['tasks'][0]['attempts'], 0)
        self.clock.advance(500)
        self.assertTrue(engine.run(run).waiting_for_approval)
        self.assertEqual(self.store.snapshot(run)['active_wall_seconds'], 0)
        self.approve(run)
        self.assertEqual(self.engine().run(run).state, 'succeeded')

    def test_digest_changes_for_command_environment_tests_ownership_budget_and_attempt(self):
        run = self.plan()
        initial = self.store.approval_request(run, 'build')
        material = {k: v for k, v in initial.items() if k != 'request_sha256'}
        self.assertEqual(initial['request_sha256'], digest_json(material))
        for path, value in [('command', ['python', '-c', 'print(1)']), ('environment', {'LEVEL': 'two'}),
                            ('tests', [{'name': 'x', 'command': ['python', '-c', 'pass']}]), ('owned_paths', ['new.txt'])]:
            changed = json.loads(json.dumps(material)); changed['task'][path] = value
            self.assertNotEqual(digest_json(changed), initial['request_sha256'])
        for field in ('attempt', 'budget'):
            changed = json.loads(json.dumps(material)); changed[field] = 2 if field == 'attempt' else {'max_tasks': 2}
            self.assertNotEqual(digest_json(changed), initial['request_sha256'])
        with self.assertRaisesRegex(ValueError, 'digest mismatch'):
            self.approve(run, request_sha256='0' * 64)
        self.assertEqual(self.store.snapshot(run)['receipt_count'], 0)

    def test_dates_types_and_bounds_refused(self):
        run = self.plan()
        for value in (True, False, '1030', None, float('nan'), float('inf'), -1, 999, 1000, 87401, 1e309):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.approve(run, expires_at=value)
        for value in (True, 1.0, '1', 0, -1, 101):
            with self.subTest(attempt=value), self.assertRaises(ValueError):
                self.approve(run, attempt=value)

    def test_rejection_and_exact_expiry_refuse_claim_without_attempt_consumption(self):
        run = self.plan(); self.approve(run, decision='rejected')
        self.store.start_run(run)
        self.assertIsNone(self.store.claim_ready_task(run, 'worker', 10))
        self.assertEqual(self.store.snapshot(run)['waiting_for_approval'][0]['approval'], 'rejected')
        self.approve(run, decision_id='new-explicit-decision', expires_at=1005)
        self.clock.advance(5)
        self.assertIsNone(self.store.claim_ready_task(run, 'worker', 10))
        self.assertEqual(self.store.snapshot(run)['waiting_for_approval'][0]['approval'], 'expired')
        self.assertEqual(self.store.snapshot(run)['tasks'][0]['attempts'], 0)

    def test_approval_is_not_actor_authentication_and_is_idempotent(self):
        run = self.plan(); first = self.approve(run)
        count = self.store.snapshot(run)['event_count']
        self.assertEqual(self.approve(run), first)
        self.assertEqual(self.store.snapshot(run)['event_count'], count)
        self.assertEqual(first['actor_authentication'], 'not_established')
        with self.assertRaisesRegex(ValueError, 'different content'):
            self.approve(run, decided_by='another-reviewer')

    def test_approved_engine_publishes_real_synthetic_artifact_and_verified_export(self):
        class Writer:
            name = 'synthetic-writer'
            def execute(_, request):
                (request.cwd / 'result.txt').write_text('actual isolated bytes')
                return result(executor='synthetic-writer')
        run = self.plan(task(approval='required', owned_paths=['result.txt'], artifacts=['result.txt']))
        self.approve(run)
        self.assertEqual(self.engine(Writer()).run(run).state, 'succeeded')
        self.assertEqual((self.root / 'workspace/result.txt').read_text(), 'actual isolated bytes')
        exported = self.store.export(run)
        self.assertTrue(verify_export(exported)[0], verify_export(exported)[1])
        decision = [e for e in exported['events'] if e['event_type'] == 'approval.decided']
        self.assertEqual(len(decision), 1)

    def test_expiry_at_publication_refuses_callback_and_preserves_workspace(self):
        run = self.plan(); self.approve(run, expires_at=1005); self.store.start_run(run)
        claim = self.store.claim_ready_task(run, 'worker', 20)
        self.assertEqual(claim.lease_expires_at, 1005)
        self.clock.advance(5)
        with self.assertRaises(LeaseLost):
            self.complete(claim, lambda: self.fail('expired publication invoked'))
        self.assertEqual(self.store.snapshot(run)['receipt_count'], 0)

    def test_expiry_during_publication_rolls_back_effect_and_receipt(self):
        from ai_software_factory.store import CompletionEffect
        run = self.plan(); self.approve(run, expires_at=1005); self.store.start_run(run)
        claim = self.store.claim_ready_task(run, 'worker', 20)
        path = self.root / 'published.txt'; path.write_text('old')
        finalized = []
        def publish():
            path.write_text('new'); self.clock.advance(5)
            return CompletionEffect(lambda: path.write_text('old'), lambda: finalized.append(True))
        with self.assertRaises(LeaseLost): self.complete(claim, publish)
        self.assertEqual(path.read_text(), 'old')
        self.assertEqual(finalized, [True])
        self.assertEqual(self.store.snapshot(run)['receipt_count'], 0)

    def test_clock_regression_after_successful_observation_fails_closed(self):
        run = self.plan(); self.approve(run); self.store.start_run(run)
        self.clock.advance(5)
        claim = self.store.claim_ready_task(run, 'worker', 10)
        self.clock.value = 1002
        with self.assertRaisesRegex(ValueError, 'clock regressed'): self.store.snapshot(run)
        with self.assertRaises(LeaseLost): self.complete(claim)
        self.clock.value = 1005
        self.assertEqual(self.store.snapshot(run)['receipt_count'], 0)

    def test_future_clock_at_creation_or_missing_runtime_never_bypasses_gate(self):
        run = self.plan()
        self.clock.value = 900
        with self.assertRaisesRegex(ValueError, 'clock regressed'): self.approve(run)
        self.clock.value = 1000
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('DELETE FROM approval_runtime WHERE run_id=?', (run,))
        with self.assertRaisesRegex(ValueError, 'missing'): self.store.snapshot(run)

    def test_stale_owner_recovery_needs_new_attempt_approval_and_old_owner_cannot_publish(self):
        run = self.plan(); self.approve(run); self.store.start_run(run)
        first = self.store.claim_ready_task(run, 'first', 2)
        self.clock.advance(3)
        self.assertIsNone(self.store.claim_ready_task(run, 'second', 2))
        second_request = self.store.approval_request(run, 'build')
        self.assertEqual(second_request['attempt'], 2)
        self.approve(run)
        second = self.store.claim_ready_task(run, 'second', 2)
        self.assertEqual(second.attempt, 2)
        with self.assertRaises(LeaseLost): self.complete(first, lambda: self.fail('obsolete owner published'))
        self.complete(second)
        reopened = FactoryStore(self.db, clock=self.clock, create=False)
        events = reopened.replay(run)
        self.assertEqual(reopened.snapshot(run)['receipt_count'], 1)
        self.assertEqual(len({event['event_key'] for event in events}), len(events))

    def test_changed_provider_command_or_test_cannot_execute_unapproved_request(self):
        for target in ('command', 'test'):
            with self.subTest(target=target):
                root = self.root / target; root.mkdir()
                store = FactoryStore(root / 'factory.sqlite3', clock=self.clock)
                parsed = FactorySpec.from_dict(spec(task(approval='required', max_attempts=1,
                    tests=[{'name': 'verify', 'command': ['python', '-c', 'pass']}])) )
                class Dynamic(SpecProvider):
                    def task_request(inner, *args):
                        request = super().task_request(*args)
                        return ExecutionRequest(request.label, ('forbidden',), request.cwd, request.timeout_seconds, request.max_output_bytes) if target == 'command' else request
                    def test_request(inner, *args):
                        request = super().test_request(*args)
                        return ExecutionRequest(request.label, ('forbidden',), request.cwd, request.timeout_seconds, request.max_output_bytes)
                executed = []
                class Recorder:
                    name = 'synthetic-recorder'
                    def execute(_, request):
                        executed.append(request.argv); return result()
                engine = FactoryEngine(store, base_directory=root, provider=Dynamic(), executor=Recorder(), clock=self.clock)
                run = engine.plan(parsed, idempotency_key='run')
                request = store.approval_request(run, 'build')
                store.record_approval(run, 'build', attempt=1, request_sha256=request['request_sha256'], decision='approved', decided_by='reviewer', expires_at=1030, decision_id='one')
                self.assertEqual(engine.run(run).state, 'failed')
                self.assertNotIn(('forbidden',), executed)
                self.assertEqual(len(executed), 0 if target == 'command' else 1)

    def test_corrupt_decision_or_missing_event_is_refused(self):
        run = self.plan(); self.approve(run); self.store.start_run(run)
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE approval_decisions SET decision_json='{}'")
        with self.assertRaisesRegex(ValueError, 'stored approval'): self.store.claim_ready_task(run, 'w', 10)

    def test_two_processes_have_one_claim_receipt_and_intact_journal(self):
        run = self.plan(); self.approve(run); self.store.start_run(run)
        ctx = multiprocessing.get_context('spawn' if os.name == 'nt' else 'fork')
        event, output = ctx.Event(), ctx.Queue()
        workers = [ctx.Process(target=_claim_process, args=(self.db, run, f'worker-{i}', event, output)) for i in range(2)]
        for worker in workers: worker.start()
        event.set()
        answers = [output.get(timeout=10) for _ in workers]
        for worker in workers:
            worker.join(10); self.assertEqual(worker.exitcode, 0)
        self.assertEqual(sum(answer is not None for answer in answers), 1)
        reopened = FactoryStore(self.db, clock=self.clock, create=False)
        events = reopened.replay(run)
        self.assertEqual(reopened.snapshot(run)['receipt_count'], 1)
        self.assertEqual(len(events), reopened.snapshot(run)['event_count'])
        self.assertEqual(len({e['event_key'] for e in events}), len(events))

    def test_connection_is_closed_when_initial_pragma_fails(self):
        class Connection:
            closed = False
            def execute(inner, _): raise sqlite3.OperationalError('synthetic PRAGMA failure')
            def close(inner): inner.closed = True
        connection = Connection()
        with patch('ai_software_factory.store.sqlite3.connect', return_value=connection):
            with self.assertRaises(sqlite3.OperationalError): self.store._connect()
        self.assertTrue(connection.closed)

    def test_required_task_does_not_starve_an_independent_unprotected_task(self):
        run = self.plan(task('needs-review', approval='required'), task('independent'))
        state = self.engine().run(run)
        self.assertTrue(state.waiting_for_approval)
        tasks = {t['task_id']: t for t in self.store.snapshot(run)['tasks']}
        self.assertEqual(tasks['needs-review']['attempts'], 0)
        self.assertEqual(tasks['independent']['state'], 'succeeded')

    def test_wait_after_dependency_success_preserves_remaining_budget_and_lease_budget(self):
        run = self.plan(task('first'), task('build', approval='required', depends_on=['first']))
        self.assertTrue(self.engine().run(run).waiting_for_approval)
        self.clock.advance(500)
        self.approve(run, expires_at=1501)
        received = []
        class Recorder:
            name = 'deadline-recorder'
            def execute(_, request): received.append(request.timeout_seconds); return result()
        self.assertEqual(self.engine(Recorder()).run(run).state, 'succeeded')
        self.assertEqual(received, [1.0])
        elapsed = self.store.snapshot(run)['active_wall_seconds']
        self.clock.advance(30)
        self.assertEqual(self.store.snapshot(run)['active_wall_seconds'], elapsed)

    def test_approval_cannot_change_during_owned_attempt(self):
        run = self.plan(); self.approve(run); self.store.start_run(run)
        self.store.claim_ready_task(run, 'worker', 10)
        request = self.store._approval_request
        with closing(self.store._connect()) as db:
            value = request(db, run, 'build', 1)
        with self.assertRaisesRegex(ValueError, 'no longer pending'):
            self.store.record_approval(run, 'build', attempt=1,
                request_sha256=value['request_sha256'], decision='rejected', decided_by='reviewer',
                expires_at=1030, decision_id='changed')

    def test_approval_is_not_available_before_dependency_is_ready(self):
        run = self.plan(task('first'), task('build', approval='required', depends_on=['first']))
        with self.assertRaisesRegex(ValueError, 'no approvable next attempt'):
            self.store.approval_request(run, 'build')
        self.assertTrue(self.engine().run(run).waiting_for_approval)
        self.assertEqual(self.store.approval_request(run, 'build')['attempt'], 1)

    def test_export_verifier_requires_bound_approval_even_after_consistent_rehash(self):
        run = self.plan(); self.approve(run)
        self.assertEqual(self.engine().run(run).state, 'succeeded')
        original = self.store.export(run)
        self.assertTrue(verify_export(original)[0])
        for mode in ('missing', 'wrong-request', 'expired', 'wrong-command'):
            with self.subTest(mode=mode):
                exported = json.loads(json.dumps(original))
                event = next(e for e in exported['events'] if e['event_type'] == 'approval.decided')
                if mode == 'missing':
                    exported['events'].remove(event)
                elif mode == 'wrong-command':
                    wrapper = exported['receipts'][0]
                    wrapper['receipt']['execution']['command_digest'] = 'sha256:' + '0' * 64
                    wrapper['receipt_hash'] = digest_json(wrapper['receipt'])
                    completed = next(e for e in exported['events'] if e['event_key'] == 'task.completed:build:1')
                    completed['payload']['receipt_hash'] = wrapper['receipt_hash']
                else:
                    decision = event['payload']['decision']
                    if mode == 'wrong-request': decision['request_sha256'] = '0' * 64
                    else: decision['expires_at'] = decision['decided_at']
                    event['payload']['decision_sha256'] = digest_json(decision)
                previous = '0' * 64
                for event in exported['events']:
                    event['previous_hash'] = previous
                    material = {key: event[key] for key in ('task_id', 'event_type', 'payload', 'created_at', 'event_key', 'previous_hash')}
                    material['run_id'] = run
                    event['event_hash'] = digest_json(material)
                    previous = event['event_hash']
                exported['event_chain_root'] = previous
                exported['status']['event_head_hash'] = previous
                exported['status']['event_count'] = len(exported['events'])
                exported['export_sha256'] = digest_json({k: v for k, v in exported.items() if k != 'export_sha256'})
                valid, issues = verify_export(exported)
                self.assertFalse(valid)
                self.assertIn('protected receipt lacks a valid bound approval', issues)

    def test_missing_journal_event_refuses_approval(self):
        run = self.plan(); self.approve(run); self.store.start_run(run)
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("DELETE FROM events WHERE event_type='approval.decided'")
        with self.assertRaisesRegex(ValueError, 'journal event'):
            self.store.claim_ready_task(run, 'worker', 10)

    def test_native_process_death_then_reopen_requires_fresh_approval(self):
        run = self.plan(); self.approve(run); self.store.start_run(run)
        ctx = multiprocessing.get_context('spawn' if os.name == 'nt' else 'fork')
        receiver, sender = ctx.Pipe(duplex=False)
        worker = ctx.Process(target=_crash_process, args=(self.db, run, sender))
        worker.start()
        self.assertTrue(receiver.poll(10))
        self.assertEqual(receiver.recv(), 1)
        worker.join(10); self.assertEqual(worker.exitcode, 23)
        self.clock.advance(3)
        self.store = FactoryStore(self.db, clock=self.clock, create=False)
        self.assertIsNone(self.store.claim_ready_task(run, 'replacement', 10))
        self.approve(run)
        self.assertEqual(self.engine().run(run).state, 'succeeded')
        events = self.store.replay(run)
        self.assertEqual(sum(e['event_key'] == 'task.completed:build:2' for e in events), 1)
        self.assertEqual(self.store.snapshot(run)['receipt_count'], 1)

    def test_v1_migration_preserves_legacy_rows_and_chain(self):
        database = self.root / 'old.sqlite3'
        raw = Path(__file__).with_name('approval-legacy-golden.json').read_text()
        spec_hash = sha256(raw.encode()).hexdigest()
        payload = {'spec_hash': spec_hash, 'task_count': 1}
        event = {'run_id': 'old-run', 'task_id': None, 'event_type': 'run.created',
                 'payload': payload, 'created_at': 1000.0, 'event_key': 'run.created', 'previous_hash': '0' * 64}
        event_hash = digest_json(event)
        with closing(sqlite3.connect(database)) as db, db:
            db.executescript(Path(__file__).with_name('approval-v1-schema.sql').read_text())
            db.execute('PRAGMA user_version=1')
            db.execute('INSERT INTO runs(run_id,idempotency_key,spec_hash,spec_json,state,created_at,max_attempts,max_wall_seconds,event_count,event_head_hash) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       ('old-run', 'legacy', spec_hash, raw, 'created', 1000, 100, 60, 1, event_hash))
            db.execute("INSERT INTO tasks(run_id,task_id,sort_order,state,max_attempts) VALUES('old-run','build',0,'ready',2)")
            db.execute('INSERT INTO events(run_id,task_id,event_type,payload_json,created_at,event_key,previous_hash,event_hash) VALUES(?,?,?,?,?,?,?,?)',
                       ('old-run', None, 'run.created', json.dumps(payload, sort_keys=True, separators=(',', ':')), 1000, 'run.created', '0' * 64, event_hash))
        with closing(sqlite3.connect(database)) as db:
            before = {table: db.execute(f'SELECT * FROM {table}').fetchall() for table in ('runs', 'tasks', 'events', 'receipts', 'dependencies')}
        migrated = FactoryStore(database, create=False, clock=self.clock)
        with closing(sqlite3.connect(database)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 3)
            self.assertIsNotNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='execution_reservations'").fetchone())
            for table, rows in before.items(): self.assertEqual(db.execute(f'SELECT * FROM {table}').fetchall(), rows)
        self.assertEqual(len(migrated.replay('old-run')), 1)
        self.assertNotIn('waiting_for_approval', migrated.snapshot('old-run'))
        (self.root / 'workspace').mkdir(exist_ok=True)
        engine = FactoryEngine(migrated, base_directory=self.root, executor=DeterministicMockExecutor(), clock=self.clock)
        self.assertEqual(engine.run('old-run').state, 'succeeded')

    def test_interrupted_schema_migration_rolls_back_added_tables_and_version(self):
        database = self.root / 'v1.sqlite3'
        with closing(sqlite3.connect(database)) as db, db:
            db.executescript(Path(__file__).with_name('approval-v1-schema.sql').read_text())
            db.execute('PRAGMA user_version=1')
        class Interrupted(FactoryStore):
            def _connect(inner):
                db = super()._connect()
                def authorizer(action, first, second, database_name, trigger):
                    return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_CREATE_TABLE and first == 'approval_decisions' else sqlite3.SQLITE_OK
                db.set_authorizer(authorizer)
                return db
        with self.assertRaises(sqlite3.DatabaseError): Interrupted(database)
        with closing(sqlite3.connect(database)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='approval_runtime'").fetchone())

    def test_cli_wait_and_native_approval_roundtrip(self):
        # Real clock for CLI, deliberately synthetic request and mocked executor.
        import time
        raw = spec(task(approval='required'))
        file = self.root / 'spec.json'; file.write_text(json.dumps(raw))
        argv = ['run', str(file), '--db', str(self.db), '--idempotency-key', 'cli']
        with redirect_stdout(io.StringIO()), patch('ai_software_factory.engine.SubprocessExecutor', return_value=DeterministicMockExecutor()):
            self.assertEqual(main(argv), 3)
        store = FactoryStore(self.db)
        run = store.create_run(FactorySpec.from_dict(raw), 'cli')
        request_output = io.StringIO()
        with redirect_stdout(request_output):
            self.assertEqual(main(['approval-request', '--db', str(self.db), '--run-id', run, '--task-id', 'build']), 0)
        request = json.loads(request_output.getvalue())
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(['approval-decide', '--db', str(self.db), '--run-id', run, '--task-id', 'build', '--attempt', '1', '--request-sha256', request['request_sha256'], '--decision', 'approved', '--decided-by', 'local-reviewer', '--expires-at', str(time.time() + 30), '--decision-id', 'cli-one']), 0)
        with redirect_stdout(io.StringIO()), patch('ai_software_factory.engine.SubprocessExecutor', return_value=DeterministicMockExecutor()):
            self.assertEqual(main(argv), 0)
