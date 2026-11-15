"""Formal prepare/submit/recovery boundaries, with an offline SDK/platform."""
import copy
import json
import unittest
from unittest.mock import patch

from lab import cloud_runtime,task_policy
from lab.lpai_evidence import apply_code_patches
from tests import test_task_policy as policy_fixture
from tests import test_lpai_recovery as recovery_fixture


class ComponentRuntimeTests(unittest.TestCase):
    prepared=recovery_fixture.RecoveryTests.prepared
    training_row=recovery_fixture.RecoveryTests.training_row
    grant=recovery_fixture.RecoveryTests.grant
    request=policy_fixture.TaskWorkPackageTests.request
    authorize=policy_fixture.TaskWorkPackageTests.authorize

    def setUp(self):
        recovery_fixture.RecoveryTests.setUp(self);self.authorize()
        self.raw['lpai_input_pvc']=[{'name':'volume-1','env_key':'LPAI_INPUT_DATA_0','env_value':'/mnt/volumes/volume-1'}]
        prior=self.company.training.sdk
        def sdk(operation,**params):
            result=prior(operation,**params)
            if operation=='component':
                data=cloud_runtime.apply(apply_code_patches(self.raw,params.get('patches',[])),params.get('result_runtime'))
                result.update(execution_digest=task_policy.fingerprint({'component_digest':result['digest'],'data':data}),volumes=copy.deepcopy(self.raw['lpai_input_pvc']))
            return result
        self.company.training.sdk=sdk
        self.company.outputs.inspect_volume=lambda name:{'name':name,'uid':'volume-uid','namespace':'vc-chassis'}

    def runtime_request(self):
        request=self.request()
        request['output_contract']={'required_files':['metrics.json'],
            'runtime':{'command_path':'/lpai_task/cmd','input_root':'/workspace/source','output_root':'/mnt/volumes/volume-1/user/results'},
            'source':{'kind':'volume','name':'volume-1','uid':'volume-uid','prefix':'user/results','mount_root':'/mnt/volumes/volume-1'}}
        return request

    def test_prepared_runtime_is_submitted_with_same_digest_not_shared_component_edit(self):
        request=self.runtime_request();prepared=self.company.training.prepare(self.sid,request);id=prepared['submission_id']
        frozen=json.loads(self.training_row(id)['request']);expected=frozen['output_contract']['expected']
        self.assertTrue(expected['source']['prefix'].endswith('/'+id));self.assertTrue(expected['runtime']['output_root'].endswith('/'+id))
        self.assertFalse(prepared['result_bundle']['integration_required']);self.assertIn('result_runtime',frozen)
        original=copy.deepcopy(self.raw)
        authority=self.company.training.delegated_submission(self.sid,id)
        result=self.company.perform(self.sid,{'action':'lpai_submit','submission_id':id},'runtime-submit',True,approval_source=authority)
        self.assertEqual(result['state'],'submitted',result)
        params=next(p for op,p in self.sdk_params if op=='submit')
        self.assertEqual(params['result_runtime'],frozen['result_runtime'])
        self.assertEqual(params['expected_execution_digest'],frozen['execution_snapshot']['execution_digest'])
        self.assertEqual(self.raw,original);self.assertEqual(self.sdk_calls.count('submit'),1)
        again=self.company.training.submit(self.sid,id,True)
        self.assertEqual(again['item']['uid'],'new-task');self.assertEqual(self.sdk_calls.count('submit'),1)

    def test_authorized_duration_unit_mismatch_rejects_at_prepare_before_sdk_read(self):
        request=self.runtime_request()
        request['task_information']['expected_duration']=300  # Minutes, not seconds.
        sdk_before=list(self.sdk_calls)
        before=self.store.rows('SELECT id FROM company_training')
        with self.assertRaisesRegex(ValueError,'300 分钟.*9000 秒'):
            self.company.training.prepare(self.sid,request)
        self.assertEqual(self.sdk_calls,sdk_before)
        self.assertEqual(self.store.rows('SELECT id FROM company_training'),before)

    def test_staging_training_files_cannot_turn_placeholder_into_real_entry(self):
        from pathlib import Path
        request=self.runtime_request();request['patch_file']=''
        path=Path(self.session['workspace'])/'implementation/train.py';path.parent.mkdir();path.write_bytes(b'print(1)')
        request['output_contract']['runtime']['work_files']=['implementation/train.py']
        self.raw['lpai_task']['cmd']='python3 -c "print(\'IterLab CPU component ready\')"'
        before=self.store.rows('SELECT id FROM company_training')
        with self.assertRaisesRegex(ValueError,'占位测试命令'):
            self.company.training.prepare(self.sid,request)
        self.assertEqual(self.store.rows('SELECT id FROM company_training'),before)
        self.assertNotIn('submit',self.sdk_calls)

    def test_prepare_reports_exact_unsupported_field_without_echoing_values(self):
        request=self.runtime_request();request['expected_information']={'credential':'must-not-echo'}
        before=list(self.sdk_calls)
        with self.assertRaisesRegex(ValueError,'不支持字段：expected_information') as result:
            self.company.training.prepare(self.sid,request)
        self.assertNotIn('must-not-echo',str(result.exception));self.assertEqual(self.sdk_calls,before)
        self.assertNotIn('submit',self.sdk_calls)

    def test_task_duration_type_error_names_field_and_units_before_sdk(self):
        request=self.runtime_request();request['task_information']['expected_duration']='5'
        before=list(self.sdk_calls)
        with self.assertRaisesRegex(ValueError,'expected_duration.*整数.*分钟'):
            self.company.training.prepare(self.sid,request)
        self.assertEqual(self.sdk_calls,before)

    def test_predecessor_submission_id_is_not_queried_as_platform_task_uid(self):
        prepared=self.company.training.prepare(self.sid,self.runtime_request())
        id=prepared['submission_id']
        request=self.runtime_request();request['predecessor_uid']=id
        self.store.execute("UPDATE company_training SET task_uid='known-original-task',state='failed' WHERE id=?",(id,))
        before=list(self.calls);sdk_before=list(self.sdk_calls)
        with self.assertRaisesRegex(ValueError,'官方task_uid.*submission_id.*known-original-task'):
            self.company.training.prepare(self.sid,request)
        self.assertEqual(self.calls,before);self.assertEqual(self.sdk_calls,sdk_before)

    def test_failed_same_trial_cannot_erase_retry_provenance(self):
        prepared=self.company.training.prepare(self.sid,self.runtime_request())
        self.store.execute("UPDATE company_training SET task_uid='original-task',state='failed' WHERE id=?",(prepared['submission_id'],))
        request=self.runtime_request()
        for key in ['predecessor_uid','diagnostic_id','change_summary','validation']:request[key]=''
        sdk_before=list(self.sdk_calls);calls_before=list(self.calls)
        with self.assertRaisesRegex(ValueError,'同一实验已有失败任务.*predecessor_uid=original-task'):
            self.company.training.prepare(self.sid,request)
        self.assertEqual(self.sdk_calls,sdk_before);self.assertEqual(self.calls,calls_before)
        request['predecessor_uid']='unrelated-failure'
        with self.assertRaisesRegex(ValueError,'最近失败任务.*original-task.*其他任务'):
            self.company.training.prepare(self.sid,request)
        self.assertEqual(self.sdk_calls,sdk_before);self.assertEqual(self.calls,calls_before)
        proof=self.company.training.diagnose(self.sid,'original-task')
        request.update(predecessor_uid='original-task',diagnostic_id=proof['id'],change_summary='explicit command repair',validation='offline command check')
        result=self.company.training.prepare(self.sid,request)
        frozen=json.loads(self.training_row(result['submission_id'])['request'])
        self.assertEqual(frozen['predecessor_uid'],'original-task');self.assertEqual(frozen['diagnostic_id'],proof['id'])
        self.assertNotIn('submit',self.sdk_calls)

    def test_failed_other_trial_does_not_require_fake_retry_fields(self):
        prepared=self.company.training.prepare(self.sid,self.runtime_request())
        self.store.execute("UPDATE company_training SET task_uid='original-task',state='failed' WHERE id=?",(prepared['submission_id'],))
        other=self.store.add_record(self.sid,{'kind':'trial','title':'independent comparison','content':'Separate explicitly identified experiment','status':'proposed','evidence':[],'plan_id':self.plan['id']},{})
        request=self.runtime_request();request['trial_id']=other['id']
        for key in ['predecessor_uid','diagnostic_id','change_summary','validation']:request[key]=''
        self.assertEqual(self.company.training.prepare(self.sid,request)['state'],'prepared')
        self.assertNotIn('submit',self.sdk_calls)

    def test_changed_volume_identity_blocks_submission_without_consuming_attempt(self):
        prepared=self.company.training.prepare(self.sid,self.runtime_request());id=prepared['submission_id']
        self.company.outputs.inspect_volume=lambda name:{'name':name,'uid':'other-volume','namespace':'vc-chassis'}
        with self.assertRaisesRegex(ValueError,'卷身份'):self.company.training.submit(self.sid,id,True)
        self.assertEqual(self.training_row(id)['state'],'prepared');self.assertNotIn('submit',self.sdk_calls)

    def test_submit_verifies_long_evidence_paths_without_treating_them_as_missing(self):
        import hashlib
        from lab import cloud_results

        relative = f"research/{'a' * 100}/{'b' * 100}/metrics.json"
        evidence = self.store.evidence_path(self.project['id'], relative)
        self.assertGreater(len(str(evidence)), 260)
        evidence.parent.mkdir(parents=True)
        self.addCleanup(evidence.parent.rmdir)
        self.addCleanup(evidence.unlink, missing_ok=True)
        original = b'{"verified":true}'
        evidence.write_bytes(original)
        make_bundle = cloud_results.bundle

        def bundle_with_evidence(*args):
            bundle = make_bundle(*args)
            bundle['files'].append({'path': relative, 'sha256': hashlib.sha256(original).hexdigest()})
            return bundle

        with patch.object(cloud_results, 'bundle', side_effect=bundle_with_evidence):
            submission = self.company.training.prepare(self.sid, self.runtime_request())['submission_id']
        evidence.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, '证据已改变'):
            self.company.training.submit(self.sid, submission, True)
        self.assertNotIn('submit', self.sdk_calls)
        evidence.write_bytes(original)
        result = self.company.training.submit(self.sid, submission, True)
        self.assertEqual(result['state'], 'submitted')
        self.assertEqual(self.sdk_calls.count('submit'), 1)

    def test_work_file_bytes_are_frozen_into_submission_identity(self):
        import base64
        from pathlib import Path
        from lab.result_receipt_writer import digest
        path=Path(self.session['workspace'])/'implementation/train.py';path.parent.mkdir();path.write_bytes(b'print(1)')
        request=self.runtime_request();request['output_contract']['runtime']['work_files']=['implementation/train.py']
        first=self.company.training.prepare(self.sid,request)['submission_id']
        original=json.loads(self.training_row(first)['request']);runtime=original['result_runtime']
        self.assertEqual(base64.b64decode(runtime['inputs']['implementation/train.py']),b'print(1)')
        self.assertEqual(runtime['contract']['sha256'],digest(runtime['contract']['expected']))
        self.assertEqual(runtime['timeout_seconds'],9000)
        path.write_bytes(b'print(2)')
        second=self.company.training.prepare(self.sid,request)['submission_id']
        self.assertNotEqual(first,second)
        self.assertEqual(json.loads(self.training_row(first)['request']),original)
        self.assertNotIn('submit',self.sdk_calls)

    def test_unmounted_source_and_unauthorized_wrapper_are_rejected(self):
        request=self.runtime_request();request['output_contract']['source']['mount_root']='/mnt/elsewhere'
        request['output_contract']['runtime']['output_root']='/mnt/elsewhere/user/results'
        with self.assertRaisesRegex(ValueError,'挂载根'):self.company.training.prepare(self.sid,request)
        self.assertNotIn('submit',self.sdk_calls)


if __name__=='__main__':unittest.main()
