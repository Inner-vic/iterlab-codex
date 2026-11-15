"""Offline compiler and lifecycle regression; no model, fitting or real cloud."""
import copy
import json
from pathlib import Path
import unittest

from lab.work_package import compile_package,workspace_path
from lab import cloud_results,cloud_runtime
from tests import test_component_configuration as configuration_fixture
from tests import test_component_provision as provision_fixture


class WorkPackageTests(unittest.TestCase):
    sdk=provision_fixture.ComponentProvisionTests.sdk
    approval=configuration_fixture.GPUProvisionTests.approval

    def setUp(self):
        configuration_fixture.GPUProvisionTests.setUp(self)
        root=Path(self.project['workspace'])
        (root/'train.py').write_text('import json, os\nfrom pathlib import Path\n# Offline fixture only\n',encoding='utf-8')
        (root/'data.csv').write_text('label,value\n0,1\n1,2\n',encoding='utf-8')
        self.plan=self.store.add_record(self.sid,{'kind':'plan','title':'Fixed-source experiment',
            'content':'Use reviewed source and data','summary':'compare classification on a fixed split',
            'evidence':['run-config.json','train.py','data.csv'],
            'scope_proposal':{'compute':{'gpu_count':8,'workflow':'batch'},'component_config':'run-config.json'}},{})
        self.trial=self.store.add_record(self.sid,{'kind':'trial','title':'first test','content':'run measured baseline',
            'plan_id':self.plan['id']},{})
        preview=self.company.training.preview_component(self.sid,self.plan)
        self.research.review(self.sid,self.approval(preview))
        self.grant=self.research.execution_grant(self.plan)
        row=self.store.one('SELECT configuration FROM research_component_previews WHERE id=?',(preview['id'],))
        self.configuration=json.loads(row['configuration'])

    def spec(self):
        return {'entrypoint':'train.py','files':['train.py','data.csv'],'args':['--option','a; echo BAD'],
                'required_files':['metrics.json'],'expected_seconds':90}

    def request(self):
        return {'plan_id':self.plan['id'],'trial_id':self.trial['id'],'task_name':'compiled-fixture',
                'reason':'verify compilation','work_package':self.spec()}

    def test_compiles_real_csv_and_root_source_without_platform_writes(self):
        before=list(self.sdk_calls)
        result=compile_package(self.store,self.plan,self.grant,self.trial,self.configuration,self.spec())
        self.assertEqual(before,self.sdk_calls)
        self.assertEqual(result['task_information']['expected_duration'],2)
        output=result['output_contract']
        self.assertEqual(output['source']['uid'],'volume-uid')
        self.assertEqual(output['runtime']['layout'],'workspace')
        patches=json.loads(self.store.safe_path(self.project['id'],result['patch_file']).read_text())
        self.assertIsInstance(patches,list)
        self.assertIn("'a; echo BAD'",patches[0]['value'])
        self.assertEqual(patches[0]['value'],patches[1]['value'])
        again=compile_package(self.store,self.plan,self.grant,self.trial,self.configuration,self.spec())
        self.assertEqual(result,again)
        with self.assertRaisesRegex(ValueError,'绑定具体方案'):
            cloud_results.execution_files(self.store,self.project['id'],output)

    def test_native_simplified_call_creates_once_and_uses_original_submit_guards(self):
        before=list(self.sdk_calls)
        prepared=self.company.training.prepare(self.sid,self.request())
        self.assertEqual(self.sdk_calls.count('provision_configured'),1)
        self.assertEqual(prepared['runtime_guards']['whole_command_timeout_seconds'],300)
        self.assertEqual(len(prepared['runtime_guards']['execution_files']),2)
        self.assertTrue(prepared['compiled_work_package']['manifest'])
        self.assertEqual(self.company.training.prepare(self.sid,self.request())['submission_id'],prepared['submission_id'])
        self.assertEqual(self.sdk_calls.count('provision_configured'),1)
        authority=self.company.training.delegated_submission(self.sid,prepared['submission_id'])
        self.assertIsNotNone(authority)
        submitted=self.company.perform(self.sid,{'action':'lpai_submit','submission_id':prepared['submission_id']},
            'compiled-submit',True,approval_source=authority)
        self.assertEqual(submitted['state'],'submitted')
        self.assertEqual(self.sdk_calls.count('submit'),1)
        self.assertFalse(self.store.rows('SELECT * FROM research_runs'))

    def test_bad_or_changed_inputs_never_reach_component_creation(self):
        for change in [dict(entrypoint='../train.py'),dict(files=['train.py','unknown.csv']),
                       dict(expected_seconds=301),dict(args=['\x00']),dict(required_files=['../metrics.json'])]:
            with self.subTest(change=change),self.assertRaises((ValueError,OSError)):
                self.company.training.prepare(self.sid,{**self.request(),'work_package':{**self.spec(),**change}})
        self.assertNotIn('provision_configured',self.sdk_calls)
        self.assertFalse(self.store.rows('SELECT * FROM company_training'))
        (Path(self.project['workspace'])/'data.csv').write_text('changed data')
        with self.assertRaisesRegex(ValueError,'冻结证据'):
            compile_package(self.store,self.plan,self.grant,self.trial,self.configuration,self.spec())

    def test_layout_does_not_relax_legacy_paths_or_accept_traversal(self):
        for name in ['../data.csv','.secret.json','/tmp/train.py','a//train.py','a/../train.py','auth.env']:
            with self.assertRaises(ValueError):workspace_path(name)
        base={'command_path':'/lpai_task/cmd','input_root':'/tmp/input','output_root':'/mnt/output','work_files':['train.py']}
        with self.assertRaises(ValueError):cloud_runtime.normalize(base)
        self.assertEqual(cloud_runtime.normalize({**base,'layout':'workspace'})['work_files'],['train.py'])

    def test_no_unreviewed_volume_or_mixed_low_level_overrides(self):
        bad=copy.deepcopy(self.configuration);bad['template']['output_volume']={}
        with self.assertRaisesRegex(ValueError,'结果卷身份'):
            compile_package(self.store,self.plan,self.grant,self.trial,bad,self.spec())
        for key,value in [('patch_file','anything.json'),('output_contract',{}),('task_information',{})]:
            with self.assertRaisesRegex(ValueError,'不要同时填写'):
                self.company.training.prepare(self.sid,{**self.request(),key:value})
        self.assertNotIn('provision_configured',self.sdk_calls)

    def test_actual_encoded_command_limit_is_checked_before_any_creation(self):
        import hashlib,random,string
        rng=random.Random(42);refs=[];names=[]
        for index in range(3):
            name=f'noise{index}.csv';body=''.join(rng.choices(string.ascii_letters+string.digits,k=62000)).encode()
            (Path(self.project['workspace'])/name).write_bytes(body)
            refs.append({'path':name,'size':len(body),'sha256':hashlib.sha256(body).hexdigest()});names.append(name)
        plan={**self.plan,'evidence':json.dumps(json.loads(self.plan['evidence'])+refs)}
        before=list(self.sdk_calls)
        with self.assertRaisesRegex(ValueError,'命令参数过长'):
            compile_package(self.store,plan,self.grant,self.trial,self.configuration,
                            {**self.spec(),'files':['train.py',*names]})
        self.assertEqual(self.sdk_calls,before)
        self.assertFalse(list((Path(self.project['workspace'])/'research/work-packages').glob('*/manifest.json')))

    def test_pinned_native_schema_uses_explicit_file_without_new_fields(self):
        value={'schema':'iterlab-experiment/v1',**self.spec()}
        (Path(self.project['workspace'])/'experiment.json').write_text(json.dumps(value),encoding='utf-8')
        request=self.request();request.pop('work_package')
        request.update(patch_file='experiment.json',predecessor_uid='',diagnostic_id='',change_summary='',validation='',
            task_information={'problem_statement':'legacy','solution_approach':'legacy','expected_result':'legacy',
                              'expected_duration':9999,'expected_duration_desc':'untrusted old metadata'})
        prepared=self.company.training.prepare(self.sid,request)
        self.assertEqual(prepared['task_information']['expected_duration'],2)
        self.assertNotIn('legacy',prepared['task_information']['problem_statement'])
        self.assertEqual(prepared['compiled_work_package']['compatibility']['mode'],'registered_patch_file_v1')
        self.assertEqual(self.sdk_calls.count('provision_configured'),1)
        frozen=json.loads(self.store.one('SELECT request FROM company_training WHERE id=?',(prepared['submission_id'],))['request'])
        self.assertEqual(frozen['work_package_compilation'],prepared['compiled_work_package'])
        with self.assertRaisesRegex(ValueError,'不与高级'):
            self.company.training.prepare(self.sid,{**request,'output_contract':{'required_files':['other.json']}})
