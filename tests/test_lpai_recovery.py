import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

from lab.company import Company
from lab.research import Research,plan_digest
from lab.store import Store
from lab.lpai_evidence import scrub,failed_training_pods,gpu_count,apply_code_patches


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        # Only fresh, self-owned temporary databases/workspaces. No .data,
        # existing .evidence, SDK environment, credentials or live transport.
        self.temp=tempfile.TemporaryDirectory(prefix='iterlab-lpai-recovery-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.assertFalse(self.root.is_relative_to(Path(__file__).resolve().parents[1]))
        for target in ('subprocess.run','subprocess.Popen','socket.create_connection','socket.socket.connect','socket.socket.connect_ex'):
            blocker=patch(target,side_effect=AssertionError('offline fixture forbids real process/network'))
            blocker.start();self.addCleanup(blocker.stop)
        self.store=Store(self.root)
        self.addCleanup(self.store.close)
        (self.root/'settings.json').write_text(json.dumps({'channel':'ept','profile':'fixture-profile'}),encoding='utf-8')
        self.project=self.store.create_project('cloud-recovery-fixture')
        self.session=self.store.create_session(self.project['id'],'ept');self.sid=self.session['id']
        self.calls=[];self.phase='failed';self.sdk_calls=[];self.sdk_params=[];self.sdk_unknown=False;self.config_digest='a'*64
        self.task_overrides={};self.submission_echo=None
        def runner(args,**kwargs):
            self.assertIn(args[0],{'lpai-agent','git'})
            self.calls.append(args)
            task={'uid':args[args.index('--uid')+1] if '--uid' in args else 'original-task',
                  'name':'retry-fixture','namespace':'vc-chassis','status':self.phase,'cluster':'fixture-cluster','project_uid':'component-1',
                  'create_timestamp':{'time':datetime.now(timezone.utc).isoformat()},
                  'expected_duration_desc':self.submission_echo}
            task.update(self.task_overrides)
            if 'inspect' in args:
                pods=[{'name':'init-pod','step':'dataPreparation','pod_type':'user','status':'Succeeded','exit_code':0},
                    {'name':'training-master','uid':'pod-uid','step':'task','pod_type':'user','status':'deleted','exit_code':1,
                     'create_timestamp':{'time':'2026-10-29T01:00:00Z'},'finish_timestamp':{'time':'2026-10-29T01:01:00Z'}}]
                value={'task':task,'pods':{'status':'ok','data':pods},'_inspected_pods':['init-pod'],'pod_events':{},'summary_events':{'data':[]}}
            elif 'logs' in args:
                value={'result':[{'values':[['123',"ModuleNotFoundError: No module named 'misc'"],['124','PRIVATE_CREDENTIAL=do-not-store']]}]}
            else:value=task
            return subprocess.CompletedProcess(args,0,json.dumps({'status':'ok','data':value}),'')
        self.company=Company(self.store,runner=runner,locator=lambda name:name)
        self.addCleanup(self.company.close)
        self.research=Research(self.store)
        self.addCleanup(self.research.close)
        self.company.configure({'revision':0,'lpai_enabled':True})
        def sdk(operation,**kwargs):
            self.sdk_calls.append(operation);self.sdk_params.append((operation,kwargs))
            if operation=='component':return {'uid':'component-1','name':'fixed-component','namespace':'vc-chassis','cluster':'fixture-cluster','digest':self.config_digest,'gpu_count':16}
            if operation=='submit':
                self.submission_echo=kwargs['task_information']['expected_duration_desc']
                if self.sdk_unknown:raise TimeoutError('outcome unknown')
                return {'state':'submitted','item':{'uid':'new-task','name':'retry-fixture','phase':'pending','namespace':'vc-chassis'}}
            raise AssertionError(operation)
        self.company.training.sdk=sdk
        self.plan=self.store.add_record(self.sid,{'kind':'plan','title':'repair plan','content':'Keep protocol and allocation; repair import; verify before retry.','status':'proposed','evidence':[]},{})
        self.trial=self.store.add_record(self.sid,{'kind':'trial','title':'import repair','content':'Check import and resume fixed checkpoint','status':'proposed','evidence':[],'plan_id':self.plan['id']},{})

    def unknown_submission(self):
        prepared=self.prepared();self.grant();self.sdk_unknown=True
        result=self.company.training.submit(self.sid,prepared['submission_id'],True)
        self.assertEqual(result['state'],'unknown')
        return prepared['submission_id']

    def training_row(self,id):
        return self.store.one('SELECT * FROM company_training WHERE id=?',(id,))

    def identity_row(self,id):
        return self.store.one('SELECT * FROM company_training_identity WHERE submission_id=?',(id,))

    def assert_unknown_original(self,id,task_uid=None):
        row=self.training_row(id)
        self.assertEqual(row['state'],'unknown');self.assertEqual(row['task_uid'],task_uid)
        self.assertEqual(self.sdk_calls.count('submit'),1)
        self.assertEqual(self.store.rows('SELECT id FROM company_watches WHERE session_id=?',(self.sid,)),[])

    def test_output_contract_preparation_creates_immutable_producer_without_submitting(self):
        request={'plan_id':self.plan['id'],'trial_id':self.trial['id'],'component_uid':'component-1',
            'task_name':'result-contract-fixture','reason':'preserve exact result identity','predecessor_uid':'',
            'diagnostic_id':'','change_summary':'','validation':'','output_contract':{'required_files':['metrics.json']},
            'task_information':{'problem_statement':'verify result association','solution_approach':'emit producer receipt',
                'expected_result':'exact byte checks','expected_duration':1,'expected_duration_desc':'isolated fixture'}}
        result=self.company.training.prepare(self.sid,request)
        self.assertTrue(result['result_bundle']['integration_required'])
        id=result['submission_id'];frozen=json.loads(self.training_row(id)['request'])
        self.assertEqual(frozen['output_contract']['expected']['run_id'],id)
        workspace=Path(self.session['workspace'])
        self.assertTrue((workspace/result['result_bundle']['writer']).is_file())
        self.assertEqual(self.company.training.prepare(self.sid,request)['submission_id'],id)
        self.assertEqual(len(self.store.rows('SELECT id FROM company_training')),1)
        self.assertNotIn('submit',self.sdk_calls);self.assertEqual(self.store.rows('SELECT * FROM company_watches'),[])
        (workspace/result['result_bundle']['writer']).write_text('changed')
        with self.assertRaisesRegex(ValueError,'工作包'):self.company.training.prepare(self.sid,request)
        before=len(self.sdk_calls)
        request['output_contract']={'required_files':['../metrics.json']}
        with self.assertRaises(ValueError):self.company.training.prepare(self.sid,request)
        self.assertEqual(len(self.sdk_calls),before)

    def prepared(self):
        evidence=self.company.training.diagnose(self.sid,'original-task')
        return self.company.training.prepare(self.sid,{'plan_id':self.plan['id'],'trial_id':self.trial['id'],
            'component_uid':'component-1','task_name':'retry-fixture','reason':'verify import repair',
            'predecessor_uid':'original-task','diagnostic_id':evidence['id'],'change_summary':'correct source path',
            'validation':'same-image import check passed; fixture only',
            'task_information':{'problem_statement':'recover startup','solution_approach':'fix confirmed import path',
                'expected_result':'reach real training and save outputs','expected_duration':150,'expected_duration_desc':'fixed bounded configuration'}})

    def grant(self):
        self.research.review(self.sid,{'plan_id':self.plan['id'],'expected_digest':plan_digest(self.plan),'decision':'accept',
            'reason':'fixture exact approval','execution':'lpai','allow_cloud':True,'component_uid':'component-1',
            'component_digest':'a'*64,'max_gpus':16,'max_runs':2,'max_fits':2,'timeout':9000})

    def test_deleted_failed_training_pod_selected_with_original_window(self):
        before=self.store.rows('SELECT id FROM records')
        result=self.company.training.diagnose(self.sid,'original-task')
        logs=[args for args in self.calls if 'logs' in args]
        self.assertEqual(len(logs),1);self.assertIn('training-master',logs[0]);self.assertNotIn('init-pod',logs[0])
        self.assertIn('2026-10-29T01:00:00Z',logs[0]);self.assertIn('2026-10-29T01:01:00Z',logs[0])
        self.assertIn('ModuleNotFoundError',json.dumps(result))
        proof=(Path(self.project['workspace'])/result['path']).read_text(encoding='utf-8')
        self.assertNotIn('do-not-store',proof)
        count=len(self.calls);self.assertTrue(self.company.training.diagnose(self.sid,'original-task')['cached']);self.assertEqual(len(self.calls),count)
        self.assertEqual(self.store.rows('SELECT id FROM records'),before,'取证保存在运行诊断中，不自动新建研究进展')
        self.assertEqual(len(self.store.rows('SELECT id FROM company_diagnostics')),1)

    def test_scrub_credentials_and_encoded_environment(self):
        raw={'lpai_envs':[{'name':'secret','value':'never'}],'token':'never','logs':'SOME_ENV=never\nModuleNotFoundError: missing'}
        self.assertNotIn('never',json.dumps(scrub(raw)))

    def test_same_plan_repair_reuses_trial_before_any_platform_read(self):
        from lab.store import now
        with patch.object(self.company.training,'prepare',side_effect=lambda sid,request:request):
            request=self.prepared()
        self.store.execute('INSERT INTO company_training VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            ('original-submission',self.sid,self.project['id'],self.plan['id'],self.trial['id'],
             'component-1','original-task','{}','failed',None,now(),now()))
        other=self.store.add_record(self.sid,{'kind':'trial','title':'unnecessary duplicate','content':'same repair',
            'plan_id':self.plan['id'],'evidence':[]},{})
        before=len(self.calls);writes=len(self.sdk_calls)
        with self.assertRaisesRegex(ValueError,'复用原实验trial_id'):
            self.company.training.prepare(self.sid,{**request,'trial_id':other['id']})
        self.assertEqual(len(self.calls),before);self.assertEqual(len(self.sdk_calls),writes)
        self.assertEqual(len(self.store.rows('SELECT * FROM company_training')),1)

    def test_runtime_and_placeholder_lines_survive_bounded_failure_summary(self):
        lines=[{'timestamp':'123','text':'IterLab CPU component ready'},
               {'timestamp':'124','text':'IterLab result lifecycle: results_incomplete'}]
        with patch('lab.lpai_training.log_lines',return_value=lines):
            result=self.company.training.diagnose(self.sid,'original-task')
        self.assertEqual([e['text'] for e in result['summary']['signals']],[e['text'] for e in lines])
        self.assertIn('不是自动根因认定',result['summary']['meaning'])

    def test_finished_pod_second_does_not_truncate_final_fractional_traceback(self):
        self.task_overrides['update_timestamp']={'time':'2026-10-29T01:01:10.15Z'}
        result=self.company.training.diagnose(self.sid,'original-task')
        logs=[args for args in self.calls if 'logs' in args]
        self.assertEqual(logs[0][logs[0].index('--end')+1],'2026-10-29T01:01:10.15Z')
        self.assertEqual(result['summary']['projection_version'],4)
        self.assertFalse(result['summary']['partial'])

    def test_pre_training_failure_retains_official_stage_without_invented_cause(self):
        self.task_overrides['flows']=[{'name':'dataPreparation','phase':'failed','duration':0,
            'message':'pipeline-init error','private_env':{'token':'never'}},
            {'name':'task','phase':'','startedat':0}]
        with patch('lab.lpai_training.failed_training_pods',return_value=[]):
            result=self.company.training.diagnose(self.sid,'original-task')
        summary=result['summary']
        self.assertEqual(summary['failed_stages'][0]['name'],'dataPreparation')
        self.assertEqual(summary['failed_stages'][0]['message'],'pipeline-init error')
        self.assertEqual(summary['log_pods'],0)
        self.assertTrue(summary['partial'])
        self.assertNotIn('never',json.dumps(summary))
        self.assertNotIn('root_cause',summary)
        # An old summary is retained as history, refreshed once, then cached.
        legacy={**summary,'projection_version':3}
        self.store.execute('UPDATE company_diagnostics SET summary=? WHERE id=?',(json.dumps(legacy),result['id']))
        updated=self.company.training.diagnose(self.sid,'original-task')
        self.assertFalse(updated['cached']);self.assertNotEqual(updated['id'],result['id'])
        count=len(self.calls)
        self.assertTrue(self.company.training.diagnose(self.sid,'original-task')['cached'])
        self.assertEqual(len(self.calls),count)

    def test_official_zero_alias_does_not_hide_sixteen_gpu_allocation(self):
        roles=[{'resource':{'Gpu':8,'gpu':0},'replica':1}]*2
        self.assertEqual(gpu_count(roles,{'num':16,'resource_type':'A800'}),16)
        with self.assertRaises(ValueError):gpu_count([{'resource':{'Gpu':8,'gpu':4},'replica':1}])
        with self.assertRaises(ValueError):gpu_count([])
        with self.assertRaises(ValueError):gpu_count(roles,{'num':8,'resource_type':'A800'})

    def test_task_source_patch_preserves_component_and_rejects_resource_changes(self):
        original={'lpai_git':[{'commit_id':'a'*40}],'lpai_task':{'cmd':'old-entry','roles':[{'cmd':'old-entry','resource':{'Gpu':8}}]}}
        updated=apply_code_patches(original,[{'path':'/lpai_git/0/commit_id','value':'b'*40},{'path':'/lpai_task/cmd','value':'python fixed.py'}])
        self.assertEqual(original['lpai_task']['cmd'],'old-entry');self.assertEqual(updated['lpai_task']['roles'][0]['resource']['Gpu'],8)
        for bad in [{'path':'/lpai_task/roles/0/resource/Gpu','value':'16'},{'path':'/lpai_git/0/commit_id','value':'main'},
                    {'path':'/lpai_task/cmd','value':'old-entry'},{'path':'/lpai_task/roles/9/cmd','value':'new'}]:
            with self.assertRaises(ValueError):apply_code_patches(original,[bad])

    def test_patch_file_must_be_frozen_in_exact_plan(self):
        prepared=self.prepared();row=self.store.one('SELECT request FROM company_training WHERE id=?',(prepared['submission_id'],))
        request=json.loads(row['request']);allowed={'plan_id','trial_id','component_uid','task_name','reason','predecessor_uid','diagnostic_id','change_summary','validation','task_information'}
        request={key:value for key,value in request.items() if key in allowed};request['patch_file']='repair.json'
        (Path(self.project['workspace'])/'repair.json').write_text('[{"path":"/lpai_task/cmd","value":"python fixed.py"}]')
        with self.assertRaisesRegex(ValueError,'冻结证据'):self.company.training.prepare(self.sid,request)

    def test_already_stopped_binding_does_not_arm_auto_resume(self):
        self.phase='stopped';self.company.watch(self.sid,'original-task',True)
        watch=self.store.one('SELECT * FROM company_watches WHERE session_id=?',(self.sid,))
        self.assertEqual(watch['wake_state'],'already_terminal');self.assertEqual(watch['auto_resume'],0)

    def test_external_stop_queues_readonly_analysis_without_stopping_or_resubmitting_tasks(self):
        self.phase='pending';self.company.watch(self.sid,'original-task',True)
        sent=[];kernel=types.SimpleNamespace(settings=types.SimpleNamespace(value={'channel':'ept'}),send=lambda *a,**kw:sent.append(a))
        self.phase='stopped';self.company.poll_watches(kernel,clock=10**12)
        self.assertEqual(len(sent),1)
        self.assertIn('停止/取消只触发分析，不代表允许重投',sent[0][1])
        self.assertEqual(len(self.company.context(self.sid)['training']['notices']),1)
        self.assertEqual(len(self.company.context(self.sid)['training']['diagnostics']),1)
        self.assertNotIn('submit',self.sdk_calls)
        self.assertEqual(self.store.rows('SELECT * FROM company_operations'),[])

    def test_unapproved_cloud_cannot_submit_or_fall_back_local(self):
        prepared=self.prepared()
        with self.assertRaises(ValueError):self.company.training.submit(self.sid,prepared['submission_id'],True)
        self.assertNotIn('submit',self.sdk_calls)
        self.grant()
        with self.assertRaisesRegex(ValueError,'云端训练'):self.research.run(self.sid,{'plan_id':self.plan['id']},'local-bypass')

    def test_exact_sdk_submission_and_unknown_never_replayed(self):
        prepared=self.prepared();self.grant();self.sdk_unknown=True
        result=self.company.training.submit(self.sid,prepared['submission_id'],True)
        self.assertEqual(result['state'],'unknown')
        self.assertEqual(self.company.training.submit(self.sid,prepared['submission_id'],True),result)
        self.assertEqual(self.sdk_calls.count('submit'),1)
        self.assertEqual(self.research.status(self.sid)['attempts'],{'runs':1,'fits':1})
    def test_autonomous_cloud_uses_explicit_scope_and_records_program_source(self):
        prepared=self.prepared();self.grant()
        scope={'objective':'隔离云范围验证','evaluation':'固定协议','data_boundary':'固定组件数据','allowed_changes':'仅固定组件任务级已验证修复','frozen_files':[]}
        self.research.autonomy.enable(self.sid,self.plan['id'],plan_digest(self.plan),scope,'用户隔离授权，不调用真实平台')
        authority=self.company.training.delegated_submission(self.sid,prepared['submission_id'])
        self.assertIsNotNone(authority)
        request={'action':'lpai_submit','submission_id':prepared['submission_id']}
        result=self.company.perform(self.sid,request,'authorized-cloud',True,approval_source=authority)
        self.assertEqual(result['state'],'submitted')
        self.assertEqual(result['approval_source']['kind'],'delegated_research_scope')
        self.assertEqual(self.sdk_calls.count('submit'),1)
        view=self.company.session_runs(self.sid)
        self.assertEqual(len(view['history'])+len(view['active']),1) # Submitted task and its watch are one run.
        self.assertIsNotNone((view['active']+view['history'])[0]['watch_id'])
        self.company.perform(self.sid,request,'authorized-cloud',True,approval_source=authority)
        self.assertEqual(self.sdk_calls.count('submit'),1)

    def test_changed_component_or_gpu_scope_stops_before_submit(self):
        prepared=self.prepared();self.grant();self.config_digest='b'*64
        with self.assertRaisesRegex(ValueError,'组件已变化'):self.company.training.submit(self.sid,prepared['submission_id'],True)
        self.assertNotIn('submit',self.sdk_calls)
        self.config_digest='a'*64
        self.store.execute('UPDATE research_cloud_scopes SET max_gpus=8')
        with self.assertRaisesRegex(ValueError,'GPU'):self.company.training.submit(self.sid,prepared['submission_id'],True)

    def test_running_original_rejects_repair_preparation(self):
        prepared=self.prepared();self.phase='running'
        self.store.execute('DELETE FROM company_training')
        with self.assertRaisesRegex(ValueError,'原任务未结束'):self.prepared()
        self.assertNotIn('submit',self.sdk_calls)

    def test_terminal_collects_evidence_notices_and_wakes_once(self):
        self.phase='pending';self.company.watch(self.sid,'original-task',True)
        sent=[];kernel=types.SimpleNamespace(settings=types.SimpleNamespace(value={'channel':'ept'}),send=lambda *a,**kw:sent.append((a,kw)))
        self.phase='failed';self.company.poll_watches(kernel,clock=10**12);self.company.poll_watches(kernel,clock=10**12+1000)
        self.assertEqual(len(sent),1);self.assertIn('ModuleNotFoundError',sent[0][0][1]);self.assertEqual(sent[0][1]['origin'],'background')
        self.assertEqual(len(self.company.context(self.sid)['training']['notices']),1)
        self.assertNotIn('submit',self.sdk_calls)

    def test_pending_watch_prevents_duplicate_cloud_and_pause_blocks_submit(self):
        prepared=self.prepared();self.grant();self.phase='pending';self.company.watch(self.sid,'existing-running-task',False)
        with self.assertRaisesRegex(ValueError,'在途'):self.company.training.submit(self.sid,prepared['submission_id'],True)
        self.research.pause(self.sid)
        with self.assertRaisesRegex(ValueError,'暂停'):self.company.training.submit(self.sid,prepared['submission_id'],True)

    def test_cloud_round_cannot_close_unknown_then_keeps_exact_failed_run(self):
        prepared=self.prepared();self.grant();self.sdk_unknown=True
        self.company.training.submit(self.sid,prepared['submission_id'],True)
        review={'kind':'review','title':'repair round review','content':'Failure remains evidence; next repair needs review.',
                'status':'recorded','evidence':[],'plan_id':self.plan['id'],'cloud_run_ids':[prepared['submission_id']]}
        with self.assertRaisesRegex(ValueError,'在途/未知云任务'):self.store.add_record(self.sid,review,{})
        self.store.execute("UPDATE company_training SET state='failed' WHERE id=?",(prepared['submission_id'],))
        saved=self.store.add_record(self.sid,review,{})
        self.assertEqual(json.loads(saved['source'])['cloud_run_ids'],[prepared['submission_id']])
        other=self.store.create_project('unrelated');other_sid=self.store.create_session(other['id'],'ept')['id']
        with self.assertRaises(ValueError):self.store.add_record(other_sid,{'kind':'result','title':'cross-project','content':'no','evidence':[],'cloud_run_ids':[prepared['submission_id']]},{})

    def test_reconstructed_adapter_preserves_original_unknown_attempt(self):
        prepared=self.prepared();self.grant();self.sdk_unknown=True
        self.company.training.submit(self.sid,prepared['submission_id'],True)
        reconstructed=Company(self.store,runner=self.company.runner,locator=lambda name:name)
        self.addCleanup(reconstructed.close)
        reconstructed.training.sdk=lambda *args,**kwargs:self.fail('Unknown must never call SDK again')
        self.assertEqual(reconstructed.training.submit(self.sid,prepared['submission_id'],True)['state'],'unknown')
        self.assertEqual(self.sdk_calls.count('submit'),1)
        self.assertEqual(self.research.status(self.sid)['attempts']['runs'],1)
    def test_actual_request_identity_is_committed_before_first_sdk_write(self):
        prepared=self.prepared();id=prepared['submission_id'];self.grant()
        self.assertIsNone(self.identity_row(id)['requested_at'])
        original_sdk=self.company.training.sdk
        requested='2026-10-30T03:00:00+00:00'
        self.store.execute('UPDATE company_training SET created_at=?,updated_at=? WHERE id=?',
            ('2026-10-30T01:00:00+00:00','2026-10-30T02:00:00+00:00',id))
        def sdk(operation,**kwargs):
            if operation=='submit':
                reader=Store(self.root)
                try:
                    row=reader.one('SELECT * FROM company_training WHERE id=?',(id,))
                    identity=reader.one('SELECT * FROM company_training_identity WHERE submission_id=?',(id,))
                    self.assertEqual(row['state'],'requested')
                    self.assertEqual(identity['requested_at'],requested)
                    self.assertEqual(identity['namespace'],'vc-chassis')
                    self.assertEqual(identity['profile'],'fixture-profile')
                    self.assertEqual(identity['read_channel'],'lpai-agent')
                    self.assertEqual(json.loads(identity['submission_authority'])['kind'],'human_confirmation')
                    self.assertEqual(kwargs['namespace'],identity['namespace'])
                    self.assertEqual(kwargs['task_information'],prepared['task_information'])
                    self.assertIn('['+identity['submission_marker']+']',kwargs['task_information']['expected_duration_desc'])
                    self.assertIsNone(identity['receipt'])
                finally:reader.close()
            return original_sdk(operation,**kwargs)
        self.company.training.sdk=sdk
        with patch('lab.lpai_training.now',return_value=requested):
            result=self.company.training.submit(self.sid,id,True)
        self.assertEqual(result['state'],'submitted');self.assertEqual(result['item']['uid'],'new-task')
        columns=[row['name'] for row in self.store.rows('PRAGMA table_info(company_training)')]
        self.assertEqual(columns,['id','session_id','project_id','plan_id','trial_id','component_uid','task_uid','request','state','result','created_at','updated_at'])
        self.assertEqual(self.identity_row(id)['original_task_uid'],'new-task')
        self.assertEqual(json.loads(self.identity_row(id)['receipt'])['item']['uid'],'new-task')
        self.company.training.submit(self.sid,id,True)
        self.assertEqual(self.sdk_calls.count('submit'),1)
        self.assertEqual(self.identity_row(id)['requested_at'],requested)

    def test_task_created_after_prepare_but_before_actual_request_is_rejected(self):
        prepared=self.prepared();id=prepared['submission_id'];self.grant();self.sdk_unknown=True
        self.store.execute('UPDATE company_training SET created_at=?,updated_at=? WHERE id=?',
            ('2026-10-30T01:00:00+00:00','2026-10-30T01:00:00+00:00',id))
        with patch('lab.lpai_training.now',return_value='2026-10-30T03:00:00+00:00'):
            self.company.training.submit(self.sid,id,True)
        self.task_overrides['create_timestamp']={'time':'2026-10-30T02:00:00Z'}
        with self.assertRaisesRegex(ValueError,'实际原请求'):
            self.company.training.reconcile(self.sid,id,'same-name-old-task')
        self.assert_unknown_original(id)

    def test_offset_equivalent_time_reconciles_original_request_echo(self):
        id=self.unknown_submission()
        self.store.execute('UPDATE company_training_identity SET requested_at=? WHERE submission_id=?',
            ('2026-10-30T03:00:00+08:00',id))
        self.task_overrides['create_timestamp']={'time':'2026-10-29T19:00:00Z'}
        result=self.company.training.reconcile(self.sid,id,'original-returned-task')
        self.assertEqual(result['state'],'submitted');self.assertTrue(result['identity_reconciled'])
        self.assertEqual(result['origin_evidence'],'original_request_echo')
        self.assertEqual(self.training_row(id)['task_uid'],'original-returned-task')
        self.assertNotIn('identity_unique',result)

    def test_missing_naive_and_unsupported_dates_keep_unknown(self):
        id=self.unknown_submission()
        for value in (None,{},'2026-10-30T20:00:00','not-a-date',0,{'seconds':1793329200},'0001-01-01T00:00:00Z'):
            with self.subTest(value=value):
                self.task_overrides['create_timestamp']=value
                result=self.company.training.reconcile(self.sid,id,'unconfirmed-time-task')
                self.assertEqual(result['state'],'unknown');self.assertFalse(result['identity_reconciled'])
                self.assertEqual(result['reason'],'unconfirmed_creation_time')
                self.assert_unknown_original(id)

    def test_exact_timestamp_and_name_do_not_identify_submission_without_marker(self):
        id=self.unknown_submission();requested=self.identity_row(id)['requested_at']
        self.task_overrides['create_timestamp']={'time':requested}
        for name in ('retry-fixture','retry-fixture-colliding-postfix'):
            for echo in (None,'fixed bounded configuration','wrong [iterlab-submission:not-original]'):
                with self.subTest(name=name,echo=echo):
                    self.task_overrides.update(name=name,expected_duration_desc=echo)
                    result=self.company.training.reconcile(self.sid,id,'same-name-other-origin')
                    self.assertEqual(result['state'],'unknown')
                    self.assertEqual(result['reason'],'original_submission_marker_not_echoed')
                    self.assert_unknown_original(id)

    def test_uid_namespace_component_and_name_contradictions_reject(self):
        id=self.unknown_submission()
        for field,value in (('uid','other-returned-uid'),('namespace','other-namespace'),('project_uid','component-2'),('name','different-task')):
            with self.subTest(field=field):
                self.task_overrides={field:value}
                with self.assertRaises(ValueError):self.company.training.reconcile(self.sid,id,'candidate-task')
                self.assert_unknown_original(id)
        for field in ('namespace','project_uid','name'):
            with self.subTest(missing=field):
                self.task_overrides={field:None}
                result=self.company.training.reconcile(self.sid,id,'candidate-task')
                self.assertEqual(result['state'],'unknown');self.assert_unknown_original(id)

    def test_known_uid_cannot_switch_and_needs_no_guessed_origin(self):
        id=self.unknown_submission()
        self.store.execute('UPDATE company_training SET task_uid=? WHERE id=?',('locked-original',id))
        self.task_overrides['expected_duration_desc']=None
        before=len(self.calls)
        with self.assertRaisesRegex(ValueError,'UID已锁定'):self.company.training.reconcile(self.sid,id,'different-task')
        self.assertEqual(len(self.calls),before);self.assert_unknown_original(id,'locked-original')
        result=self.company.training.reconcile(self.sid,id,'locked-original')
        self.assertEqual(result['state'],'submitted');self.assertEqual(result['origin_evidence'],'original_uid_receipt')
        before=len(self.calls)
        self.assertEqual(self.company.training.reconcile(self.sid,id,'locked-original'),result)
        self.assertEqual(len(self.calls),before)
        with self.assertRaisesRegex(ValueError,'UID已锁定'):self.company.training.reconcile(self.sid,id,'different-task')

    def test_known_original_receipt_uid_cannot_be_replaced_when_task_column_is_empty(self):
        id=self.unknown_submission()
        receipt={'state':'submitted','item':{'uid':'receipt-original'}}
        self.store.execute('UPDATE company_training SET result=? WHERE id=?',(json.dumps(receipt),id))
        before=len(self.calls)
        with self.assertRaisesRegex(ValueError,'UID已锁定'):self.company.training.reconcile(self.sid,id,'other-task')
        self.assertEqual(len(self.calls),before)
        self.task_overrides['expected_duration_desc']=None
        result=self.company.training.reconcile(self.sid,id,'receipt-original')
        self.assertEqual(result['origin_evidence'],'original_uid_receipt')
        self.assertEqual(self.training_row(id)['task_uid'],'receipt-original')

    def test_conflicting_current_and_locked_receipt_uid_is_never_overwritten(self):
        id=self.unknown_submission()
        self.store.execute('UPDATE company_training_identity SET original_task_uid=? WHERE submission_id=?',('locked-original',id))
        self.store.execute('UPDATE company_training SET task_uid=? WHERE id=?',('conflicting-current',id))
        before=len(self.calls)
        for candidate in ('locked-original','conflicting-current','third-task'):
            with self.assertRaisesRegex(ValueError,'UID已锁定'):self.company.training.reconcile(self.sid,id,candidate)
        self.assertEqual(len(self.calls),before)
        self.assertEqual(self.training_row(id)['task_uid'],'conflicting-current')
        self.assertEqual(self.identity_row(id)['original_task_uid'],'locked-original')

    def test_double_reconcile_is_idempotent_and_does_not_rearm_watch(self):
        id=self.unknown_submission()
        result=self.company.training.reconcile(self.sid,id,'original-task-uid')
        calls=len(self.calls);events=len(self.store.rows('SELECT seq FROM events'))
        self.assertEqual(self.company.training.reconcile(self.sid,id,'original-task-uid'),result)
        self.assertEqual(len(self.calls),calls);self.assertEqual(len(self.store.rows('SELECT seq FROM events')),events)
        self.assertEqual(len(self.store.rows('SELECT id FROM company_watches')),1)
        self.assertEqual(len(self.store.rows("SELECT seq FROM events WHERE kind='company.training_reconciled'")),1)
        self.assertEqual(self.sdk_calls.count('submit'),1)

    def test_concurrent_reconcile_under_company_lock_binds_once(self):
        id=self.unknown_submission();barrier=threading.Barrier(3);results=[];errors=[]
        def reconcile():
            try:
                barrier.wait(timeout=5)
                results.append(self.company.training.reconcile(self.sid,id,'one-original-task'))
            except Exception as exc:errors.append(exc)
        threads=[threading.Thread(target=reconcile) for _ in range(2)]
        for thread in threads:thread.start()
        barrier.wait(timeout=5)
        for thread in threads:thread.join(timeout=10);self.assertFalse(thread.is_alive())
        self.assertEqual(errors,[]);self.assertEqual(len(results),2);self.assertEqual(results[0],results[1])
        self.assertEqual(self.training_row(id)['task_uid'],'one-original-task')
        self.assertEqual(len(self.store.rows('SELECT id FROM company_watches')),1)
        self.assertEqual(len(self.store.rows("SELECT seq FROM events WHERE kind='company.training_reconciled'")),1)

    def test_separate_connections_cas_same_uid_returns_winner_once(self):
        id=self.unknown_submission();other_store=Store(self.root);self.addCleanup(other_store.close)
        other=Company(other_store,runner=self.company.runner,locator=lambda name:name);self.addCleanup(other.close)
        barrier=threading.Barrier(2);results=[];errors=[];watches=[]
        def reader(company):
            run=company._run
            def read(args,**kwargs):
                value=run(args,**kwargs);barrier.wait(timeout=5);return value
            return read
        self.company._run=reader(self.company);other._run=reader(other)
        self.company.watch=lambda *args,**kwargs:watches.append(args)
        other.watch=lambda *args,**kwargs:watches.append(args)
        def reconcile(company):
            try:results.append(company.training.reconcile(self.sid,id,'cas-original'))
            except Exception as exc:errors.append(exc)
        threads=[threading.Thread(target=reconcile,args=(company,)) for company in (self.company,other)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(timeout=10);self.assertFalse(thread.is_alive())
        self.assertEqual(errors,[]);self.assertEqual(len(results),2);self.assertEqual(results[0],results[1])
        self.assertEqual(len(watches),1)
        self.assertEqual(self.identity_row(id)['original_task_uid'],'cas-original')
        self.assertEqual(len(self.store.rows("SELECT seq FROM events WHERE kind='company.training_reconciled'")),1)

    def test_separate_connections_cas_conflicting_candidates_cannot_switch_uid(self):
        id=self.unknown_submission();other_store=Store(self.root);self.addCleanup(other_store.close)
        other=Company(other_store,runner=self.company.runner,locator=lambda name:name);self.addCleanup(other.close)
        barrier=threading.Barrier(2);results=[];errors=[];watches=[]
        def reader(company):
            run=company._run
            def read(args,**kwargs):
                value=run(args,**kwargs);barrier.wait(timeout=5);return value
            return read
        self.company._run=reader(self.company);other._run=reader(other)
        self.company.watch=lambda *args,**kwargs:watches.append(args)
        other.watch=lambda *args,**kwargs:watches.append(args)
        def reconcile(company,candidate):
            try:results.append(company.training.reconcile(self.sid,id,candidate))
            except Exception as exc:errors.append(exc)
        threads=[threading.Thread(target=reconcile,args=(self.company,'candidate-a')),
                 threading.Thread(target=reconcile,args=(other,'candidate-b'))]
        for thread in threads:thread.start()
        for thread in threads:thread.join(timeout=10);self.assertFalse(thread.is_alive())
        self.assertEqual(len(results),1);self.assertEqual(len(errors),1);self.assertIsInstance(errors[0],ValueError)
        winner=results[0]['item']['uid']
        self.assertEqual(self.training_row(id)['task_uid'],winner)
        self.assertEqual(self.identity_row(id)['original_task_uid'],winner);self.assertEqual(len(watches),1)

    def test_changed_state_during_read_is_not_overwritten(self):
        id=self.unknown_submission();run=self.company._run
        def read(args,**kwargs):
            response=run(args,**kwargs)
            self.store.execute("UPDATE company_training SET state='failed',task_uid='externally-locked' WHERE id=?",(id,))
            return response
        self.company._run=read
        with self.assertRaisesRegex(ValueError,'并发核对'):self.company.training.reconcile(self.sid,id,'candidate-task')
        self.assertEqual(self.training_row(id)['state'],'failed')
        self.assertEqual(self.training_row(id)['task_uid'],'externally-locked')
        self.assertIsNone(self.identity_row(id)['receipt'])

    def test_late_binding_change_after_read_does_not_bind_candidate(self):
        id=self.unknown_submission();run=self.company._run
        def read(args,**kwargs):
            response=run(args,**kwargs)
            self.store.execute('UPDATE company_config SET revision=revision+1')
            return response
        self.company._run=read
        with self.assertRaisesRegex(ValueError,'authority'):self.company.training.reconcile(self.sid,id,'candidate-task')
        self.assert_unknown_original(id);self.assertIsNone(self.identity_row(id)['receipt'])

    def test_late_receipt_uid_during_read_is_not_overwritten(self):
        id=self.unknown_submission();run=self.company._run
        def read(args,**kwargs):
            response=run(args,**kwargs)
            receipt={'state':'submitted','item':{'uid':'late-original'}}
            self.store.execute('UPDATE company_training SET result=? WHERE id=?',(json.dumps(receipt),id))
            return response
        self.company._run=read
        with self.assertRaisesRegex(ValueError,'并发核对'):self.company.training.reconcile(self.sid,id,'candidate-task')
        self.assertEqual(json.loads(self.training_row(id)['result'])['item']['uid'],'late-original')
        self.assertIsNone(self.identity_row(id)['receipt']);self.assert_unknown_original(id)

    def test_original_namespace_profile_read_channel_and_connection_authority_cannot_mix(self):
        id=self.unknown_submission();identity=self.identity_row(id);config=self.company.config()
        def check_blocked():
            count=len(self.calls)
            with self.assertRaises(ValueError):self.company.training.reconcile(self.sid,id,'candidate-task')
            self.assertEqual(len(self.calls),count);self.assert_unknown_original(id)
        content={key:value for key,value in config.items() if key!='revision'}
        self.store.execute('UPDATE company_config SET content=?',(json.dumps({**content,'namespace':'other-namespace'}),))
        check_blocked();self.store.execute('UPDATE company_config SET content=?',(json.dumps(content),))
        (self.root/'settings.json').write_text(json.dumps({'channel':'ept','profile':'different-profile'}),encoding='utf-8')
        check_blocked();(self.root/'settings.json').write_text(json.dumps({'channel':'ept','profile':'fixture-profile'}),encoding='utf-8')
        self.store.execute('UPDATE company_training_identity SET read_channel=? WHERE submission_id=?',('sdk-fallback',id))
        check_blocked();self.store.execute('UPDATE company_training_identity SET read_channel=? WHERE submission_id=?',(identity['read_channel'],id))
        authority=json.loads(identity['connection_authority']);authority['bindings_hash']='different-binding'
        self.store.execute('UPDATE company_training_identity SET connection_authority=? WHERE submission_id=?',(json.dumps(authority),id))
        check_blocked();self.store.execute('UPDATE company_training_identity SET connection_authority=? WHERE submission_id=?',(identity['connection_authority'],id))
        self.store.execute("UPDATE sessions SET channel='codex' WHERE id=?",(self.sid,))
        check_blocked()

    def test_profile_or_namespace_change_blocks_first_sdk_write(self):
        prepared=self.prepared();id=prepared['submission_id'];self.grant()
        (self.root/'settings.json').write_text(json.dumps({'channel':'ept','profile':'another-profile'}),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'profile'):self.company.training.submit(self.sid,id,True)
        self.assertIsNone(self.identity_row(id)['requested_at']);self.assertNotIn('submit',self.sdk_calls)
        (self.root/'settings.json').write_text(json.dumps({'channel':'ept','profile':'fixture-profile'}),encoding='utf-8')
        config=self.company.config();content={key:value for key,value in config.items() if key!='revision'}
        self.store.execute('UPDATE company_config SET content=?',(json.dumps({**content,'namespace':'other-namespace'}),))
        with self.assertRaisesRegex(ValueError,'namespace'):self.company.training.submit(self.sid,id,True)
        self.assertIsNone(self.identity_row(id)['requested_at']);self.assertNotIn('submit',self.sdk_calls)

    def test_program_authority_preserved_after_policy_mode_change_without_new_grant(self):
        prepared=self.prepared();id=prepared['submission_id'];self.grant()
        scope={'objective':'offline original program authority','evaluation':'fixed','data_boundary':'fixture','allowed_changes':'fixed component','frozen_files':[]}
        self.research.autonomy.enable(self.sid,self.plan['id'],plan_digest(self.plan),scope,'fixture explicit original delegation')
        authority=self.company.training.delegated_submission(self.sid,id);self.sdk_unknown=True
        self.company.perform(self.sid,{'action':'lpai_submit','submission_id':id},'program-original-operation',True,approval_source=authority)
        original=json.loads(self.identity_row(id)['submission_authority'])
        self.assertEqual(original['kind'],'delegated_research_scope');self.assertEqual(original['authority_id'],authority['authority_id'])
        self.store.execute("UPDATE research_autonomy_policies SET mode='manual' WHERE project_id=?",(self.project['id'],))
        before_grants=self.store.rows('SELECT * FROM research_grants')
        result=self.company.training.reconcile(self.sid,id,'program-original-task')
        self.assertEqual(result['state'],'submitted');self.assertEqual(result['approval_source'],original)
        self.assertFalse(result['new_authorization']);self.assertEqual(self.store.rows('SELECT * FROM research_grants'),before_grants)
        self.assertEqual(json.loads(self.identity_row(id)['submission_authority']),original)
        self.assertEqual(self.sdk_calls.count('submit'),1)

    def test_human_original_authority_does_not_become_program_after_mode_change(self):
        prepared=self.prepared();id=prepared['submission_id'];self.grant()
        scope={'objective':'offline human original authority','evaluation':'fixed','data_boundary':'fixture','allowed_changes':'fixed component','frozen_files':[]}
        self.research.autonomy.enable(self.sid,self.plan['id'],plan_digest(self.plan),scope,'fixture explicit delegation')
        self.store.execute("UPDATE research_autonomy_policies SET mode='manual' WHERE project_id=?",(self.project['id'],))
        self.sdk_unknown=True;self.company.training.submit(self.sid,id,True)
        self.assertEqual(json.loads(self.identity_row(id)['submission_authority'])['kind'],'human_confirmation')
        self.store.execute("UPDATE research_autonomy_policies SET mode='autonomous' WHERE project_id=?",(self.project['id'],))
        result=self.company.training.reconcile(self.sid,id,'human-original')
        self.assertEqual(result['approval_source']['kind'],'human_confirmation')
        self.assertFalse(result['new_authorization']);self.assertEqual(self.sdk_calls.count('submit'),1)

    def test_original_program_authority_mismatch_blocks_readback(self):
        prepared=self.prepared();id=prepared['submission_id'];self.grant()
        scope={'objective':'offline authority mismatch','evaluation':'fixed','data_boundary':'fixture','allowed_changes':'fixed component','frozen_files':[]}
        self.research.autonomy.enable(self.sid,self.plan['id'],plan_digest(self.plan),scope,'fixture explicit original delegation')
        authority=self.company.training.delegated_submission(self.sid,id);self.sdk_unknown=True
        self.company.perform(self.sid,{'action':'lpai_submit','submission_id':id},'program-mismatch-operation',True,approval_source=authority)
        self.store.execute('UPDATE research_autonomy_policies SET owner_session_id=? WHERE project_id=?',('another-session',self.project['id']))
        before=len(self.calls)
        with self.assertRaisesRegex(ValueError,'authority'):self.company.training.reconcile(self.sid,id,'candidate-task')
        self.assertEqual(len(self.calls),before);self.assert_unknown_original(id)
        self.store.execute('UPDATE research_autonomy_policies SET owner_session_id=? WHERE project_id=?',(self.sid,self.project['id']))
        self.store.execute('UPDATE research_authorities SET scope_digest=? WHERE id=?',('different-original-scope',authority['authority_id']))
        with self.assertRaisesRegex(ValueError,'authority'):self.company.training.reconcile(self.sid,id,'candidate-task')
        self.assert_unknown_original(id)

    def test_optional_platform_identity_missing_or_conflicting_keeps_origin_safe(self):
        id=self.unknown_submission();original={'platform':'lpai','account_uid':'fixture-account'}
        self.store.execute('UPDATE company_training_identity SET platform_identity=? WHERE submission_id=?',(json.dumps(original),id))
        result=self.company.training.reconcile(self.sid,id,'candidate-task')
        self.assertEqual(result['reason'],'missing_platform_identity');self.assert_unknown_original(id)
        self.task_overrides['platform_identity']={'platform':'lpai','account_uid':'different-account'}
        with self.assertRaisesRegex(ValueError,'账号身份'):self.company.training.reconcile(self.sid,id,'candidate-task')
        self.assert_unknown_original(id)
        self.task_overrides['platform_identity']=original
        result=self.company.training.reconcile(self.sid,id,'candidate-task')
        self.assertEqual(result['state'],'submitted')

    def test_legacy_without_actual_request_time_never_infers_submitted_from_prepare_or_update(self):
        id=self.unknown_submission()
        self.store.execute('DELETE FROM company_training_identity WHERE submission_id=?',(id,))
        self.store.execute('UPDATE company_training SET created_at=?,updated_at=? WHERE id=?',
            ('2026-01-01T00:00:00+00:00','2026-01-02T00:00:00+00:00',id))
        for known in (None,'legacy-immutable-task'):
            self.store.execute('UPDATE company_training SET task_uid=? WHERE id=?',(known,id))
            result=self.company.training.reconcile(self.sid,id,known or 'same-name-guessed-task')
            self.assertEqual(result['state'],'unknown');self.assertFalse(result['identity_reconciled'])
            self.assertEqual(result['reason'],'missing_original_requested_at')
            if known:self.assertEqual(result['original_task_uid'],known)
            self.assert_unknown_original(id,known)
        with self.assertRaisesRegex(ValueError,'UID已锁定'):self.company.training.reconcile(self.sid,id,'different-task')

    def test_companion_missing_actual_timestamp_observes_known_uid_without_binding_success(self):
        id=self.unknown_submission();self.store.execute('UPDATE company_training SET task_uid=? WHERE id=?',('known-original',id))
        self.store.execute('UPDATE company_training_identity SET requested_at=NULL WHERE submission_id=?',(id,))
        result=self.company.training.reconcile(self.sid,id,'known-original')
        self.assertEqual(result['state'],'unknown');self.assertEqual(result['reason'],'missing_original_requested_at')
        self.assert_unknown_original(id,'known-original');self.assertIsNone(self.identity_row(id)['receipt'])

    def test_reconcile_only_requested_unknown_lifecycle_and_original_receipt_repeats(self):
        id=self.unknown_submission()
        for state in ('prepared','pending_approval','submitted','running','failed','succeeded'):
            with self.subTest(state=state):
                self.store.execute('UPDATE company_training SET state=? WHERE id=?',(state,id))
                before=len(self.calls)
                with self.assertRaisesRegex(ValueError,'生命周期'):self.company.training.reconcile(self.sid,id,'candidate-task')
                self.assertEqual(len(self.calls),before)
        self.store.execute("UPDATE company_training SET state='requested' WHERE id=?",(id,))
        result=self.company.training.reconcile(self.sid,id,'candidate-task')
        self.assertEqual(result['state'],'submitted')
        self.store.execute("UPDATE company_training SET state='succeeded' WHERE id=?",(id,))
        before=len(self.calls);again=self.company.training.reconcile(self.sid,id,'candidate-task')
        self.assertEqual(again,result);self.assertEqual(self.training_row(id)['state'],'succeeded');self.assertEqual(len(self.calls),before)

    def test_reconcile_watch_failure_returns_persisted_success_and_original_uid(self):
        id=self.unknown_submission();observed=[]
        def watch(sid,task_uid,auto_resume,**kwargs):
            observed.append((self.training_row(id),self.identity_row(id),task_uid,kwargs))
            raise RuntimeError('offline monitor failure')
        with patch.object(self.company,'watch',side_effect=watch) as attachment:
            result=self.company.training.reconcile(self.sid,id,'persisted-original')
            again=self.company.training.reconcile(self.sid,id,'persisted-original')
            self.assertEqual(attachment.call_count,1)
        row,identity,task_uid,kwargs=observed[0]
        self.assertEqual(row['state'],'submitted');self.assertEqual(row['task_uid'],task_uid)
        self.assertEqual(json.loads(identity['receipt'])['item']['uid'],task_uid)
        self.assertIsNotNone(identity['receipt_at']);self.assertTrue(kwargs['analyze_terminal'])
        self.assertEqual(result['state'],'submitted');self.assertEqual(result['item']['uid'],'persisted-original')
        self.assertTrue(result['monitor_attach_failed']);self.assertEqual(again,result)
        row=self.training_row(id);self.assertEqual(row['state'],'submitted');self.assertEqual(row['task_uid'],'persisted-original')
        events=self.store.rows("SELECT payload FROM events WHERE kind='company.monitor_attach_failed'")
        self.assertEqual(len(events),1);self.assertEqual(json.loads(events[0]['payload'])['task_uid'],'persisted-original')
        self.assertEqual(self.store.one("SELECT kind FROM company_notices WHERE task_uid='persisted-original'")['kind'],'monitor_attach_failed')
        self.company.training.submit(self.sid,id,True);self.assertEqual(self.sdk_calls.count('submit'),1)

    def test_submit_watch_failure_preserves_confirmed_receipt_before_attach(self):
        prepared=self.prepared();id=prepared['submission_id'];self.grant();observed=[]
        def watch(sid,task_uid,auto_resume,**kwargs):
            reader=Store(self.root)
            try:
                row=reader.one('SELECT * FROM company_training WHERE id=?',(id,))
                identity=reader.one('SELECT * FROM company_training_identity WHERE submission_id=?',(id,))
                observed.append((row,identity,task_uid))
            finally:reader.close()
            raise RuntimeError('offline monitor failure')
        with patch.object(self.company,'watch',side_effect=watch):result=self.company.training.submit(self.sid,id,True)
        self.assertEqual(len(observed),1)
        row,identity,task_uid=observed[0]
        self.assertEqual(row['state'],'submitted');self.assertEqual(row['task_uid'],task_uid)
        self.assertEqual(json.loads(identity['receipt'])['item']['uid'],task_uid)
        self.assertEqual(result['state'],'submitted');self.assertEqual(result['item']['uid'],'new-task');self.assertTrue(result['monitor_attach_failed'])
        self.assertEqual(self.training_row(id)['state'],'submitted')
        self.assertEqual(self.company.training.submit(self.sid,id,True),result);self.assertEqual(self.sdk_calls.count('submit'),1)
        self.assertEqual(self.company.training.reconcile(self.sid,id,'new-task'),result)
        self.assertEqual(len(self.store.rows("SELECT seq FROM events WHERE kind='company.monitor_attach_failed'")),1)

    def test_platform_identity_is_frozen_and_revalidated_before_first_sdk_write(self):
        original={'platform':'lpai','account_uid':'fixture-sdk-account'}
        current=dict(original);sdk=self.company.training.sdk
        def identified_sdk(operation,**kwargs):
            result=sdk(operation,**kwargs)
            return {**result,'platform_identity':dict(current)} if operation=='component' else result
        self.company.training.sdk=identified_sdk
        prepared=self.prepared();id=prepared['submission_id'];self.grant()
        self.assertEqual(json.loads(self.identity_row(id)['platform_identity']),original)
        current['account_uid']='different-account'
        with self.assertRaisesRegex(ValueError,'平台身份'):self.company.training.submit(self.sid,id,True)
        self.assertNotIn('submit',self.sdk_calls);self.assertIsNone(self.identity_row(id)['requested_at'])
        current.update(original);self.sdk_unknown=True
        self.company.training.submit(self.sid,id,True)
        self.task_overrides['platform_identity']=original
        result=self.company.training.reconcile(self.sid,id,'identified-original')
        self.assertEqual(result['state'],'submitted');self.assertEqual(self.sdk_calls.count('submit'),1)

    def test_persisted_watch_failure_receipt_survives_reconstruction_without_reads_or_writes(self):
        id=self.unknown_submission()
        with patch.object(self.company,'watch',side_effect=RuntimeError('offline monitor failure')):
            result=self.company.training.reconcile(self.sid,id,'durable-original')
        other_store=Store(self.root);self.addCleanup(other_store.close)
        reconstructed=Company(other_store,runner=lambda *args,**kwargs:self.fail('cached receipt must not call transport'),locator=lambda name:name)
        self.addCleanup(reconstructed.close)
        reconstructed.training.sdk=lambda *args,**kwargs:self.fail('cached receipt must not call SDK')
        reconstructed.watch=lambda *args,**kwargs:self.fail('idempotent receipt must not rearm monitor')
        self.assertEqual(reconstructed.training.reconcile(self.sid,id,'durable-original'),result)
        self.assertEqual(reconstructed.training.submit(self.sid,id,True),result)
        self.assertEqual(self.sdk_calls.count('submit'),1)

    def test_missing_original_submission_authority_is_not_recreated(self):
        id=self.unknown_submission()
        self.store.execute('UPDATE company_training_identity SET submission_authority=NULL WHERE submission_id=?',(id,))
        result=self.company.training.reconcile(self.sid,id,'candidate-task')
        self.assertEqual(result['state'],'unknown');self.assertEqual(result['reason'],'missing_original_submission_authority')
        self.assert_unknown_original(id)
        self.assertIsNone(self.identity_row(id)['submission_authority'])

    def test_human_authority_mismatch_does_not_bind_other_session(self):
        id=self.unknown_submission();identity=self.identity_row(id)
        source=json.loads(identity['submission_authority']);source['session_id']='not-original-session'
        self.store.execute('UPDATE company_training_identity SET submission_authority=? WHERE submission_id=?',(json.dumps(source),id))
        before=len(self.calls)
        with self.assertRaisesRegex(ValueError,'authority'):self.company.training.reconcile(self.sid,id,'candidate-task')
        self.assertEqual(len(self.calls),before);self.assert_unknown_original(id)

    def test_app_model_channel_change_cannot_mix_original_profile(self):
        id=self.unknown_submission()
        (self.root/'settings.json').write_text(json.dumps({'channel':'codex','profile':'fixture-profile'}),encoding='utf-8')
        before=len(self.calls)
        with self.assertRaisesRegex(ValueError,'模型通道'):self.company.training.reconcile(self.sid,id,'candidate-task')
        self.assertEqual(len(self.calls),before);self.assert_unknown_original(id)

    def test_prepared_state_cannot_replay_an_already_locked_request(self):
        id=self.unknown_submission()
        self.store.execute("UPDATE company_training SET state='prepared' WHERE id=?",(id,))
        with self.assertRaisesRegex(ValueError,'已经锁定'):self.company.training.submit(self.sid,id,True)
        self.assertEqual(self.sdk_calls.count('submit'),1)
        self.assertIsNotNone(self.identity_row(id)['requested_at'])

    def test_20261030_fast_confirmed_submit_analyzes_once_and_old_terminal_does_not(self):
        prepared=self.prepared();id=prepared['submission_id'];self.grant();self.phase='Succeeded';sent=[]
        kernel=types.SimpleNamespace(settings=types.SimpleNamespace(value={'channel':'ept'}),send=lambda *args,**kwargs:sent.append((args,kwargs)))
        result=self.company.training.submit(self.sid,id,True)
        self.assertEqual(result['state'],'submitted')
        self.company.poll_watches(kernel,clock=10**12)
        self.company.training.submit(self.sid,id,True)
        self.company.training.reconcile(self.sid,id,'new-task')
        self.company.poll_watches(kernel,clock=10**12+1000)
        self.assertEqual(len(sent),1);self.assertEqual(self.sdk_calls.count('submit'),1)
        other_sid=self.store.create_session(self.project['id'],'ept')['id']
        self.company.watch(other_sid,'old-terminal',True)
        self.company.poll_watches(kernel,clock=10**12+2000)
        self.assertEqual(len(sent),1)
        self.assertEqual(self.store.one('SELECT wake_state FROM company_watches WHERE session_id=?',(other_sid,))['wake_state'],'already_terminal')

    def test_20261030_fast_terminal_original_submission_analyzes_once(self):
        id=self.unknown_submission();self.phase='Succeeded';sent=[]
        kernel=types.SimpleNamespace(settings=types.SimpleNamespace(value={'channel':'ept'}),send=lambda *args,**kwargs:sent.append((args,kwargs)))
        self.company.training.reconcile(self.sid,id,'fast-terminal-original')
        watch=self.store.one('SELECT * FROM company_watches WHERE task_uid=?',('fast-terminal-original',))
        self.assertEqual(watch['auto_resume'],1);self.assertEqual(watch['wake_state'],'none')
        self.company.poll_watches(kernel,clock=10**12)
        self.company.training.reconcile(self.sid,id,'fast-terminal-original')
        self.company.poll_watches(kernel,clock=10**12+1000)
        self.assertEqual(len(sent),1);self.assertEqual(sent[0][1]['origin'],'background')
        self.assertEqual(self.training_row(id)['state'],'Succeeded')
        self.assertEqual(self.sdk_calls.count('submit'),1)


if __name__=='__main__':unittest.main()
