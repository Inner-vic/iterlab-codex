import json
from pathlib import Path
import unittest

from lab import cloud_runtime,task_policy
from lab.lpai_evidence import apply_code_patches
from lab.lpai_training import AUTO_COMMAND_PATHS,CPU_PROFILES,LPAITraining
from lab.research import plan_digest
from tests import test_lpai_recovery as fixture


class ComponentProvisionTests(unittest.TestCase):
    def setUp(self):
        fixture.RecoveryTests.setUp(self)
        self.ambiguous=False;self.visible=False
        self.component='new-component';self.name=None
        self.volume=CPU_PROFILES['vc-chassis'][1];self.mount='/mnt/volumes/'+self.volume
        self.volumes=[{'name':self.volume,'env_key':'LPAI_INPUT_DATA_0','env_value':self.mount}]
        self.raw={'lpai_task':{'cmd':'template stub','roles':[{'replica':1,'cmd':'template stub'}]}}
        self.company.training.sdk=self.sdk
        self.company.outputs.inspect_volume=lambda name:{'name':name,'uid':'volume-uid','namespace':'vc-chassis'}
        (Path(self.project['workspace'])/'task-patch.json').write_text(json.dumps([
            {'path':path,'value':'python train.py'} for path in AUTO_COMMAND_PATHS]),encoding='utf-8')

    def sdk(self,operation,**kwargs):
        self.sdk_calls.append(operation)
        if operation=='cpu_template':
            self.name=kwargs['component_name']
            return {'config':{'name':self.name,'cmd':'template stub','image':'python:3.11','queue_code':'fixture-queue'},
                'namespace':'vc-chassis','cluster':'fixture-cluster','source_uid':kwargs['component_uid'],
                'source_digest':'b'*64,'config_sha256':'c'*64,'gpu_count':0,'cpu':1,'memory_gib':4}
        if operation=='provision_component':
            self.assertEqual(self.store.one('SELECT state FROM research_component_provisions')['state'],'requested')
            self.assertEqual(kwargs['component_name'],self.name)
            if self.ambiguous:raise TimeoutError('fake reply lost')
            return {'state':'created','uid':self.component}
        if operation=='lookup_component':
            return {'state':'observed' if self.visible else 'not_observed','name':self.name,'namespace':'vc-chassis','uid':self.component}
        if operation=='verify_provisioned_component':
            self.assertEqual(self.store.one('SELECT component_uid FROM research_component_provisions')['component_uid'],self.component)
        base={'uid':self.component,'name':self.name,'namespace':'vc-chassis','cluster':'fixture-cluster','digest':'a'*64,'gpu_count':0,'volumes':self.volumes}
        if operation=='verify_provisioned_component':return base
        if operation=='component':
            data=cloud_runtime.apply(apply_code_patches(self.raw,kwargs.get('patches',[])),kwargs.get('result_runtime'))
            return {**base,'execution_digest':task_policy.fingerprint({'component_digest':base['digest'],'data':data})}
        if operation=='submit':
            self.assertEqual(kwargs['component_uid'],self.component)
            return {'state':'submitted','item':{'uid':'new-task','name':'auto-fixture','phase':'pending','namespace':'vc-chassis','project_uid':self.component}}
        raise AssertionError(operation)

    def approval(self,preview):
        return {'plan_id':self.plan['id'],'expected_digest':plan_digest(self.plan),'decision':'accept',
            'reason':'fixture approval','execution':'lpai','allow_cloud':True,'component_preview_id':preview['id'],'max_gpus':0,
            'max_runs':6,'max_fits':4,'timeout':300,'autonomous':True,'task_policy':{'schema':1,'allowed_patch_paths':AUTO_COMMAND_PATHS},
            'research_scope':{'objective':'improve held-out classification','evaluation':'fixed split and macro F1',
                'data_boundary':'published training and validation only','allowed_changes':'vary training settings'}}

    def request(self):
        return {'plan_id':self.plan['id'],'trial_id':self.trial['id'],'task_name':'auto-fixture','reason':'verify auto-create',
            'predecessor_uid':'','diagnostic_id':'','change_summary':'','validation':'','patch_file':'task-patch.json',
            'task_information':{'problem_statement':'verify provision','solution_approach':'run frozen command',
                'expected_result':'return metrics','expected_duration':1,'expected_duration_desc':'fixture duration'},
            'output_contract':{'required_files':['metrics.json'],'runtime':{'command_path':AUTO_COMMAND_PATHS[0],
                'mirror_paths':[AUTO_COMMAND_PATHS[1]],'input_root':'/tmp/iterlab-fixture','output_root':self.mount+'/fixture'},
                'source':{'kind':'volume','name':self.volume,'uid':'volume-uid','prefix':'fixture','mount_root':self.mount}}}

    def test_incomplete_new_scope_never_silently_uses_cpu_template(self):
        candidate=self.store.add_record(self.sid,{'kind':'plan','title':'GPU proposal missing metadata',
            'content':'A prose GPU recommendation is insufficient',
            'scope_proposal':{'objective':'new GPU scope, not authorized'}},{})
        before=list(self.sdk_calls)
        with self.assertRaisesRegex(ValueError,'component_config'):
            self.company.training.preview_component(self.sid,candidate)
        self.assertEqual(self.sdk_calls,before)
        self.assertFalse(self.store.rows('SELECT * FROM research_component_previews'))
        self.assertFalse(self.store.rows('SELECT * FROM research_component_provisions'))

    def test_exact_approval_creates_once_then_submits_and_tracks_original_uid(self):
        training=self.company.training
        preview=training.preview_component(self.sid,self.plan)
        self.assertEqual(training.preview_component(self.sid,self.plan),preview)
        request=self.request();request.pop('patch_file')
        with self.assertRaisesRegex(ValueError,'未获新建组件授权'):training.prepare(self.sid,request)
        self.assertEqual(self.sdk_calls,['cpu_template'])
        self.research.review(self.sid,self.approval(preview))
        self.assertEqual(self.sdk_calls,['cpu_template'])
        prepared=training.prepare(self.sid,self.request());submission=prepared['submission_id']
        self.assertEqual(training.prepare(self.sid,self.request())['submission_id'],submission)
        authority=training.delegated_submission(self.sid,submission)
        self.assertEqual(authority['kind'],'delegated_research_scope')
        result=self.company.perform(self.sid,{'action':'lpai_submit','submission_id':submission},'auto-operation',True,approval_source=authority)
        self.assertEqual(result['state'],'submitted',result)
        self.assertEqual(self.store.one('SELECT component_uid,task_uid FROM company_training'),{'component_uid':self.component,'task_uid':'new-task'})
        self.assertEqual(self.sdk_calls.count('provision_component'),1)
        self.assertEqual(self.sdk_calls.count('submit'),1)

    def test_lost_create_reply_reopens_with_exact_lookup_only(self):
        preview=self.company.training.preview_component(self.sid,self.plan)
        self.research.review(self.sid,self.approval(preview));self.ambiguous=True
        with self.assertRaisesRegex(ValueError,'创建结果未知'):self.company.training.prepare(self.sid,self.request())
        self.assertEqual(self.store.one('SELECT state FROM research_component_provisions')['state'],'unknown')
        self.company.training=LPAITraining(self.company);self.company.training.sdk=self.sdk
        with self.assertRaisesRegex(ValueError,'尚未查到原组件'):self.company.training.prepare(self.sid,self.request())
        self.visible=True
        self.company.training.prepare(self.sid,self.request())
        self.assertEqual(self.store.one('SELECT state FROM research_component_provisions')['state'],'ready')
        self.assertEqual(self.sdk_calls.count('provision_component'),1)
        self.assertEqual(self.sdk_calls.count('lookup_component'),2)
        self.assertNotIn('submit',self.sdk_calls)

    def test_invalid_source_file_is_rejected_before_approved_component_creation(self):
        training=self.company.training
        preview=training.preview_component(self.sid,self.plan)
        self.research.review(self.sid,self.approval(preview))
        before=list(self.sdk_calls)
        request=self.request()
        source='research/gpu/implementation/train.py'
        request['output_contract']['runtime']['work_files']=[source+'=>implementation/train.py']
        with self.assertRaises((ValueError,FileNotFoundError)):training.prepare(self.sid,request)
        self.assertEqual(self.sdk_calls,before)
        self.assertEqual(self.store.one('SELECT state FROM research_component_provisions')['state'],'approved')
        self.assertFalse(self.store.rows('SELECT * FROM company_training'))
        path=Path(self.project['workspace'])/source;path.parent.mkdir(parents=True);path.write_bytes(b'x'*(64*1024+1))
        with self.assertRaises(ValueError):training.prepare(self.sid,request)
        self.assertEqual(self.sdk_calls,before)
        self.assertEqual(self.store.one('SELECT state FROM research_component_provisions')['state'],'approved')
        self.assertFalse(self.store.rows('SELECT * FROM company_training'))
