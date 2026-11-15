"""Cloud prerequisite checks at the write boundary; all SDK/process calls are fixtures."""
import json
import unittest
from unittest.mock import patch
from tests import test_lpai_recovery as fixtures
from lab.store import dumps,now


class CloudExecutionIntegrityTests(unittest.TestCase):
    setUp=fixtures.RecoveryTests.setUp
    prepared=fixtures.RecoveryTests.prepared
    grant=fixtures.RecoveryTests.grant
    training_row=fixtures.RecoveryTests.training_row
    identity_row=fixtures.RecoveryTests.identity_row

    def dependency(self,state=None):
        record=self.store.add_record(self.sid,{'kind':'trial','title':'前置实验','content':'必须有实际完成且校验的结果',
            'plan_id':self.plan['id'],'evidence':[]},{})
        source=json.loads(self.trial['source']);source['depends_on_trial_ids']=[record['id']]
        self.store.execute('UPDATE records SET source=? WHERE id=?',(dumps(source),self.trial['id']))
        if state is not None:
            self.store.execute('INSERT INTO company_training VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                ('dependency-cloud',self.sid,self.project['id'],self.plan['id'],record['id'],'component-1',
                 'dependency-task','{}',state,None,now(),now()))
        return record

    def assert_not_submitted(self,id):
        self.assertEqual(self.training_row(id)['state'],'prepared')
        self.assertIsNone(self.identity_row(id)['requested_at'])
        self.assertNotIn('submit',self.sdk_calls)
        self.assertIsNone(self.training_row(id)['task_uid'])

    def test_unexecuted_dependency_blocks_before_actual_cloud_request(self):
        self.dependency();prepared=self.prepared();self.grant()
        with self.assertRaisesRegex(ValueError,'前置实验'):
            self.company.training.submit(self.sid,prepared['submission_id'],True)
        self.assert_not_submitted(prepared['submission_id'])

    def test_platform_success_without_result_archive_is_not_dependency_success(self):
        self.dependency('Succeeded');prepared=self.prepared();self.grant()
        with self.assertRaisesRegex(ValueError,'前置实验'):
            self.company.training.submit(self.sid,prepared['submission_id'],True)
        self.assert_not_submitted(prepared['submission_id'])
        self.assertEqual(self.training_row('dependency-cloud')['state'],'Succeeded')

    def test_unknown_dependency_never_replaced_or_counted_as_completed(self):
        self.dependency('unknown');prepared=self.prepared();self.grant()
        with self.assertRaisesRegex(ValueError,'前置实验'):
            self.company.training.submit(self.sid,prepared['submission_id'],True)
        self.assert_not_submitted(prepared['submission_id'])
        self.assertEqual(self.training_row('dependency-cloud')['task_uid'],'dependency-task')

    def test_no_dependency_keeps_existing_exact_submission_and_receipt(self):
        prepared=self.prepared();self.grant()
        result=self.company.training.submit(self.sid,prepared['submission_id'],True)
        self.assertEqual(result['state'],'submitted')
        self.assertEqual(result['verified_dependencies'],[])
        receipt=json.loads(self.identity_row(prepared['submission_id'])['receipt'])
        self.assertEqual(receipt['verified_dependencies'],[])
        self.company.training.submit(self.sid,prepared['submission_id'],True)
        self.assertEqual(self.sdk_calls.count('submit'),1)

    def test_completed_original_wrapped_submit_is_not_an_unresolved_new_operation(self):
        prepared=self.prepared();self.grant()
        request={'action':'lpai_submit','submission_id':prepared['submission_id']}
        result=self.company.perform(self.sid,request,'original-wrapped-submit',True)
        self.assertEqual(result['state'],'submitted')
        self.store.execute("UPDATE company_training SET state='Succeeded' WHERE id=?",(prepared['submission_id'],))
        self.store.execute("UPDATE company_watches SET state='Succeeded' WHERE task_uid=?",(result['item']['uid'],))
        before=self.store.one('SELECT * FROM company_operations')
        self.research.autonomy.unresolved(self.project['id'])
        self.assertEqual(self.store.one('SELECT * FROM company_operations'),before)
        self.assertEqual(self.sdk_calls.count('submit'),1)

    def test_unmatched_wrapped_submission_keeps_original_operation_barrier(self):
        prepared=self.prepared();self.grant()
        result=self.company.perform(self.sid,{'action':'lpai_submit','submission_id':prepared['submission_id']},'wrapped-original',True)
        self.store.execute("UPDATE company_training SET state='Succeeded' WHERE id=?",(prepared['submission_id'],))
        self.store.execute("UPDATE company_watches SET state='Succeeded' WHERE task_uid=?",(result['item']['uid'],))
        self.store.execute("UPDATE company_operations SET result=? WHERE id='wrapped-original'",(dumps({'state':'submitted','item':{'uid':'foreign-task'}}),))
        with self.assertRaisesRegex(ValueError,'平台操作'):
            self.research.autonomy.unresolved(self.project['id'])
        self.assertEqual(self.sdk_calls.count('submit'),1)

    def test_missing_local_evidence_cannot_be_bypassed_by_cloud_submission(self):
        prepared=self.prepared();self.grant()
        gap={'run_id':'original-local','state':'partial','manifest_path':None}
        with patch.object(self.store.evidence,'blocking',return_value=gap),self.assertRaisesRegex(ValueError,'证据'):
            self.company.training.submit(self.sid,prepared['submission_id'],True)
        self.assert_not_submitted(prepared['submission_id'])

    def test_terminal_wrapper_requires_every_original_identity_proof(self):
        from lab import rounds
        prepared=self.prepared();self.grant()
        result=self.company.perform(self.sid,{'action':'lpai_submit','submission_id':prepared['submission_id']},'identity-locked',True)
        self.store.execute("UPDATE company_training SET state='Succeeded' WHERE id=?",(prepared['submission_id'],))
        self.store.execute("UPDATE company_watches SET state='Succeeded' WHERE task_uid=?",(result['item']['uid'],))
        operation=self.store.one("SELECT * FROM company_operations WHERE id='identity-locked'")
        identity=self.identity_row(prepared['submission_id'])
        run=self.training_row(prepared['submission_id'])
        cases=[
            ('unknown wrapper',"UPDATE company_operations SET state='unknown' WHERE id='identity-locked'",()),
            ('requested wrapper',"UPDATE company_operations SET state='requested' WHERE id='identity-locked'",()),
            ('pending approval',"UPDATE company_operations SET state='pending_approval' WHERE id='identity-locked'",()),
            ('wrong action',"UPDATE company_operations SET request=? WHERE id='identity-locked'",(dumps({'action':'lpai_start','submission_id':run['id']}),)),
            ('foreign submission',"UPDATE company_operations SET request=? WHERE id='identity-locked'",(dumps({'action':'lpai_submit','submission_id':'foreign-submission'}),)),
            ('invalid request',"UPDATE company_operations SET request='[]' WHERE id='identity-locked'",()),
            ('invalid result',"UPDATE company_operations SET result='[]' WHERE id='identity-locked'",()),
            ('missing requested_at',"UPDATE company_training_identity SET requested_at=NULL WHERE submission_id=?",(run['id'],)),
            ('missing receipt',"UPDATE company_training_identity SET receipt=NULL WHERE submission_id=?",(run['id'],)),
            ('wrong locked uid',"UPDATE company_training_identity SET original_task_uid='foreign-task' WHERE submission_id=?",(run['id'],)),
            ('invalid locked receipt',"UPDATE company_training_identity SET receipt='[]' WHERE submission_id=?",(run['id'],)),
            ('nonterminal phase',"UPDATE company_training SET state='Scheduling' WHERE id=?",(run['id'],)),
            ('unknown task',"UPDATE company_training SET state='unknown' WHERE id=?",(run['id'],)),
            ('foreign session',"UPDATE company_training SET session_id='foreign-session' WHERE id=?",(run['id'],)),
            ('foreign project',"UPDATE company_training SET project_id='foreign-project' WHERE id=?",(run['id'],)),
        ]
        for label,sql,args in cases:
            with self.subTest(label):
                try:
                    self.store.execute(sql,args)
                    self.assertEqual(rounds.pending_platform_operation(self.store,self.project['id'])['id'],'identity-locked')
                    with self.assertRaisesRegex(ValueError,'平台操作'):
                        self.research.autonomy.unresolved(self.project['id'])
                    self.assertTrue(self.research.workflow.external_pending(self.sid))
                    self.assertEqual(self.research.workflow.project(self.sid)['subject_id'],'identity-locked')
                finally:
                    self.store.execute('UPDATE company_operations SET state=?,request=?,result=? WHERE id=?',
                        (operation['state'],operation['request'],operation['result'],operation['id']))
                    self.store.execute('UPDATE company_training_identity SET requested_at=?,receipt=?,original_task_uid=? WHERE submission_id=?',
                        (identity['requested_at'],identity['receipt'],identity['original_task_uid'],run['id']))
                    self.store.execute('UPDATE company_training SET state=?,session_id=?,project_id=? WHERE id=?',
                        (run['state'],run['session_id'],run['project_id'],run['id']))
        self.assertIsNone(rounds.pending_platform_operation(self.store,self.project['id']))
        self.assertEqual(self.sdk_calls.count('submit'),1)

    def test_original_local_active_states_block_cloud_before_sdk_write(self):
        prepared=self.prepared();self.grant()
        pid=self.project['id'];stamp=now()
        for state in ['requested','running','unknown']:
            with self.subTest(state):
                try:
                    self.store.execute('INSERT INTO research_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        ('local-'+state,'original-'+state,pid,self.sid,self.plan['id'],'fixture-grant','fixture-hash','{}',state,'runs/fixture',None,stamp,stamp))
                    with self.assertRaisesRegex(ValueError,'本地计算'):
                        self.company.training.submit(self.sid,prepared['submission_id'],True)
                    self.assert_not_submitted(prepared['submission_id'])
                finally:self.store.execute('DELETE FROM research_runs WHERE id=?',('local-'+state,))

    def test_unknown_platform_operation_cannot_be_bypassed_by_cloud_submission(self):
        prepared=self.prepared();self.grant();stamp=now()
        self.store.execute('INSERT INTO company_operations VALUES(?,?,?,?,?,?,?,?)',
            ('original-unknown',self.sid,'fixture',dumps({'action':'lpai_start'}),'unknown',None,stamp,stamp))
        with self.assertRaisesRegex(ValueError,'平台操作'):
            self.company.training.submit(self.sid,prepared['submission_id'],True)
        self.assert_not_submitted(prepared['submission_id'])
        self.assertEqual(self.store.one("SELECT state FROM company_operations WHERE id='original-unknown'")['state'],'unknown')

    def test_current_wrapper_exception_never_exempts_an_older_operation(self):
        prepared=self.prepared();self.grant();stamp=now()
        request={'action':'lpai_submit','submission_id':prepared['submission_id']}
        self.store.execute('INSERT INTO company_operations VALUES(?,?,?,?,?,?,?,?)',
            ('older-wrapper',self.sid,'fixture',dumps(request),'requested',None,stamp,stamp))
        result=self.company.perform(self.sid,request,'current-wrapper',True)
        self.assertEqual(result['state'],'failed')
        self.assertIn('平台操作',result['message'])
        self.assert_not_submitted(prepared['submission_id'])
        self.assertEqual(self.store.one("SELECT state FROM company_operations WHERE id='older-wrapper'")['state'],'requested')

    def test_terminal_original_wrapper_allows_next_exact_attempt_within_budget(self):
        prepared=self.prepared();self.grant()
        result=self.company.perform(self.sid,{'action':'lpai_submit','submission_id':prepared['submission_id']},'first-wrapper',True)
        self.store.execute("UPDATE company_training SET state='Succeeded' WHERE id=?",(prepared['submission_id'],))
        self.store.execute("UPDATE company_watches SET state='Succeeded' WHERE task_uid=?",(result['item']['uid'],))
        original=self.store.one("SELECT * FROM company_operations WHERE id='first-wrapper'")
        frozen=json.loads(self.training_row(prepared['submission_id'])['request'])
        fields={'plan_id','trial_id','component_uid','task_name','reason','predecessor_uid','diagnostic_id','change_summary','validation','task_information'}
        request={key:value for key,value in frozen.items() if key in fields}
        request['task_name']='next-exact-attempt'
        request['task_information']={**request['task_information'],'expected_duration_desc':'same approved bounded configuration'}
        next_prepared=self.company.training.prepare(self.sid,request)
        with self.assertRaisesRegex(ValueError,'回收'):
            self.company.training.submit(self.sid,next_prepared['submission_id'],True)
        self.assertEqual(self.sdk_calls.count('submit'),1)
        with patch.object(self.company.outputs,'collect',return_value={'state':'read_failed','task_uid':result['item']['uid'],'message':'脚本化证据缺口'}):
            collected=self.company.outputs.advance(self.sid,result['item']['uid'])
        self.assertEqual(collected['state'],'diagnostic_only')
        original_sdk=self.company.training.sdk
        def next_sdk(operation,**kwargs):
            result=original_sdk(operation,**kwargs)
            return {**result,'item':{**result['item'],'uid':'next-original-task'}} if operation=='submit' else result
        self.company.training.sdk=next_sdk
        next_result=self.company.perform(self.sid,{'action':'lpai_submit','submission_id':next_prepared['submission_id']},'next-wrapper',True)
        self.assertEqual(next_result['state'],'submitted',next_result)
        self.assertEqual(next_result['item']['uid'],'next-original-task')
        self.assertEqual(self.sdk_calls.count('submit'),2)
        self.assertEqual(self.store.one("SELECT * FROM company_operations WHERE id='first-wrapper'"),original)

    def test_reporting_identity_blocks_prepared_cloud_compute_without_modifying_original(self):
        prepared=self.prepared();self.grant()
        from lab.research import plan_digest
        from lab.autonomy import policy,active
        self.research.autonomy.enable(self.sid,self.plan['id'],plan_digest(self.plan),
            {'objective':'离线门禁验证','evaluation':'固定评价','data_boundary':'固定组件',
             'allowed_changes':'固定范围','frozen_files':[]},'隔离模拟授权')
        branch=active(self.store,self.project['id']);p=policy(self.store,self.project['id'])
        self.store.execute('''CREATE TABLE research_finalizations(id TEXT PRIMARY KEY,session_id TEXT,
            authority_id TEXT,branch_id TEXT,plan_id TEXT,policy_generation TEXT,reason TEXT,text TEXT,created_at TEXT)''')
        self.store.execute('INSERT INTO research_finalizations VALUES(?,?,?,?,?,?,?,?,?)',
            ('final-fixture',self.sid,p['authority_id'],branch['id'],self.plan['id'],p['updated_at'],'no_progress','fixture',now()))
        with self.assertRaisesRegex(ValueError,'收尾'):
            self.company.training.submit(self.sid,prepared['submission_id'],True)
        self.assert_not_submitted(prepared['submission_id'])


if __name__=='__main__':unittest.main()
