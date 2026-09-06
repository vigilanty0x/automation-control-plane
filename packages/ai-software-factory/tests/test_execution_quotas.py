"""CONS-01 public acceptance: synthetic data, actual Store/Engine/CLI seams."""
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
from ai_software_factory.executors import DeterministicMockExecutor, SpecProvider, SubprocessExecutor
from ai_software_factory.models import FactorySpec, SpecError
from ai_software_factory.store import FactoryStore, LeaseLost
from tests.support import ManualClock, result, spec, task


def quota(calls=10, output=100_000, milliseconds=100_000, owners=None):
    return {'limits': {'executor_calls': calls, 'retained_output_bytes': output, 'execution_ms': milliseconds},
            'owners': owners or {}}


def _claim_worker(database, run_id, worker, barrier, pipe):
    store = FactoryStore(database, clock=lambda: 1000.0)
    pipe.send('ready')
    barrier.wait(10)
    claim = store.claim_ready_task(run_id, worker, 10)
    pipe.send(None if claim is None else claim.task_id)
    pipe.close()


def _crash_worker(database, run_id, pipe):
    store = FactoryStore(database, clock=lambda: 1000.0)
    claim = store.claim_ready_task(run_id, 'lost-worker', 2)
    parsed = store.load_spec(run_id)
    request = SpecProvider().task_request(parsed, parsed.task(claim.task_id), Path(database).parent)
    store.begin_dispatch(claim, request, 0)
    pipe.send(claim.attempt)
    pipe.close()
    os._exit(23)


class ExecutionQuotaTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.clock = ManualClock()
        self.db = self.root / 'factory.sqlite3'
        self.store = FactoryStore(self.db, clock=self.clock)

    def plan(self, *tasks, limits=None, **budget):
        raw = spec(*(tasks or (task(),)))
        raw['budget'].update(budget)
        raw['budget']['execution_quota'] = limits if limits is not None else quota()
        parsed = FactorySpec.from_dict(raw)
        (self.root / 'workspace').mkdir(exist_ok=True)
        return self.store.create_run(parsed, 'quota-run')

    def engine(self, executor=None, provider=None):
        return FactoryEngine(self.store, base_directory=self.root,
            executor=executor or DeterministicMockExecutor(), provider=provider,
            clock=self.clock, sleeper=lambda _: self.fail('quota/approval waits must not spin'))

    def claim(self, run, worker='worker'):
        self.store.start_run(run)
        claim = self.store.claim_ready_task(run, worker, 10)
        self.assertIsNotNone(claim)
        parsed = self.store.load_spec(run)
        request = SpecProvider().task_request(parsed, parsed.task(claim.task_id), self.root / 'workspace')
        return claim, request

    def test_absent_option_preserves_full_golden_export_bytes(self):
        engine = self.engine()
        run = engine.plan(FactorySpec.from_dict(spec()), idempotency_key='quota-legacy-golden')
        engine.run(run)
        actual = json.dumps(self.store.export(run), sort_keys=True, separators=(',', ':')) + '\n'
        golden = Path(__file__).with_name('quota-legacy-export.json').read_text(encoding='utf-8')
        self.assertEqual(actual, golden)

    def test_policy_closed_types_bounds_unsupported_units_and_unknown_owner(self):
        bad = [None, {}, True, {'limits': {}}, {'limits': {'tokens': 100}},
               {**quota(), 'cost': 0}, quota(owners={'absent': quota()['limits']})]
        for value in (True, False, -1, 1.0, '1', float('nan'), float('inf'), 1_000_000_000_001):
            for key in ('executor_calls', 'retained_output_bytes', 'execution_ms'):
                item = quota(); item['limits'][key] = value; bad.append(item)
        for item in bad:
            with self.subTest(policy=item), self.assertRaises(SpecError):
                raw = spec(); raw['budget']['execution_quota'] = item
                FactorySpec.from_dict(raw)

    def test_two_processes_cannot_overreserve_one_call(self):
        run = self.plan(task('a'), task('b'), limits=quota(calls=1))
        self.store.start_run(run)
        context = multiprocessing.get_context('spawn' if os.name == 'nt' else 'fork')
        barrier = context.Event()
        children, pipes = [], []
        for worker in ('one', 'two'):
            parent_pipe, child_pipe = context.Pipe(duplex=False)
            child = context.Process(target=_claim_worker, args=(self.db, run, worker, barrier, child_pipe))
            child.start(); children.append(child); pipes.append(parent_pipe); child_pipe.close()
        try:
            values = []
            for pipe in pipes:
                self.assertTrue(pipe.poll(15)); self.assertEqual(pipe.recv(), 'ready')
            barrier.set()
            for pipe in pipes:
                self.assertTrue(pipe.poll(15)); values.append(pipe.recv())
            for child in children:
                child.join(15); self.assertEqual(child.exitcode, 0)
            self.assertEqual(sum(value is not None for value in values), 1)
            state = self.store.snapshot(run)
            self.assertEqual(sum(item['attempts'] for item in state['tasks']), 1)
            self.assertEqual(state['execution_quota']['occupied']['executor_calls'], 1)
        finally:
            for child in children:
                if child.is_alive(): child.terminate(); child.join(5)
            for pipe in pipes: pipe.close()

    def test_owner_is_declared_quota_group_and_independent_group_can_progress(self):
        limits = quota(calls=2, owners={'a': quota(calls=1)['limits']})
        run = self.plan(task('a1', owner='a'), task('a2', owner='a'), task('b1', owner='b'), limits=limits)
        first, _ = self.claim(run)
        second = self.store.claim_ready_task(run, 'other', 10)
        self.assertEqual((first.task_id, second.task_id), ('a1', 'b1'))
        self.assertEqual(self.store.snapshot(run)['execution_quota']['owner_authentication'], 'not_established')

    def test_real_subprocess_retained_bytes_are_raw_combined_and_truncated(self):
        command = [sys.executable, '-c', "import sys;sys.stdout.buffer.write(bytes([195,169])*10);sys.stderr.buffer.write(b'z'*10)"]
        run = self.plan(task(command=command), max_output_bytes=7)
        outcome = self.engine(SubprocessExecutor(self.root)).run(run)
        self.assertEqual(outcome.state, 'succeeded')
        receipt = self.store.export(run)['receipts'][0]['receipt']
        usage = receipt['execution_quota']
        self.assertEqual(usage['consumption']['retained_output_bytes'], 7)
        self.assertEqual(usage['consumption']['executor_calls'], 1)
        self.assertEqual(usage['dispatches'][0]['measurement_origin'], 'engine_monotonic_output')
        self.assertEqual(usage['producer_authentication'], 'not_established')
        self.assertTrue(receipt['execution']['output_truncated'])
        self.assertEqual(receipt['execution']['stdout']['bytes_seen'] + receipt['execution']['stderr']['bytes_seen'], 30)
        self.assertIsNone(usage['tokens']); self.assertIsNone(usage['cost'])
        self.assertEqual(verify_export(self.store.export(run)), (True, ()))

    def test_failed_attempt_consumption_remains_charged_on_retry(self):
        run = self.plan(limits=quota(calls=2))
        executor = DeterministicMockExecutor({'build': [result(1, stdout=b'bad'), result(0, stdout=b'ok')]})
        self.assertEqual(self.engine(executor).run(run).state, 'succeeded')
        state = self.store.snapshot(run)
        self.assertEqual(state['tasks'][0]['attempts'], 2)
        self.assertEqual(state['execution_quota']['consumption']['executor_calls'], 2)
        self.assertEqual(state['execution_quota']['consumption']['retained_output_bytes'], 5)

    def test_unused_test_reservation_released_but_failed_call_is_not_refunded(self):
        tests = [{'name': 'one', 'command': ['python', '-c', 'pass']}, {'name': 'two', 'command': ['python', '-c', 'pass']}]
        run = self.plan(task(tests=tests, max_attempts=1), limits=quota(calls=3))
        self.assertEqual(self.engine(DeterministicMockExecutor(default_exit_code=1)).run(run).state, 'failed')
        usage = self.store.snapshot(run)['execution_quota']
        self.assertEqual(usage['occupied']['executor_calls'], 1)
        self.assertEqual(usage['consumption']['executor_calls'], 1)

    def test_exhausted_retry_waits_without_attempt_or_wall_budget_spin(self):
        run = self.plan(limits=quota(calls=1))
        engine = self.engine(DeterministicMockExecutor(default_exit_code=1))
        self.assertTrue(engine.run(run).waiting_for_quota)
        self.clock.advance(500)
        self.assertTrue(engine.run(run).waiting_for_quota)
        state = self.store.snapshot(run)
        self.assertEqual(state['tasks'][0]['attempts'], 1)
        self.assertEqual(state['active_wall_seconds'], 0)
        self.assertEqual(state['execution_quota']['waiting'][0]['reason'], 'run_limit')

    def test_exception_after_dispatch_is_unknown_not_zero_and_never_retried(self):
        class Broken(DeterministicMockExecutor):
            calls = 0
            def execute(self, request):
                self.calls += 1
                raise RuntimeError('synthetic lost result')
        run = self.plan(); executor = Broken()
        self.assertEqual(self.engine(executor).run(run).state, 'failed')
        self.assertEqual(executor.calls, 1)
        state = self.store.snapshot(run)['execution_quota']
        self.assertIsNone(state['consumption'])
        self.assertFalse(state['measurement_complete'])
        self.assertEqual(state['occupied']['executor_calls'], 1)
        self.assertEqual(state['reservation_states'][0]['state'], 'unknown')

    def test_missing_measurement_capability_refuses_before_dispatch(self):
        class Unmeasured:
            name = 'unmeasured'
            def execute(self, request): raise AssertionError('must not dispatch')
        run = self.plan()
        self.assertEqual(self.engine(Unmeasured()).run(run).state, 'failed')
        self.assertEqual(self.store.snapshot(run)['tasks'][0]['attempts'], 1)
        self.assertEqual(self.store.snapshot(run)['execution_quota']['consumption']['executor_calls'], 0)
        self.assertFalse(any(event['event_type'] == 'quota.dispatch' for event in self.store.replay(run)))

    def test_dynamic_provider_is_refused_before_dispatch(self):
        from dataclasses import replace
        class Dynamic(SpecProvider):
            def task_request(self, *args):
                return replace(super().task_request(*args), argv=('python', '-c', 'print(999)'))
        run = self.plan(task(max_attempts=1))
        self.assertEqual(self.engine(provider=Dynamic()).run(run).state, 'failed')
        self.assertEqual(self.store.snapshot(run)['execution_quota']['consumption']['executor_calls'], 0)

    def test_dispatch_is_not_reissued_and_measurement_replay_is_idempotent(self):
        run = self.plan(); claim, request = self.claim(run)
        self.store.begin_dispatch(claim, request, 0)
        with self.assertRaises(ValueError): self.store.begin_dispatch(claim, request, 0)
        measurement = {'executor_calls': 1, 'retained_output_bytes': 2, 'execution_ms': 1}
        self.assertTrue(self.store.settle_dispatch(claim, 0, measurement))
        self.assertEqual(self.store.quota_usage(claim)['dispatches'][0]['measurement_origin'], 'caller_declared')
        before = self.store.replay(run)
        self.assertTrue(self.store.settle_dispatch(claim, 0, measurement))
        self.assertEqual(before, self.store.replay(run))
        with self.assertRaises(ValueError): self.store.settle_dispatch(claim, 0, {**measurement, 'execution_ms': 2})
        with self.assertRaises(ValueError): self.store.begin_dispatch(claim, request, 0)

    def test_expired_worker_cannot_measure_or_publish_and_uncertain_attempt_is_not_reclaimed(self):
        run = self.plan(); claim, request = self.claim(run)
        self.store.begin_dispatch(claim, request, 0)
        self.clock.advance(11)
        self.assertIsNone(self.store.claim_ready_task(run, 'replacement', 10))
        with self.assertRaises(LeaseLost): self.store.settle_dispatch(claim, 0, {'executor_calls': 1, 'retained_output_bytes': 0, 'execution_ms': 0})
        state = self.store.snapshot(run)
        self.assertEqual(state['tasks'][0]['state'], 'failed')
        self.assertEqual(state['tasks'][0]['attempts'], 1)
        self.assertIsNone(state['execution_quota']['consumption'])

    def test_never_dispatched_expired_reservation_can_be_released_and_reclaimed(self):
        run = self.plan(limits=quota(calls=1)); first, _ = self.claim(run)
        self.clock.advance(11)
        second = self.store.claim_ready_task(run, 'new-worker', 10)
        self.assertEqual(second.attempt, 2)
        state = self.store.snapshot(run)['execution_quota']
        self.assertEqual(state['occupied']['executor_calls'], 1)
        self.assertEqual(state['known_consumption']['executor_calls'], 0)
        self.assertEqual(state['reservation_states'][0]['reason'], 'never_dispatched')

    def test_process_crash_after_marker_survives_database_reopen(self):
        run = self.plan(); self.store.start_run(run)
        context = multiprocessing.get_context('spawn' if os.name == 'nt' else 'fork')
        parent_pipe, child_pipe = context.Pipe(duplex=False)
        child = context.Process(target=_crash_worker, args=(self.db, run, child_pipe))
        child.start(); child_pipe.close()
        try:
            self.assertTrue(parent_pipe.poll(15)); self.assertEqual(parent_pipe.recv(), 1)
            child.join(15); self.assertEqual(child.exitcode, 23)
            self.clock.advance(3)
            reopened = FactoryStore(self.db, clock=self.clock)
            self.assertIsNone(reopened.claim_ready_task(run, 'replacement', 10))
            usage = reopened.snapshot(run)['execution_quota']
            self.assertIsNone(usage['consumption'])
            self.assertEqual(usage['occupied']['executor_calls'], 1)
        finally:
            parent_pipe.close()
            if child.is_alive(): child.terminate(); child.join(5)

    def test_invalid_output_return_does_not_become_zero_consumption(self):
        from dataclasses import replace
        run = self.plan()
        executor = DeterministicMockExecutor({'build': [replace(result(), stdout='not bytes')]})
        self.assertEqual(self.engine(executor).run(run).state, 'failed')
        self.assertIsNone(self.store.snapshot(run)['execution_quota']['consumption'])

    def test_observed_output_overrun_is_kept_and_publication_refused(self):
        class TooMuch(DeterministicMockExecutor):
            def execute(self, request):
                (request.cwd / 'answer.txt').write_text('synthetic output')
                return result(stdout=b'x' * 10)
        run = self.plan(task(artifacts=['answer.txt']), max_output_bytes=4)
        self.assertEqual(self.engine(TooMuch()).run(run).state, 'failed')
        self.assertFalse((self.root / 'workspace/answer.txt').exists())
        usage = self.store.snapshot(run)['execution_quota']
        self.assertEqual(usage['consumption']['retained_output_bytes'], 10)
        self.assertEqual(usage['reservation_states'][0]['state'], 'overrun')

    def test_monotonic_time_overrun_is_preserved_not_clamped(self):
        run = self.plan()
        with patch('ai_software_factory.engine.time.monotonic_ns', side_effect=[0, 2_000_000_001]):
            self.assertEqual(self.engine().run(run).state, 'failed')
        usage = self.store.snapshot(run)['execution_quota']
        self.assertEqual(usage['consumption']['execution_ms'], 2001)
        self.assertEqual(usage['reservation_states'][0]['state'], 'overrun')

    def test_clock_regression_refused_and_no_new_dispatch(self):
        run = self.plan(); self.claim(run)
        before = self.store.replay(run)
        self.clock.advance(-1)
        with self.assertRaises(ValueError): self.store.snapshot(run)
        self.assertEqual(before, self.store.replay(run))

    def test_tampered_or_removed_ledger_row_is_not_valid_measurement(self):
        run = self.plan(); self.claim(run)
        with closing(sqlite3.connect(self.db)) as db:
            original = db.execute('SELECT record_json,record_sha256 FROM execution_reservations').fetchone()
            altered = json.loads(original[0]); altered['contract']['reserved']['executor_calls'] = 0
            db.execute('UPDATE execution_reservations SET record_json=?,record_sha256=?', (json.dumps(altered), digest_json(altered))); db.commit()
        with self.assertRaises(ValueError): self.store.snapshot(run)
        with closing(sqlite3.connect(self.db)) as db:
            db.execute('DELETE FROM execution_reservations'); db.commit()
        with self.assertRaises(ValueError): self.store.snapshot(run)

    def test_completion_requires_ledger_bound_receipt_not_caller_zero(self):
        run = self.plan(); claim, request = self.claim(run)
        self.store.begin_dispatch(claim, request, 0)
        self.store.settle_dispatch(claim, 0, {'executor_calls': 1, 'retained_output_bytes': 2, 'execution_ms': 1})
        receipt = {'execution_quota': {'consumption': {'executor_calls': 0}}}
        with self.assertRaises(ValueError):
            self.store.complete_task(claim, succeeded=True, receipt=receipt, receipt_hash=digest_json(receipt),
                error=None, retry_base_seconds=0, retry_cap_seconds=0)
        self.assertEqual(self.store.snapshot(run)['receipt_count'], 0)

    def test_export_rehashed_false_consumption_and_omitted_quota_are_refused(self):
        run = self.plan(); self.engine().run(run)
        exported = self.store.export(run)
        self.assertEqual(verify_export(exported), (True, ()))
        changed = json.loads(json.dumps(exported))
        changed['status']['execution_quota']['consumption']['executor_calls'] = 0
        changed['export_sha256'] = digest_json({k: v for k, v in changed.items() if k != 'export_sha256'})
        self.assertFalse(verify_export(changed)[0])
        changed = json.loads(json.dumps(exported))
        changed['status']['execution_quota']['reservation_states'] = []
        changed['export_sha256'] = digest_json({k: v for k, v in changed.items() if k != 'export_sha256'})
        self.assertFalse(verify_export(changed)[0])
        for kind, field in (('task.transition', 'attempt'), ('quota.dispatch', 'ordinal')):
            with self.subTest(identity_kind=kind):
                changed = json.loads(json.dumps(exported))
                target = next(event for event in changed['events'] if event['event_type'] == kind and field in event['payload'])
                target['payload'][field] = True
                previous = '0' * 64
                for event in changed['events']:
                    event['previous_hash'] = previous
                    event['event_hash'] = digest_json({'run_id': changed['status']['run_id'], **{key: value for key, value in event.items() if key not in {'event_hash', 'sequence'}}})
                    previous = event['event_hash']
                changed['event_chain_root'] = previous
                changed['status']['event_head_hash'] = previous
                changed['export_sha256'] = digest_json({k: v for k, v in changed.items() if k != 'export_sha256'})
                self.assertFalse(verify_export(changed)[0])
        changed = json.loads(json.dumps(exported))
        changed['status']['execution_quota']['consumption']['executor_calls'] = True
        changed['export_sha256'] = digest_json({k: v for k, v in changed.items() if k != 'export_sha256'})
        self.assertFalse(verify_export(changed)[0])
        changed = json.loads(json.dumps(exported))
        del changed['status']['execution_quota']
        changed['export_sha256'] = digest_json({k: v for k, v in changed.items() if k != 'export_sha256'})
        self.assertFalse(verify_export(changed)[0])

    def test_unknown_and_overrun_exports_preserve_their_actual_state(self):
        class Broken(DeterministicMockExecutor):
            def execute(self, request): raise RuntimeError('synthetic interruption')
        run = self.plan(); self.engine(Broken()).run(run)
        self.assertEqual(verify_export(self.store.export(run)), (True, ()))

    def test_invalid_settlement_counters_do_not_mutate_the_ledger(self):
        run = self.plan(); claim, request = self.claim(run)
        self.store.begin_dispatch(claim, request, 0)
        before = self.store.replay(run)
        for bad in (None, {}, {'tokens': 2}, {'executor_calls': True, 'retained_output_bytes': 0, 'execution_ms': 0},
                    {'executor_calls': 1, 'retained_output_bytes': -1, 'execution_ms': 0},
                    {'executor_calls': 1, 'retained_output_bytes': 0, 'execution_ms': float('inf')}):
            with self.subTest(measurement=bad), self.assertRaises(ValueError):
                self.store.settle_dispatch(claim, 0, bad)
        self.assertEqual(self.store.replay(run), before)

    def test_existing_parallel_claim_cannot_dispatch_after_other_overrun(self):
        run = self.plan(task('a'), task('b'), limits=quota(calls=2))
        first, request_a = self.claim(run)
        second = self.store.claim_ready_task(run, 'second', 10)
        parsed = self.store.load_spec(run)
        request_b = SpecProvider().task_request(parsed, parsed.task('b'), self.root / 'workspace')
        self.store.begin_dispatch(first, request_a, 0)
        self.assertFalse(self.store.settle_dispatch(first, 0, {'executor_calls': 1, 'retained_output_bytes': 5000, 'execution_ms': 1}))
        with self.assertRaises(ValueError): self.store.begin_dispatch(second, request_b, 0)

    def test_other_overrun_vetoes_publication_of_already_measured_parallel_work(self):
        run = self.plan(task('a'), task('b'), limits=quota(calls=2))
        first, request_a = self.claim(run)
        second = self.store.claim_ready_task(run, 'second', 10)
        parsed = self.store.load_spec(run)
        request_b = SpecProvider().task_request(parsed, parsed.task('b'), self.root / 'workspace')
        self.store.begin_dispatch(first, request_a, 0)
        self.store.begin_dispatch(second, request_b, 0)
        self.store.settle_dispatch(second, 0, {'executor_calls': 1, 'retained_output_bytes': 1, 'execution_ms': 1})
        self.store.settle_dispatch(first, 0, {'executor_calls': 1, 'retained_output_bytes': 5000, 'execution_ms': 1})
        receipt = {'execution_quota': self.store.quota_usage(second)}
        published = []
        with self.assertRaises(ValueError):
            self.store.complete_task(second, succeeded=True, receipt=receipt, receipt_hash=digest_json(receipt),
                error=None, retry_base_seconds=0, retry_cap_seconds=0, before_transition=lambda: published.append(True))
        self.assertEqual(published, [])

    def test_kill_preserves_unknown_dispatch_and_refuses_late_settlement(self):
        run = self.plan(); claim, request = self.claim(run)
        self.store.begin_dispatch(claim, request, 0)
        self.store.activate_kill_switch(run, reason='synthetic operator stop')
        with self.assertRaises(LeaseLost):
            self.store.settle_dispatch(claim, 0, {'executor_calls': 1, 'retained_output_bytes': 0, 'execution_ms': 1})
        state = self.store.snapshot(run)
        self.assertEqual(state['state'], 'cancelled')
        self.assertIsNone(state['execution_quota']['consumption'])
        self.assertEqual(state['execution_quota']['occupied']['executor_calls'], 1)

    def test_engine_measures_elapsed_time_instead_of_trusting_result_duration(self):
        from dataclasses import replace
        run = self.plan()
        executor = DeterministicMockExecutor({'build': [replace(result(), duration_seconds=999999)]})
        with patch('ai_software_factory.engine.time.monotonic_ns', side_effect=[0, 1_000_001]):
            self.assertEqual(self.engine(executor).run(run).state, 'succeeded')
        self.assertEqual(self.store.snapshot(run)['execution_quota']['consumption']['execution_ms'], 2)

    def make_v2(self):
        from ai_software_factory.store import SCHEMA
        from ai_software_factory.approvals import APPROVAL_SCHEMA
        path = self.root / 'version-two.sqlite3'
        parsed = FactorySpec.from_dict(spec())
        with closing(sqlite3.connect(path)) as db:
            db.executescript(SCHEMA + APPROVAL_SCHEMA + '; PRAGMA user_version=2;')
            db.execute('INSERT INTO runs(run_id,idempotency_key,spec_hash,spec_json,state,created_at,max_attempts,max_wall_seconds) VALUES(?,?,?,?,?,?,?,?)',
                       ('old', 'old-key', sha256(parsed.canonical_json().encode()).hexdigest(), parsed.canonical_json(), 'created', 1000, 100, 60))
            db.execute("INSERT INTO tasks(run_id,task_id,sort_order,state,max_attempts) VALUES('old','build',0,'ready',2)")
            db.commit()
            before = (db.execute('SELECT * FROM runs').fetchall(), db.execute('SELECT * FROM tasks').fetchall())
        return path, before

    def test_v2_migration_preserves_rows_and_allows_new_quota_run(self):
        path, before = self.make_v2()
        migrated = FactoryStore(path, clock=self.clock)
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(before, (db.execute('SELECT * FROM runs').fetchall(), db.execute('SELECT * FROM tasks').fetchall()))
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 3)
        self.assertNotIn('execution_quota', migrated.snapshot('old'))
        raw = spec(); raw['budget']['execution_quota'] = quota()
        run = migrated.create_run(FactorySpec.from_dict(raw), 'new')
        self.assertEqual(migrated.snapshot(run)['execution_quota']['consumption']['executor_calls'], 0)

    def test_interrupted_v2_quota_migration_is_atomic(self):
        path, before = self.make_v2()
        with patch('ai_software_factory.store.QUOTA_SCHEMA', 'CREATE TABLE temporary_quota_marker(id); INVALID SQL;'):
            with self.assertRaises(sqlite3.OperationalError): FactoryStore(path, clock=self.clock)
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 2)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='temporary_quota_marker'").fetchone())
            self.assertEqual(before, (db.execute('SELECT * FROM runs').fetchall(), db.execute('SELECT * FROM tasks').fetchall()))

    def test_approval_wait_and_quota_share_one_active_wall_clock(self):
        run = self.plan(task(approval='required'), limits=quota(calls=1))
        self.assertTrue(self.engine().run(run).waiting_for_approval)
        self.clock.advance(500)
        request = self.store.approval_request(run, 'build')
        self.store.record_approval(run, 'build', attempt=1, request_sha256=request['request_sha256'],
            decision='approved', decided_by='declared-reviewer', expires_at=self.clock()+30, decision_id='one')
        self.assertEqual(self.engine().run(run).state, 'succeeded')
        self.assertEqual(self.store.snapshot(run)['active_wall_seconds'], 0)

    def test_cli_quota_wait_is_visible_exit_three_and_export_is_verified(self):
        raw = spec(); raw['budget']['execution_quota'] = quota(calls=0)
        input_path = self.root / 'spec.json'; input_path.write_text(json.dumps(raw))
        with redirect_stdout(io.StringIO()) as output:
            exit_code = main(['run', str(input_path), '--db', str(self.db)])
        self.assertEqual(exit_code, 3)
        value = json.loads(output.getvalue())
        self.assertEqual(value['execution_status'], 'waiting_quota')
        self.assertEqual(value['tasks'][0]['attempts'], 0)


if __name__ == '__main__':
    unittest.main()
