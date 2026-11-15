"""Isolated execution-policy regression. No live SDK, network or training."""
import hashlib
import copy
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

from lab import task_policy
from lab.lpai_evidence import apply_code_patches
from lab.research import plan_digest
from tests import test_lpai_recovery as fixtures


class PolicyUnitTests(unittest.TestCase):
    def test_exact_paths_only_and_canonical_identity(self):
        a={'schema':1,'allowed_patch_paths':['/lpai_task/cmd','/lpai_git/0/commit_id']}
        b={'schema':1,'allowed_patch_paths':list(reversed(a['allowed_patch_paths']))}
        self.assertEqual(task_policy.fingerprint(task_policy.normalize(a)),task_policy.fingerprint(task_policy.normalize(b)))
        for value in [True,{'schema':True,'allowed_patch_paths':['/lpai_task/cmd']},
                      {'schema':1,'allowed_patch_paths':[]},
                      {'schema':1,'allowed_patch_paths':['/lpai_task/*']},
                      {'schema':1,'allowed_patch_paths':['/lpai_task/roles/00/cmd']},
                      {'schema':1,'allowed_patch_paths':['/lpai_git/٠/commit_id']},
                      {'schema':1,'allowed_patch_paths':['/lpai_task/roles/0/resource/Gpu']},
                      {'schema':1,'allowed_patch_paths':['/lpai_task/cmd']*2},
                      {'schema':1,'allowed_patch_paths':['/lpai_task/cmd'],'allow_all':True}]:
            with self.subTest(value=value),self.assertRaises(ValueError):task_policy.normalize(value)

    def test_supported_positions_do_not_disclose_values_or_invent_fields(self):
        data={'lpai_task':{'cmd':'secret-command','roles':[{'cmd':'role-command'},{'resource':{'Gpu':8}}]},
              'lpai_git':[{'commit_id':'a'*40},{'repo':'other'}],'lpai_envs':['never']}
        paths=task_policy.supported_paths(data)
        self.assertEqual(paths,['/lpai_git/0/commit_id','/lpai_task/cmd','/lpai_task/roles/0/cmd'])
        self.assertNotIn('secret-command',str(paths));self.assertNotIn('never',str(paths))

    def test_sdk_bridge_checks_applied_data_digest_before_first_write(self):
        spec=importlib.util.spec_from_file_location('isolated_lpai_bridge',Path(__file__).resolve().parents[1]/'scripts/lpai_sdk_bridge.py')
        bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
        raw={'uid':'component-1','namespace':'fixture','cluster':'fixture-cluster','name':'component',
             'data':{'lpai_task':{'cmd':'python old.py','roles':[{'replica':1,'resource':{'Gpu':1}}]}}}
        writes=[]
        class Conf:
            backend='bos'
            def __init__(self,**kwargs):pass
            def get(self,key):return 'isolated-fixture-value'
            def get_namespace(self):return 'fixture'
            def get_store_type(self):return self.backend
        class Project:
            def __init__(self,*args):pass
            def get(self,uid):return copy.deepcopy(raw)
        class Task:
            def __init__(self,*args):pass
            def validate_data(self,data):
                if data.get('lpai_input_dataset')=='invalid':raise ValueError('fixture invalid data')
            def submit(self,**kwargs):writes.append(kwargs);return {'uid':'task-1','namespace':'fixture'}
        modules={}
        for name,attr,value in [('lpai.conf.lipai_conf','LpaiConf',Conf),('lpai.pipelinev2.project','Project',Project),('lpai.pipelinev2.task','Task',Task)]:
            modules[name]=types.ModuleType(name);setattr(modules[name],attr,value)
        with patch.dict(sys.modules,modules):
            request={'namespace':'fixture','component_uid':'component-1','operation':'component',
                     'patches':[{'path':'/lpai_task/cmd','value':'python fixed.py'}]}
            preview=bridge.execute(request)
            submit={**request,'operation':'submit','expected_digest':preview['digest'],'expected_execution_digest':'0'*64,
                    'task_name':'fixture','task_information':{},'expected_submission_policy':preview['submission_policy']}
            with self.assertRaisesRegex(ValueError,'冻结工作包'):bridge.execute(submit)
            self.assertEqual(writes,[])
            with self.assertRaisesRegex(ValueError,'结果存储'):
                bridge.execute({**submit,'expected_execution_digest':preview['execution_digest'],
                    'expected_submission_policy':{**preview['submission_policy'],'storage_type':2}})
            self.assertEqual(writes,[])
            result=bridge.execute({**submit,'expected_execution_digest':preview['execution_digest']})
            raw['data']['lpai_input_dataset']='invalid'
            with self.assertRaisesRegex(ValueError,'本地输入配置校验失败'):bridge.execute(request)
            raw['data'].pop('lpai_input_dataset')
            Conf.backend=None
            with self.assertRaisesRegex(ValueError,'SDK存储配置无效'):bridge.execute(request)
            Conf.backend='bos'
            self.assertEqual(raw['data']['lpai_task']['cmd'],'python old.py')
            for placeholder_position in ['cmd','role']:
                with self.subTest(placeholder_position=placeholder_position):
                    raw['data']['lpai_task']['cmd']='python real.py'
                    raw['data']['lpai_task']['roles'][0]['cmd']='python real.py'
                    if placeholder_position=='cmd':raw['data']['lpai_task']['cmd']='echo IterLab CPU component ready'
                    else:raw['data']['lpai_task']['roles'][0]['cmd']='python3 -c "print(\'IterLab approved component ready\')"'
                    with self.assertRaisesRegex(ValueError,'占位测试命令'):
                        bridge.execute({**submit,'patches':[]})
        self.assertEqual(len(writes),1);self.assertEqual(writes[0]['data']['lpai_task']['cmd'],'python fixed.py')
        self.assertNotIn('storage_type',writes[0])
        self.assertEqual(preview['submission_policy']['configured_sdk_store_type'],'bos')
        self.assertFalse(preview['submission_policy']['override_sent'])
        self.assertFalse(preview['preflight']['effective_storage_backend_verified'])
        self.assertEqual(writes[0]['data']['lpai_task']['roles'][0].get('cmd'),None)
        self.assertEqual(result['execution_digest'],preview['execution_digest'])


class TaskWorkPackageTests(unittest.TestCase):
    setUp=fixtures.RecoveryTests.setUp
    prepared=fixtures.RecoveryTests.prepared
    grant=fixtures.RecoveryTests.grant
    training_row=fixtures.RecoveryTests.training_row

    def request(self):
        prepared=self.prepared()
        frozen=json.loads(self.training_row(prepared['submission_id'])['request'])
        keys={'plan_id','trial_id','component_uid','task_name','reason','predecessor_uid',
              'diagnostic_id','change_summary','validation','task_information'}
        # Remove the previous preview's correlation marker, not a platform write.
        frozen['task_information']['expected_duration_desc']='bounded fixture'
        return {key:frozen[key] for key in keys}

    def authorize(self,paths=None):
        self.policy={'schema':1,'allowed_patch_paths':paths or ['/lpai_task/cmd']}
        result=self.research.review(self.sid,{'plan_id':self.plan['id'],'expected_digest':plan_digest(self.plan),'decision':'accept',
            'reason':'isolated first scope approval','execution':'lpai','allow_cloud':True,'component_uid':'component-1',
            'component_digest':'a'*64,'max_gpus':16,'max_runs':4,'max_fits':4,'timeout':9000,'task_policy':self.policy,
            'autonomous':True,'research_scope':{'objective':'fixture recovery','evaluation':'unchanged protocol',
                'data_boundary':'fixture data','allowed_changes':'only approved source and entry','frozen_files':[]}})
        self.grant_id=result['grant_id']
        original=self.company.training.sdk
        self.raw={'lpai_task':{'cmd':'python old.py','roles':[{'resource':{'Gpu':16},'replica':1}]},
                  'lpai_git':[{'commit_id':'a'*40}]}
        def sdk(operation,**kwargs):
            result=original(operation,**kwargs)
            if operation=='component':
                actual=apply_code_patches(self.raw,kwargs.get('patches',[]))
                result.update(execution_digest=task_policy.fingerprint({'component_digest':result['digest'],'data':actual}),
                              supported_patch_paths=task_policy.supported_paths(self.raw))
            return result
        self.company.training.sdk=sdk
        return result

    def patch_request(self,request,value='python fixed.py'):
        from pathlib import Path
        path=Path(self.project['workspace'])/'attempt-patch.json'
        path.write_text(json.dumps([{'path':'/lpai_task/cmd','value':value}]),encoding='utf-8')
        return {**request,'patch_file':'attempt-patch.json'},path

    def test_initial_scope_exposes_original_limits_and_old_grants_stay_strict(self):
        self.grant()
        old=self.research.resources(self.plan)['execution_scope']
        self.assertIsNone(old['task_policy']);self.assertEqual(old['cloud']['max_gpus'],16)
        request,path=self.patch_request(self.request())
        with self.assertRaisesRegex(ValueError,'冻结证据'):self.company.training.prepare(self.sid,request)
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM research_reviews')['n'],1)

    def test_approved_task_patch_runs_under_same_plan_and_cumulative_grant(self):
        request=self.request();self.authorize();request,path=self.patch_request(request)
        count=self.store.one("SELECT COUNT(*) AS n FROM records WHERE kind='plan'")['n']
        prepared=self.company.training.prepare(self.sid,request)
        original_snapshot=json.loads(self.training_row(prepared['submission_id'])['request'])['execution_snapshot']
        original_file_digest=hashlib.sha256(path.read_bytes()).hexdigest()
        # The task snapshot, rather than mutable input text, is the execution source.
        path.write_text('[{"path":"/lpai_task/cmd","value":"python changed.py"}]',encoding='utf-8')
        authority=self.company.training.delegated_submission(self.sid,prepared['submission_id'])
        self.assertIsNotNone(authority)
        result=self.company.perform(self.sid,{'action':'lpai_submit','submission_id':prepared['submission_id']},
                                    'same-plan-work-package',True,approval_source=authority)
        self.assertEqual(result['state'],'submitted');self.assertEqual(self.sdk_calls.count('submit'),1)
        params=next(params for operation,params in self.sdk_params if operation=='submit')
        self.assertEqual(params['patches'][0]['value'],'python fixed.py')
        self.assertEqual(params['expected_execution_digest'],original_snapshot['execution_digest'])
        self.assertEqual(original_snapshot['patch_file_sha256'],original_file_digest)
        self.assertEqual(self.store.one("SELECT COUNT(*) AS n FROM records WHERE kind='plan'")['n'],count)
        self.assertEqual(self.research.resources(self.plan)['used_runs'],1)
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM research_reviews')['n'],1)
        self.assertEqual(self.research.status(self.sid)['cloud_scopes'][0]['task_policy'],self.policy)

    def test_unapproved_position_and_bad_values_stop_before_write(self):
        request=self.request();self.authorize();request,path=self.patch_request(request)
        for patch in [{'path':'/lpai_git/0/commit_id','value':'b'*40},
                      {'path':'/lpai_task/roles/0/resource/Gpu','value':'32'},
                      {'path':'/lpai_task/cmd','value':'python old.py'},
                      {'path':'/lpai_task/cmd','value':'TOKEN=credential python fixed.py'}]:
            path.write_text(json.dumps([patch]),encoding='utf-8')
            with self.subTest(patch=patch),self.assertRaises(ValueError):self.company.training.prepare(self.sid,request)
        self.assertNotIn('submit',self.sdk_calls)

    def test_corrupted_snapshot_or_changed_sdk_execution_cannot_submit(self):
        request=self.request();self.authorize();request,path=self.patch_request(request)
        prepared=self.company.training.prepare(self.sid,request);id=prepared['submission_id']
        row=self.training_row(id);frozen=json.loads(row['request']);frozen['patches'][0]['value']='python tampered.py'
        self.store.execute('UPDATE company_training SET request=? WHERE id=?',(json.dumps(frozen),id))
        with self.assertRaisesRegex(ValueError,'工作包'):self.company.training.submit(self.sid,id,True)
        self.assertNotIn('submit',self.sdk_calls)
        deleted=json.loads(row['request']);deleted.pop('execution_snapshot')
        self.store.execute('UPDATE company_training SET request=? WHERE id=?',(json.dumps(deleted),id))
        with self.assertRaisesRegex(ValueError,'工作包'):self.company.training.submit(self.sid,id,True)
        self.assertNotIn('submit',self.sdk_calls)
        self.store.execute('UPDATE company_training SET request=? WHERE id=?',(row['request'],id))
        self.raw['lpai_task']['roles'][0]['replica']=2
        with self.assertRaisesRegex(ValueError,'执行快照'):self.company.training.submit(self.sid,id,True)
        self.assertNotIn('submit',self.sdk_calls)

    def test_stopped_predecessor_cannot_receive_automatic_retry_authority(self):
        request=self.request();self.authorize();self.phase='stopped'
        prepared=self.company.training.prepare(self.sid,request)
        self.assertIsNone(self.company.training.delegated_submission(self.sid,prepared['submission_id']))
        self.assertNotIn('submit',self.sdk_calls)

    def test_missing_sdk_execution_digest_does_not_downgrade_new_policy(self):
        request=self.request();self.authorize();request,path=self.patch_request(request)
        sdk=self.company.training.sdk
        def without_digest(operation,**kwargs):
            result=sdk(operation,**kwargs);result.pop('execution_digest',None);return result
        self.company.training.sdk=without_digest
        with self.assertRaisesRegex(ValueError,'执行快照'):self.company.training.prepare(self.sid,request)
        self.assertNotIn('submit',self.sdk_calls)

    def test_policy_cannot_expand_by_mutating_review_text(self):
        self.authorize()
        review=self.store.one('SELECT * FROM research_reviews LIMIT 1');authority=json.loads(review['authority'])
        authority['task_policy']['allowed_patch_paths'].append('/lpai_git/0/commit_id')
        self.store.execute('UPDATE research_reviews SET authority=? WHERE id=?',(json.dumps(authority),review['id']))
        with self.assertRaisesRegex(ValueError,'授权摘要'):self.research.resources(self.plan)

    def test_patch_hash_and_execution_are_derived_from_one_bounded_read(self):
        request=self.request();self.authorize();request,path=self.patch_request(request)
        original_bytes=path.read_bytes();replacement=b'[{"path":"/lpai_task/cmd","value":"python concurrent_edit.py"}]'
        original_open=Path.open;changed=[]
        class ConcurrentEdit:
            def __init__(self,stream):self.stream=stream
            def __enter__(self):return self.stream.__enter__()
            def __exit__(self,*args):
                result=self.stream.__exit__(*args)
                with original_open(path,'wb') as target:target.write(replacement)
                changed.append(True)
                return result
        def edit_after_read(target,*args,**kwargs):
            stream=original_open(target,*args,**kwargs)
            if target==path and (args[0] if args else kwargs.get('mode','r'))=='rb' and not changed:
                return ConcurrentEdit(stream)
            return stream
        with patch.object(Path,'open',edit_after_read):
            result=self.company.training.prepare(self.sid,request)
        frozen=json.loads(self.training_row(result['submission_id'])['request'])
        self.assertTrue(changed);self.assertEqual(path.read_bytes(),replacement)
        self.assertEqual(frozen['patch_sha256'],hashlib.sha256(original_bytes).hexdigest())
        self.assertEqual(frozen['patches'],json.loads(original_bytes.decode('utf-8')))

    def test_successor_inherits_policy_without_new_user_review_or_budget(self):
        self.authorize()
        review=self.store.add_record(self.sid,{'kind':'review','title':'fixture round assessment','content':'No cloud run; isolated scope inheritance.',
            'plan_id':self.plan['id'],'round_outcome':'continue','run_ids':[],'cloud_run_ids':[],'evidence':[]},{})
        status=self.research.autonomy.status(self.sid)
        successor=self.store.add_record(self.sid,{'kind':'plan','title':'fixture revised hypothesis','content':'Next route within original fixture scope.',
            'previous_plan_id':self.plan['id'],'review_record_id':review['id'],'authority_id':status['authority_id'],
            'scope_digest':status['scope_digest'],'evidence':[]},{})
        self.research.autonomy.adopt(self.sid,successor['id'],plan_digest(successor),'Evidence review retained; fixture only')
        resources=self.research.resources(successor)
        self.assertEqual(resources['execution_scope']['task_policy'],self.policy)
        self.assertEqual(resources['remaining_runs'],4);self.assertFalse(resources['budget_reset'])
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM research_reviews')['n'],1)
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM research_grants')['n'],1)

    def test_two_recovery_attempts_share_plan_authority_and_do_not_replay_after_reconstruction(self):
        from lab.company import Company
        request=self.request();self.authorize();request,path=self.patch_request(request)
        sdk=self.company.training.sdk
        def unique_uid(operation,**kwargs):
            result=sdk(operation,**kwargs)
            if operation=='submit':result['item']['uid']='new-task-'+str(self.sdk_calls.count('submit'))
            return result
        self.company.training.sdk=unique_uid
        first=self.company.training.prepare(self.sid,request);first_id=first['submission_id']
        authority=self.company.training.delegated_submission(self.sid,first_id)
        result=self.company.perform(self.sid,{'action':'lpai_submit','submission_id':first_id},'recovery-attempt-1',True,approval_source=authority)
        uid=result['item']['uid']
        self.store.execute("UPDATE company_training SET state='failed' WHERE id=?",(first_id,))
        self.store.execute("UPDATE company_watches SET state='failed',wake_state='finished' WHERE task_uid=?",(uid,))
        diagnostic=self.company.training.diagnose(self.sid,uid)
        request.update(predecessor_uid=uid,diagnostic_id=diagnostic['id'],change_summary='Second evidenced fixture repair')
        path.write_text('[{"path":"/lpai_task/cmd","value":"python second_fix.py"}]',encoding='utf-8')
        second=self.company.training.prepare(self.sid,request);second_id=second['submission_id']
        self.assertNotEqual(first_id,second_id)
        authority=self.company.training.delegated_submission(self.sid,second_id)
        result=self.company.perform(self.sid,{'action':'lpai_submit','submission_id':second_id},'recovery-attempt-2',True,approval_source=authority)
        self.assertEqual(result['state'],'submitted');self.assertEqual(self.sdk_calls.count('submit'),2)
        reconstructed=Company(self.store,runner=self.company.runner,locator=lambda name:name)
        self.addCleanup(reconstructed.close);reconstructed.training.sdk=unique_uid
        again=reconstructed.training.submit(self.sid,second_id,True)
        self.assertEqual(again['item']['uid'],result['item']['uid']);self.assertEqual(self.sdk_calls.count('submit'),2)
        self.assertEqual(self.research.resources(self.plan)['used_runs'],2)
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM research_reviews')['n'],1)
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM research_grants')['n'],1)


if __name__=='__main__':unittest.main()
