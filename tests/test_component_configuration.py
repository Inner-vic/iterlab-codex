import copy
import json
import unittest
from unittest.mock import patch

from lab.component_catalog import queues
from lab.component_configuration import build,verify_component,ConfigurationError
from lab import cloud_runtime
from lab.result_receipt_writer import digest
from tests.test_cloud_runtime import contract
from tests.test_component_provision import ComponentProvisionTests
from lab.research import plan_digest
from pathlib import Path


def directory():
    return {'live':True,'account_scope':{'subject':'a'*64,'namespace':'vc-chassis','connection_revision':1},
        'queues':[{'key':'queue-a','name':'Account training','queue_code':'queue-code','cluster':'cluster-a','business':['train'],
        'packages':[{'id':41,'name':'eight GPU','gpu_count':8,'gpu_type':'gpu/A800','cpu':104,'memory_gib':872,'selectable':True,'projects':[{'code':'billing','name':'Research'}]}]}]}


def recipe(workers=1):
    return {'schema':1,'account_scope':directory()['account_scope'],'queue_key':'queue-a','package_id':41,'workers':workers,
        'packages_per_worker':1,'image':{'uid':'image-id','version':'cuda12'},
        'volumes':[{'uid':'volume-uid','name':'fixture-volume','mount_path':'/mnt/volumes/fixture-volume'}],
        'datasets':[{'uid':'data-id','name':'data','version':'v1','mount_path':'/mnt/data'}],
        'output_volume_uid':'volume-uid','command':'python train.py','training_parameters':{'batch_size':32},
        'rationale':'Measured memory requires this shape; compare fixed split.','compatibility':'Verified framework API and distributed entry from code.'}


def asset(kind,uid,version=None):
    name={'image-id':'registry.example/train','volume-uid':'fixture-volume','data-id':'data'}[uid]
    return {'subject':'a'*64,'namespace':'vc-chassis','item':{'uid':uid if not version else 'version-id','name':version or name,
        'spec':{},'labels':{},'kind':kind,'namespace':None}}


def raw_component(template,uid='new-component'):
    config=template['config'];spec=template['package']
    return {'uid':uid,'namespace':'vc-chassis','name':config['name'],'cluster':'cluster-a','queue_code':'queue-code',
        'framework_type':config['framework'],'gems_project':config['gems_project'],'data':{'lpai_task':{'cmd':config['cmd'],'roles':[
            {'role':r['rolename'],'replica':int(r['replica']),'cmd':config['cmd'],'image_info':{'image':config['image']},
            'resource':{'Id':41,'Gpu':8,'GpuType':'gpu/A800','Cpu':104,'Mem':872}} for r in config['task_role_specs']]},
            'lpai_input_pvc':config['input_data_volume'],'lpai_input_dataset':config['input_dataset']}}


class ConfigurationTests(unittest.TestCase):
    def test_single_node_gpu_has_master_without_adding_a_worker_or_gpu(self):
        config=build(recipe(),directory(),asset,'unique','vc-chassis')
        roles=config['config']['task_role_specs']
        self.assertEqual(config['config']['framework'],'pytorch')
        self.assertEqual(roles,[{'rolename':'master','replica':'1','resource':{'id':41,'replica':1}}])
        self.assertEqual(config['gpu_count'],8)
        raw=raw_component(config)
        self.assertEqual(verify_component(raw,config)['gpu_count'],8)
        raw['data']['lpai_task']['roles'][0]['role']='worker'
        with self.assertRaisesRegex(ValueError,'单worker配置'):
            verify_component(raw,config)

    def test_single_cpu_custom_template_keeps_its_worker_role(self):
        catalog=directory()
        catalog['queues'][0]['packages'][0].update(gpu_count=0,gpu_type='')
        config=build(recipe(),catalog,asset,'cpu','vc-chassis')
        self.assertEqual(config['config']['framework'],'custom')
        self.assertEqual(config['config']['task_role_specs'][0]['rolename'],'worker')
        self.assertEqual(config['gpu_count'],0)

    def test_structured_compatibility_is_bounded_explanation_not_executable_config(self):
        original=recipe();structured={**original,'compatibility':{'python':'3.10.12','cuda':'12.1','pytorch':'2.3.1',
            'notes':'Runtime CUDA allocation and model devices remain unverified until the task runs.'}}
        preserved=copy.deepcopy(structured)
        baseline=build(original,directory(),asset,'unique','vc-chassis')
        actual=build(structured,directory(),asset,'unique','vc-chassis')
        # The component description intentionally binds the exact recipe hash;
        # metadata changes must preserve that identity difference, while all
        # executable/resource fields remain identical.
        self.assertEqual({k:v for k,v in actual['config'].items() if k!='describe'},
                         {k:v for k,v in baseline['config'].items() if k!='describe'})
        self.assertNotEqual(actual['config']['describe'],baseline['config']['describe'])
        self.assertNotEqual(actual['config_sha256'],baseline['config_sha256'])
        self.assertEqual(actual['recipe'],preserved)
        self.assertEqual(structured,preserved)
        self.assertIn('cuda: 12.1',actual['software_compatibility'])
        self.assertIn('remain unverified',actual['software_compatibility'])
        self.assertEqual(actual['compatibility_verified'],'asset_identity_and_cluster_only')
        for bad in [{},{'cuda':12.1},{'notes':{}},{'notes':''},{'bad/path':'value'},
                    {'notes':'x'*8001},['CUDA'],True]:
            with self.subTest(value_type=type(bad).__name__),self.assertRaises(ConfigurationError):
                build({**original,'compatibility':bad},directory(),asset,'unique','vc-chassis')

    def test_32_gpu_is_four_nodes_and_readback_checks_actual_resources(self):
        config=build(recipe(4),directory(),asset,'unique','vc-chassis')
        self.assertEqual(config['gpu_count'],32)
        self.assertEqual([r['replica'] for r in config['config']['task_role_specs']],['1','3'])
        self.assertEqual(config['config']['image'],'registry.example/train:cuda12')
        raw=raw_component(config);self.assertEqual(verify_component(raw,config)['gpu_count'],32)
        for mutate in [lambda r:r.update(queue_code='other'),lambda r:r['data']['lpai_task']['roles'][1]['resource'].update(Gpu=1),
            lambda r:r['data']['lpai_task']['roles'][0]['image_info'].update(image='other'),lambda r:r['data'].update(lpai_input_pvc=[])]:
            wrong=copy.deepcopy(raw);mutate(wrong)
            with self.assertRaises(ValueError):verify_component(wrong,config)

    def test_account_asset_and_package_mismatches_do_not_produce_configuration(self):
        for change in [{'account_scope':{**directory()['account_scope'],'subject':'b'*64}}, {'package_id':999},
            {'packages_per_worker':4},{'output_volume_uid':'unknown'},{'image':{'uid':'image-id','version':''}}]:
            with self.subTest(change=change),self.assertRaises(ValueError):build({**recipe(),**change},directory(),asset,'unique','vc-chassis')
        with self.assertRaisesRegex(ValueError,'账号不同'):
            build(recipe(),directory(),lambda *args:{**asset(*args),'subject':'b'*64},'unique','vc-chassis')

    def test_model_gets_actionable_own_validation_but_never_sdk_exception_body(self):
        with self.assertRaisesRegex(ConfigurationError,'账号已变化'):
            build({**recipe(),'account_scope':{}},directory(),asset,'unique','vc-chassis')
        def remote_error(*args):raise ValueError('raw private SDK diagnostic')
        try:build(recipe(),directory(),remote_error,'unique','vc-chassis')
        except ValueError as exc:self.assertNotIsInstance(exc,ConfigurationError)
        else:self.fail('remote error should propagate without public validation classification')

    def test_live_queue_projection_has_no_global_or_other_namespace_resources(self):
        spec=[{'Id':41,'Name':'eight GPU','Cpu':104,'Mem':872,'Gpu':8,'GpuType':'gpu/A800'}]
        raw=[{'status':'enable','code':'q','cluster':'c','nickname':'training','business':['train'],
            'package_info':[{'package_code':41}],'resource':{'GPU':{'gpu/A800':{'resource':{'guarantee_remain':16},
            'namespace':{'scope':{'guarantee_remain':32,'guarantee':64},'other':{'guarantee_remain':999}}}}}}]
        value=queues(raw,spec+[{'Id':9,'Name':'foreign','Gpu':1,'Cpu':1,'Mem':1}], 'scope')
        self.assertEqual(value[0]['resources'][0]['available'],16);self.assertEqual(len(value[0]['packages']),1)
        self.assertNotIn('other',json.dumps(value));self.assertNotIn('999',json.dumps(value))
        raw[0]['resource']['GPU']['gpu/A800']['resource']['guarantee_remain']=0
        self.assertEqual(len(queues(raw,spec,'scope')),1) # Permission is not current free capacity.

    def test_distributed_runtime_has_one_publisher_and_separate_worker_payload(self):
        config=build(recipe(4),directory(),asset,'unique','vc-chassis');raw=raw_component(config)['data']
        # Provisioned templates deliberately contain a placeholder. A real
        # authorized entry patch precedes runtime wrapping in production.
        raw['lpai_task']['cmd']='python train.py'
        for role in raw['lpai_task']['roles']:role['cmd']='python train.py'
        value=contract();value['expected']['runtime']['mirror_paths']=['/lpai_task/roles/0/cmd'];value['sha256']=digest(value['expected'])
        runtime=cloud_runtime.freeze(value,{'schema':1,'allowed_patch_paths':['/lpai_task/cmd','/lpai_task/roles/0/cmd','/lpai_task/roles/1/cmd']})
        changed=cloud_runtime.apply(raw,runtime)['lpai_task']
        self.assertEqual(changed['cmd'],changed['roles'][0]['cmd']);self.assertNotEqual(changed['roles'][0]['cmd'],changed['roles'][1]['cmd'])
        raw['lpai_task']['roles'][1]['cmd']='other configuration'
        with self.assertRaisesRegex(ValueError,'不一致'):cloud_runtime.apply(raw,runtime)

    def test_worker_outputs_cannot_precreate_or_overwrite_master_result_directory(self):
        import tempfile
        import types
        from lab.cloud_runtime_worker import run
        with tempfile.TemporaryDirectory(prefix='distributed-results-') as tmp:
            value=contract();value['expected']['runtime']['output_root']=tmp.replace('\\','/')+'/result'
            value['expected']['runtime']['input_root']=tmp.replace('\\','/')+'/inputs';value['sha256']=digest(value['expected'])
            # Build payload directly: this tests the portable writer isolation,
            # not a production path whitelist or a distributed CUDA experiment.
            payload={'contract':value,'writer':Path('lab/result_receipt_writer.py').read_text(encoding='utf-8'),
                'inputs':{},'command':'fixture training'}
            def training(*args,**kw):
                root=Path(kw['env']['ITERLAB_OUTPUT_DIR']);(root/'metrics.json').write_text('{"score":0.5}')
                return types.SimpleNamespace(returncode=0)
            with patch('lab.cloud_runtime_worker.subprocess.run',side_effect=training):
                self.assertEqual(run({**payload,'worker_only':True}),0)
                self.assertFalse((Path(tmp)/'result').exists())
                self.assertEqual(run(payload),0)
            self.assertTrue((Path(tmp)/'result'/'metrics.json').is_file())
            self.assertEqual(len(list(Path(tmp).rglob('iterlab-run.json'))),1)


class GPUProvisionTests(unittest.TestCase):
    sdk=ComponentProvisionTests.sdk
    request=ComponentProvisionTests.request
    def setUp(self):
        ComponentProvisionTests.setUp(self);self.template=None;self.legacy_sdk=self.company.training.sdk
        data=recipe();self.volume='fixture-volume';self.mount='/mnt/volumes/'+self.volume
        self.volumes=[{'name':self.volume,'env_key':'LPAI_INPUT_DATA_0','env_value':self.mount}]
        (Path(self.project['workspace'])/'run-config.json').write_text(json.dumps(data),encoding='utf-8')
        self.plan=self.store.add_record(self.sid,{'kind':'plan','title':'GPU configuration','content':'Use measured resource configuration',
            'evidence':['run-config.json'],'scope_proposal':{'compute':{'gpu_count':8,'workflow':'batch'},'component_config':'run-config.json'}},{})
        self.trial=self.store.add_record(self.sid,{'kind':'trial','title':'GPU baseline','content':'Run fixed baseline','plan_id':self.plan['id']},{})
        def sdk(operation,**kw):
            if operation in {'configured_template','provision_configured','verify_configured'}:
                self.sdk_calls.append(operation)
                if operation=='configured_template':
                    self.name=kw['component_name'];self.template=build(data,directory(),asset,self.name,'vc-chassis');return self.template
                if operation=='provision_configured':
                    if self.ambiguous:raise TimeoutError('lost response')
                    return {'state':'created','uid':self.component}
                return verify_component(raw_component(self.template),self.template)
            result=self.legacy_sdk(operation,**kw)
            if operation=='component':
                from lab import task_policy
                from lab.lpai_evidence import apply_code_patches
                result['gpu_count']=8;result['digest']=verify_component(raw_component(self.template),self.template)['digest']
                changed=cloud_runtime.apply(apply_code_patches(self.raw,kw.get('patches',[])),kw.get('result_runtime'))
                result['execution_digest']=task_policy.fingerprint({'component_digest':result['digest'],'data':changed})
            return result
        self.company.training.sdk=sdk

    def approval(self,preview):
        return {**ComponentProvisionTests.approval(self,preview),'max_gpus':8}

    def test_gpu_confirm_creates_and_prepares_exact_component_without_local_fallback(self):
        preview=self.company.training.preview_component(self.sid,self.plan)
        self.assertEqual(preview['gpu_count'],8);self.assertEqual(preview['training_parameters']['batch_size'],32)
        self.research.review(self.sid,self.approval(preview))
        self.assertNotIn('provision_configured',self.sdk_calls)
        prepared=self.company.training.prepare(self.sid,self.request())
        self.assertEqual(self.store.one('SELECT max_gpus FROM research_cloud_scopes')['max_gpus'],8)
        self.assertEqual(self.sdk_calls.count('provision_configured'),1)
        self.company.training.prepare(self.sid,self.request());self.assertEqual(self.sdk_calls.count('provision_configured'),1)
        self.assertFalse(self.store.rows('SELECT * FROM research_runs'))
        authority=self.company.training.delegated_submission(self.sid,prepared['submission_id'])
        submitted=self.company.perform(self.sid,{'action':'lpai_submit','submission_id':prepared['submission_id']},'gpu-fixture-submit',True,approval_source=authority)
        self.assertEqual(submitted['state'],'submitted');self.assertEqual(self.sdk_calls.count('submit'),1)

    def test_lost_gpu_creation_is_reconciled_without_duplicate_creation(self):
        preview=self.company.training.preview_component(self.sid,self.plan)
        self.research.review(self.sid,self.approval(preview));self.ambiguous=True
        with self.assertRaisesRegex(ValueError,'未知'):self.company.training.prepare(self.sid,self.request())
        self.assertEqual(self.store.one('SELECT state FROM research_component_provisions')['state'],'unknown')
        with self.assertRaisesRegex(ValueError,'尚未查到'):self.company.training.prepare(self.sid,self.request())
        self.visible=True;self.company.training.prepare(self.sid,self.request())
        self.assertEqual(self.sdk_calls.count('provision_configured'),1)
        self.assertNotIn('submit',self.sdk_calls)

    def test_changed_approved_file_blocks_before_component_creation(self):
        preview=self.company.training.preview_component(self.sid,self.plan)
        (Path(self.project['workspace'])/'run-config.json').write_text('{}',encoding='utf-8')
        with self.assertRaises(ValueError):self.research.review(self.sid,self.approval(preview))
        self.assertFalse(self.store.rows('SELECT id FROM research_grants'));self.assertNotIn('provision_configured',self.sdk_calls)
