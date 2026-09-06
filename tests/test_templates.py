"""Reusable templates through the actual compiler, Store, Engine and CLI."""
from contextlib import closing, redirect_stdout, redirect_stderr
from copy import deepcopy
from dataclasses import replace
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from ai_software_factory.cli import main
from ai_software_factory.engine import FactoryEngine
from ai_software_factory.evidence import digest_json, verify_export
from ai_software_factory.executors import DeterministicMockExecutor, SpecProvider, SubprocessExecutor
from ai_software_factory.models import FactorySpec, SpecError
from ai_software_factory.store import FactoryStore, IdempotencyConflict, StoreError
from ai_software_factory.state import RunState
from ai_software_factory.templates import CATALOG_FORMAT, compile_template, validate_origin, read_json, MAX_CATALOG_BYTES
from tests.support import ManualClock, spec, task, result

# Fixed trusted fixture programs. Bindings are read as data and escaped with repr.
BUILD = "import os;from pathlib import Path;p=Path('src/message.py');p.parent.mkdir(parents=True,exist_ok=True);p.write_text('PREFIX = '+repr(os.environ['FACTORY_INPUT_PREFIX'])+'\\ndef render(text):\\n    return PREFIX + text\\n',encoding='utf-8')"
SYNTAX = "from pathlib import Path;compile(Path('src/message.py').read_text(encoding='utf-8'),'message.py','exec')"
VERIFY = "import os,runpy,json;from pathlib import Path;n=runpy.run_path('src/message.py');actual=n['render']('value');assert actual==os.environ['FACTORY_INPUT_PREFIX']+'value';p=Path('reports/contract.json');p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps({'actual':actual,'matched':True},ensure_ascii=False),encoding='utf-8')"


def catalog(raw=None):
    raw = raw or spec(task(description='Build {{LABEL}}', environment={'FACTORY_INPUT_TEXT':'{{TEXT}}'}), name='Project {{LABEL}}')
    return {'format':CATALOG_FORMAT,'templates':{'component-v1':raw}}


def rehash(exported):
    previous = '0'*64
    for event in exported['events']:
        event['previous_hash'] = previous
        material = {key:event[key] for key in ('task_id','event_type','payload','created_at','event_key','previous_hash')}
        material['run_id'] = exported['status']['run_id']
        event['event_hash'] = previous = digest_json(material)
    exported['event_chain_root'] = exported['status']['event_head_hash'] = previous
    exported['status']['event_count'] = len(exported['events'])
    exported['export_sha256'] = digest_json({key:value for key,value in exported.items() if key!='export_sha256'})


class TemplateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); self.clock = ManualClock()
        self.db = self.root/'factory.sqlite3'; self.store = FactoryStore(self.db,clock=self.clock)
        self.engine = FactoryEngine(self.store,base_directory=self.root,executor=DeterministicMockExecutor(),clock=self.clock,
                                    sleeper=lambda _:self.fail('template wait must not spin'))
        self.catalog = catalog(); self.bindings = {'LABEL':'example','TEXT':'literal input'}

    def compile(self): return compile_template(self.catalog,'component-v1',self.bindings)

    def plan(self): return self.engine.plan_template(self.catalog,'component-v1',self.bindings)

    def test_normalized_template_and_binding_order_have_exact_identity(self):
        first = self.compile()
        normalized = catalog(FactorySpec.from_dict(self.catalog['templates']['component-v1']).to_dict())
        second = compile_template(normalized,'component-v1',dict(reversed(list(self.bindings.items()))))
        self.assertEqual(first.to_dict(),second.to_dict())
        self.assertEqual(first.origin['spec_sha256'],hashlib.sha256(first.spec.canonical_json().encode()).hexdigest())
        self.assertEqual(first.spec.tasks[0].command,('python','-c','pass'))
        self.assertEqual(dict(first.spec.tasks[0].environment),{'FACTORY_INPUT_TEXT':'literal input'})

    def test_unknown_schema_template_and_unused_invalid_template_refused(self):
        values = [{},None,[],{'format':'unknown','templates':self.catalog['templates']},
                  {**self.catalog,'extra':True},{'format':CATALOG_FORMAT,'templates':{}},
                  {'format':CATALOG_FORMAT,'templates':{'bad/id':spec()}},
                  {'format':CATALOG_FORMAT,'templates':{**self.catalog['templates'],'unused':{'unknown':True}}}]
        for value in values:
            with self.subTest(value=value),self.assertRaises(SpecError):compile_template(value,'component-v1',self.bindings)
        with self.assertRaisesRegex(SpecError,'unknown template'):compile_template(self.catalog,'absent',self.bindings)
        self.assertFalse((self.root/'workspace').exists())

    def test_bindings_exact_types_bounds_no_recursive_substitution(self):
        values = [None,{},[],True,{**self.bindings,'EXTRA':'x'},{'LABEL':'x'},
                  {**self.bindings,'TEXT':'x'*4097},{**self.bindings,'TEXT':'{{LABEL}}'},
                  {**self.bindings,'TEXT':'{{bad}}'}]
        for bad in (True,False,1,1.0,float('inf'),float('nan'),None,[],{}):values.append({**self.bindings,'TEXT':bad})
        for value in values:
            with self.subTest(value=value),self.assertRaises(SpecError):compile_template(self.catalog,'component-v1',value)

    def test_structural_and_execution_placeholders_are_forbidden(self):
        changes = [('workspace','{{TEXT}}'),('owner','{{TEXT}}'),('id','{{TEXT}}'),
                   ('command',['python','-c','{{TEXT}}']),('depends_on',['{{TEXT}}']),
                   ('owned_paths',['{{TEXT}}']),('artifacts',['{{TEXT}}']),
                   ('approval','{{TEXT}}'),('tests',[{'name':'t','command':['python','-c','{{TEXT}}']}])]
        for field,value in changes:
            raw=deepcopy(self.catalog)
            if field=='workspace':raw['templates']['component-v1'][field]=value
            else:raw['templates']['component-v1']['tasks'][0][field]=value
            with self.subTest(field=field),self.assertRaises(SpecError):compile_template(raw,'component-v1',self.bindings)
        for name in ('PATH','PYTHONPATH','LD_PRELOAD','HOME','COMSPEC','APPLICATION_DATA'):
            raw=deepcopy(self.catalog);raw['templates']['component-v1']['tasks'][0]['environment']={name:'{{TEXT}}'}
            with self.subTest(name=name),self.assertRaises(SpecError):compile_template(raw,'component-v1',self.bindings)

    def test_dag_ownership_and_native_policy_are_still_validated(self):
        for tasks in ([task('a',depends_on=['a'])],[task('a',depends_on=['absent'])],
                      [task('a',owned_paths=['src']),task('b',owned_paths=['src/x'])],
                      [task(environment={'API_CREDENTIAL':'synthetic'})]):
            with self.subTest(tasks=tasks),self.assertRaises(SpecError):compile_template(catalog(spec(*tasks)),'component-v1',{})

    def test_json_duplicate_overflow_depth_surrogate_and_sizes(self):
        for source in ('{"x":1,"x":2}','{"x":1e309}','{"x":NaN}','[',b'\xff','[ '*2000+'0'+'] '*2000,'"\\ud800"'):
            with self.subTest(source=str(source)[:40]),self.assertRaises(SpecError):read_json(source,maximum=MAX_CATALOG_BYTES)
        with self.assertRaises(SpecError):read_json(' '*101,maximum=100)
        with self.assertRaises(SpecError):compile_template({'format':CATALOG_FORMAT,'templates':{f't{i}':spec() for i in range(33)}},'t1',{})

    def test_supported_substitution_fields_preserve_input_and_literal_data(self):
        raw=self.catalog['templates']['component-v1'];compiled=self.compile().spec.to_dict()
        self.assertEqual(compiled['name'],'Project example')
        self.assertEqual(compiled['tasks'][0]['description'],'Build example')
        self.assertEqual(compiled['tasks'][0]['environment'],{'FACTORY_INPUT_TEXT':'literal input'})
        self.assertEqual(raw['tasks'][0]['description'],'Build {{LABEL}}')

    def test_origin_recompiles_inputs_not_digest_alone(self):
        compiled=self.compile()
        self.assertEqual(validate_origin(compiled.spec,compiled.origin),compiled.origin)
        for field,value in [('format','other'),('compiler','other'),('template_sha256','0'*64),
                            ('bindings',{'LABEL':'example','TEXT':'changed'}),('provenance','authenticated'),
                            ('spec_sha256','0'*64),('template_id',False)]:
            origin=deepcopy(compiled.origin);origin[field]=value
            with self.subTest(field=field),self.assertRaises(SpecError):validate_origin(compiled.spec,origin)
        forged=deepcopy(compiled.origin);forged['template']['tasks'][0]['command']=['python','-c','print(99)']
        forged['template_sha256']=digest_json(forged['template'])
        with self.assertRaises(SpecError):validate_origin(compiled.spec,forged)

    def test_single_store_origin_atomic_idempotent_resume_and_export(self):
        run=self.plan();self.assertEqual(self.plan(),run)
        events=self.store.replay(run)
        self.assertEqual([e['event_type'] for e in events],['run.created','run.template_compiled'])
        self.assertEqual(events[0]['payload']['template_origin_sha256'],digest_json(events[1]['payload']))
        reopened=FactoryStore(self.db,clock=self.clock)
        engine=FactoryEngine(reopened,base_directory=self.root,executor=DeterministicMockExecutor(),clock=self.clock)
        self.assertEqual(engine.run(run).state,RunState.SUCCEEDED)
        exported=reopened.export(run);self.assertEqual(verify_export(exported),(True,()))
        self.assertEqual(exported['receipts'][0]['receipt']['spec_hash'],self.compile().origin['spec_sha256'])
        self.assertEqual(reopened.load_spec(run),self.compile().spec)

    def test_same_spec_different_origin_or_plain_key_is_not_silently_relabelled(self):
        compiled=self.compile();run=self.store.create_run(compiled.spec,'fixed',template_origin=compiled.origin)
        with self.assertRaises(IdempotencyConflict):self.store.create_run(compiled.spec,'fixed')
        other=compile_template({'format':CATALOG_FORMAT,'templates':{'other':self.catalog['templates']['component-v1']}},'other',self.bindings)
        with self.assertRaises(IdempotencyConflict):self.store.create_run(other.spec,'fixed',template_origin=other.origin)
        self.assertNotEqual(self.store.create_run(compiled.spec),run)
        self.assertNotEqual(self.store.create_run(other.spec,template_origin=other.origin),run)

    def test_two_store_connections_register_one_origin_without_duplicates(self):
        barrier=threading.Barrier(2);results=[];failures=[]
        compiled=self.compile()
        def plan():
            try:
                store=FactoryStore(self.db,clock=self.clock);barrier.wait(5)
                results.append(store.create_run(compiled.spec,template_origin=compiled.origin))
            except BaseException as exc:failures.append(exc)
        threads=[threading.Thread(target=plan) for _ in range(2)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(10)
        self.assertFalse(failures);self.assertEqual(len(results),2);self.assertEqual(results[0],results[1])
        self.assertEqual(len(self.store.replay(results[0])),2)

    def test_registration_fault_rolls_back_spec_tasks_and_origin_together(self):
        native=self.store._event
        def fail(*a,**kw):
            if kw['event_type']=='run.template_compiled':raise sqlite3.OperationalError('synthetic interrupt')
            return native(*a,**kw)
        with patch.object(self.store,'_event',side_effect=fail),self.assertRaises(sqlite3.OperationalError):self.plan()
        with closing(sqlite3.connect(self.db)) as connection:
            for table in ('runs','tasks','events'):
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)
        self.assertEqual(len(self.store.replay(self.plan())),2)

    def test_export_rehashed_origin_tamper_and_omissions_are_refused(self):
        exported=self.store.export(self.plan())
        for change in ('bindings','missing','duplicate','late','anchor','spec'):
            forged=deepcopy(exported)
            if change=='bindings':forged['events'][1]['payload']['bindings']['TEXT']='tampered'
            elif change=='missing':del forged['events'][1]
            elif change=='duplicate':forged['events'].append(deepcopy(forged['events'][1]))
            elif change=='late':forged['events'].reverse()
            elif change=='anchor':forged['events'][0]['payload']['template_origin_sha256']='0'*64
            elif change=='spec':forged['spec']['name']='changed';forged['status']['spec_hash']=digest_json(forged['spec'])
            rehash(forged)
            with self.subTest(change=change):self.assertFalse(verify_export(forged)[0])

    def test_database_rehashed_false_origin_is_refused_before_dispatch(self):
        run=self.plan();forged=self.store.export(run)
        forged['events'][1]['payload']['bindings']['TEXT']='different';rehash(forged)
        with closing(sqlite3.connect(self.db)) as connection:
            for event in forged['events']:
                connection.execute('UPDATE events SET payload_json=?,previous_hash=?,event_hash=? WHERE run_id=? AND event_key=?',
                    (json.dumps(event['payload'],sort_keys=True,separators=(',',':')),event['previous_hash'],event['event_hash'],run,event['event_key']))
            connection.execute('UPDATE runs SET event_head_hash=? WHERE run_id=?',(forged['event_chain_root'],run));connection.commit()
        with self.assertRaises(StoreError):self.store.load_spec(run)
        with self.assertRaises(StoreError):self.engine.run(run)

    def test_approval_and_quota_share_the_same_effective_task(self):
        raw=self.catalog['templates']['component-v1'];raw['tasks'][0]['approval']='required'
        raw['budget']['execution_quota']={'limits':{'executor_calls':2,'retained_output_bytes':8192,'execution_ms':4000}}
        run=self.plan();waiting=self.engine.run(run)
        self.assertTrue(waiting.waiting_for_approval)
        self.assertEqual(self.store.snapshot(run)['receipt_count'],0)
        request=self.store.approval_request(run,'build')
        self.store.record_approval(run,'build',attempt=request['attempt'],request_sha256=request['request_sha256'],
            decision='approved',decided_by='synthetic-reviewer',expires_at=1060,decision_id='template-approval')
        self.assertEqual(self.engine.run(run).state,RunState.SUCCEEDED)
        exported=self.store.export(run);self.assertTrue(verify_export(exported)[0])
        self.assertEqual(exported['receipts'][0]['receipt']['execution_quota']['consumption']['executor_calls'],1)

    def test_template_reopened_refuses_dynamic_task_provider_before_dispatch(self):
        class Dynamic(SpecProvider):
            def task_request(self,*args):
                return replace(super().task_request(*args),argv=('changed-command',))
        self.catalog['templates']['component-v1']['tasks'][0]['max_attempts']=1
        run=self.plan();reopened=FactoryStore(self.db,clock=self.clock)
        engine=FactoryEngine(reopened,base_directory=self.root,executor=DeterministicMockExecutor(),provider=Dynamic(),clock=self.clock)
        with patch.object(engine.executor,'execute',wraps=engine.executor.execute) as dispatch:
            self.assertEqual(engine.run(run).state,RunState.FAILED)
            dispatch.assert_not_called()
        self.assertTrue(verify_export(reopened.export(run))[0])

    def test_template_refuses_dynamic_test_provider_after_fixed_main_only(self):
        class Dynamic(SpecProvider):
            def test_request(self,*args):
                return replace(super().test_request(*args),argv=('changed-test',))
        raw=self.catalog['templates']['component-v1']['tasks'][0]
        raw['max_attempts']=1;raw['tests']=[{'name':'check','command':['python','-c','pass']}]
        run=self.plan();self.engine.provider=Dynamic()
        with patch.object(self.engine.executor,'execute',wraps=self.engine.executor.execute) as dispatch:
            self.assertEqual(self.engine.run(run).state,RunState.FAILED)
            self.assertEqual(dispatch.call_count,1)
            self.assertEqual(dispatch.call_args.args[0].argv,('python','-c','pass'))
        self.assertTrue(verify_export(self.store.export(run))[0])

    def test_native_unprotected_run_preserves_dynamic_provider_contract(self):
        class Dynamic(SpecProvider):
            def task_request(self,*args):
                return replace(super().task_request(*args),argv=('native-dynamic-main',))
            def test_request(self,*args):
                return replace(super().test_request(*args),argv=('native-dynamic-test',))
        self.engine.provider=Dynamic()
        run=self.engine.plan(FactorySpec.from_dict(spec(task(tests=[{'name':'check','command':['python','-c','pass']}]))))
        with patch.object(self.engine.executor,'execute',wraps=self.engine.executor.execute) as dispatch:
            self.assertEqual(self.engine.run(run).state,RunState.SUCCEEDED)
            self.assertEqual([call.args[0].argv for call in dispatch.call_args_list],
                             [('native-dynamic-main',),('native-dynamic-test',)])
        self.assertTrue(verify_export(self.store.export(run))[0])

    def test_cli_compile_plan_no_overwrite_and_invalid_has_no_database(self):
        source=self.root/'catalog.json';bindings=self.root/'bindings.json'
        source.write_text(json.dumps(self.catalog));bindings.write_text(json.dumps(self.bindings))
        options=[str(source),'--template','component-v1','--bindings',str(bindings)]
        output=io.StringIO()
        with redirect_stdout(output):self.assertEqual(main(['template-compile',*options]),0)
        self.assertEqual(json.loads(output.getvalue()),self.compile().to_dict())
        target=self.root/'compiled.json'
        with redirect_stdout(io.StringIO()):self.assertEqual(main(['template-compile',*options,'--output',str(target)]),0)
        data=target.read_bytes()
        with redirect_stderr(io.StringIO()):self.assertEqual(main(['template-compile',*options,'--output',str(target)]),2)
        self.assertEqual(target.read_bytes(),data)
        other=self.root/'not-created.sqlite3';bad=[str(source),'--template','absent','--bindings',str(bindings),'--db',str(other)]
        with redirect_stderr(io.StringIO()):self.assertEqual(main(['template-plan',*bad]),2)
        self.assertFalse(other.exists())
        with redirect_stdout(io.StringIO()):self.assertEqual(main(['template-plan',*options,'--db',str(self.db)]),0)

    def test_real_subprocess_project_build_test_artifacts_and_cli_replay(self):
        raw=spec(task('build',command=[sys.executable,'-I','-S','-B','-c',BUILD],owned_paths=['src/message.py'],artifacts=['src/message.py'],
            environment={'FACTORY_INPUT_PREFIX':'{{PREFIX}}'},tests=[{'name':'syntax','command':[sys.executable,'-I','-S','-B','-c',SYNTAX]}]),
            task('verify',depends_on=['build'],command=[sys.executable,'-I','-S','-B','-c',VERIFY],owned_paths=['reports/contract.json'],
                 artifacts=['reports/contract.json'],environment={'FACTORY_INPUT_PREFIX':'{{PREFIX}}'}),name='Reusable component')
        source=self.root/'catalog.json';binding=self.root/'binding.json'
        source.write_text(json.dumps(catalog(raw)))
        for index,prefix in enumerate(("alpha ","quoted '; literal ")):
            bindings={'PREFIX':prefix};binding.write_text(json.dumps(bindings))
            options=[str(source),'--template','component-v1','--bindings',str(binding),'--db',str(self.db)]
            out=io.StringIO()
            with redirect_stdout(out):self.assertEqual(main(['template-run',*options]),0)
            status=json.loads(out.getvalue());run=status['run_id']
            generated=(self.root/'workspace/src/message.py').read_text()
            self.assertIn(repr(prefix),generated)
            self.assertEqual(json.loads((self.root/'workspace/reports/contract.json').read_text())['actual'],prefix+'value')
            exported=self.store.export(run);self.assertTrue(verify_export(exported)[0])
            self.assertEqual(exported['status']['receipt_count'],2)
            for receipt in exported['receipts']:
                for artifact in receipt['receipt']['artifacts']:
                    content=(self.root/'workspace'/artifact['path']).read_bytes()
                    self.assertEqual(artifact['sha256'],'sha256:'+hashlib.sha256(content).hexdigest())
                    self.assertEqual(artifact['size'],len(content))
            prior=len(self.store.replay(run))
            with redirect_stdout(io.StringIO()):self.assertEqual(main(['template-run',*options]),0)
            self.assertEqual(len(self.store.replay(run)),prior)

    def test_effective_failure_is_failed_not_template_success(self):
        failing=DeterministicMockExecutor(scripted={'build':[result(exit_code=9)]})
        engine=FactoryEngine(self.store,base_directory=self.root,executor=failing,clock=self.clock,sleeper=lambda _:None)
        self.catalog['templates']['component-v1']['tasks'][0]['max_attempts']=1
        run=engine.plan_template(self.catalog,'component-v1',self.bindings)
        self.assertEqual(engine.run(run).state,RunState.FAILED)
        self.assertTrue(verify_export(self.store.export(run))[0])


if __name__=='__main__':unittest.main()
