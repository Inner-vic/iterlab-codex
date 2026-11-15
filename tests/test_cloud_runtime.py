"""Producer and task scope tests; no real SDK, cloud, credentials or model."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lab import cloud_runtime,cloud_results,task_policy
from lab.result_receipt_writer import digest
from lab.cloud_runtime_worker import run


ROOT=Path(__file__).resolve().parents[1]


def contract():
    expected={'schema':'iterlab-cloud-result/v1','run_id':'a'*32,'plan_id':'plan','trial_id':'trial',
        'component_uid':'component','component_digest':'b'*64,'evaluation_contract':None,'protocols':[],
        'required_files':['metrics.json'],'runtime':{'command_path':'/lpai_task/cmd','input_root':'/workspace/source',
            'output_root':'/mnt/volumes/fixture/user/runs/'+'a'*32}}
    return {'expected':expected,'sha256':digest(expected)}


class RuntimeUnitTests(unittest.TestCase):
    def setUp(self):self.policy={'schema':1,'allowed_patch_paths':['/lpai_task/cmd']}

    def test_preparation_filename_is_not_a_component_json_pointer(self):
        spec=contract()['expected']['runtime']
        for invalid in ['implementation/wine_runner.py','/lpai_task/image','/lpai_task/roles/*/cmd']:
            with self.assertRaisesRegex(ValueError,'command_path.*JSON路径'):
                cloud_runtime.normalize({**spec,'command_path':invalid})
        self.assertEqual(cloud_runtime.normalize(spec)['command_path'],'/lpai_task/cmd')
        from lab.kernel import COMPANY_TOOLS
        output=next(t for t in COMPANY_TOOLS if t['name']=='lpai_prepare')['inputSchema']['properties']['output_contract']
        fields=output['properties']['runtime']['properties']
        self.assertIn('不是源码文件名',fields['command_path']['description'])
        self.assertIn('Linux绝对',fields['input_root']['description'])
        self.assertIn('必须同时提供source',output['description'])
        # Better feedback does not grant a different valid pointer.
        with self.assertRaisesRegex(ValueError,'授权'):
            cloud_runtime.authorize(self.policy,{**spec,'command_path':'/lpai_task/roles/0/cmd'})

    def test_native_tool_advertises_actual_work_package_mapping(self):
        from lab.kernel import COMPANY_TOOLS
        from lab.plan_envelope import validate
        tool=next(t for t in COMPANY_TOOLS if t['name']=='lpai_prepare')
        schema=tool['inputSchema']['properties']['output_contract']['properties']['runtime']
        runtime={'command_path':'/lpai_task/cmd','input_root':'/tmp/input','output_root':'/mnt/output',
            'work_files':['research/package/train.py=>implementation/train.py','research/package/manifest.json=>manifest.json']}
        validate(runtime,schema)
        parsed=cloud_runtime.normalize(runtime)
        self.assertEqual(parsed['stage_work_files']['research/package/train.py'],'implementation/train.py')
        for files in [runtime['work_files']*2,['implementation/file'+str(i)+'.py' for i in range(9)]]:
            with self.assertRaises(ValueError):validate({**runtime,'work_files':files},schema)

    def test_authorized_timeout_is_frozen_and_rejects_invalid_values(self):
        value=cloud_runtime.freeze(contract(),self.policy,timeout_seconds=180)
        self.assertEqual(value['timeout_seconds'],180)
        self.assertIn('python3 -c',cloud_runtime.apply({'lpai_task':{'cmd':'train'}},value)['lpai_task']['cmd'])
        for limit in [0,True,604801,'180']:
            with self.subTest(limit=limit),self.assertRaises(ValueError):cloud_runtime.freeze(contract(),self.policy,timeout_seconds=limit)

    def test_timeout_only_kills_owned_linux_training_group_and_returns_124(self):
        import subprocess
        from unittest.mock import Mock
        from lab.cloud_runtime_worker import bounded_training
        process=Mock();process.pid=1234;process.poll.return_value=None
        process.wait.side_effect=[subprocess.TimeoutExpired('training',1),-9]
        with patch('lab.cloud_runtime_worker.os.name','posix'),patch('lab.cloud_runtime_worker.LinuxChildren',return_value=None),patch('lab.cloud_runtime_worker.signal.SIGKILL',9,create=True),patch('lab.cloud_runtime_worker.subprocess.Popen',return_value=process) as start,patch('lab.cloud_runtime_worker.os.killpg',create=True) as kill:
            self.assertEqual(bounded_training('train',{'SCOPE':'fixture'},1),124)
        self.assertTrue(start.call_args.kwargs['start_new_session']);kill.assert_called_once()
        self.assertEqual(kill.call_args.args[0],1234)

    def test_identical_global_and_single_role_entries_require_explicit_mirror_authority(self):
        value=contract();value['expected']['runtime']['mirror_paths']=['/lpai_task/roles/0/cmd'];value['sha256']=digest(value['expected'])
        with self.assertRaisesRegex(ValueError,'授权'):cloud_runtime.freeze(value,self.policy)
        policy={'schema':1,'allowed_patch_paths':['/lpai_task/cmd','/lpai_task/roles/0/cmd']}
        runtime=cloud_runtime.freeze(value,policy)
        raw={'lpai_task':{'cmd':'python train.py','roles':[{'replica':1,'cmd':'python train.py'}]}}
        changed=cloud_runtime.apply(raw,runtime)
        self.assertEqual(changed['lpai_task']['cmd'],changed['lpai_task']['roles'][0]['cmd'])
        self.assertNotEqual(raw,changed)
        wrong=copy.deepcopy(raw);wrong['lpai_task']['roles'][0]['cmd']='different'
        with self.assertRaisesRegex(ValueError,'不一致'):cloud_runtime.apply(wrong,runtime)
        wrong=copy.deepcopy(raw);wrong['lpai_task']['roles'][0]['replica']=2
        with self.assertRaisesRegex(ValueError,'单角色'):cloud_runtime.apply(wrong,runtime)
        for mirrors in [[],['/lpai_task/image'],['/lpai_task/cmd']]:
            with self.subTest(mirrors=mirrors),self.assertRaises(ValueError):cloud_runtime.normalize({**value['expected']['runtime'],'mirror_paths':mirrors})

    def test_compiler_wraps_actual_command_keeps_component_unchanged_and_freezes_digest(self):
        runtime=cloud_runtime.freeze(contract(),self.policy)
        raw={'lpai_task':{'cmd':'python train.py','roles':[{'replica':1,'resource':{'Gpu':0}}]}}
        changed=cloud_runtime.apply(raw,runtime)
        self.assertEqual(raw['lpai_task']['cmd'],'python train.py');self.assertTrue(changed['lpai_task']['cmd'].startswith('python3 -c '))
        self.assertEqual(changed,cloud_runtime.apply(raw,runtime))
        component={'digest':'b'*64,'execution_digest':task_policy.fingerprint(changed)}
        snapshot=task_policy.freeze(self.policy,component,[],None,runtime)
        frozen={'component':component,'patches':[],'patch_sha256':None,'execution_snapshot':snapshot,'result_runtime':runtime,'output_contract':contract()}
        self.assertEqual(task_policy.verify(self.policy,frozen),snapshot)
        broken=copy.deepcopy(frozen);broken['result_runtime']['writer']+='\n# changed'
        with self.assertRaises(ValueError):task_policy.verify(self.policy,broken)
        with self.assertRaisesRegex(ValueError,'版本'):cloud_runtime.apply(raw,broken['result_runtime'])

    def test_unauthorized_entry_and_multi_replica_do_not_get_wrapped(self):
        with self.assertRaisesRegex(ValueError,'授权'):cloud_runtime.freeze(contract(),None)
        runtime=cloud_runtime.freeze(contract(),self.policy)
        with self.assertRaisesRegex(ValueError,'角色'):cloud_runtime.apply({'lpai_task':{'cmd':'train','roles':[{'replica':2}]}},runtime)
        for path in ['/mnt/result/../shared','/mnt/result;curl','/mnt/$HOME','/mnt/results//nested']:
            with self.subTest(path=path),self.assertRaises(ValueError):cloud_runtime.root_path(path)

    def test_source_is_exact_unique_run_prefix_never_shared_root(self):
        source={'kind':'volume','name':'fixture','uid':'original-volume','prefix':'user/runs/'+'a'*32,'mount_root':'/mnt/volumes/fixture','run_id':'a'*32}
        self.assertEqual(cloud_runtime.result_source(source),source)
        for change in [{'prefix':'user/runs'},{'run_id':'wrong'},{'prefix':'../other/'+'a'*32},{'prefix':'user/private/token/'+'a'*32}]:
            with self.subTest(change=change),self.assertRaises(ValueError):cloud_runtime.result_source({**source,**change})

    def test_worker_preserves_training_failure_and_publishes_only_after_actual_results(self):
        for state in ['success','failure','missing']:
            with self.subTest(state=state),tempfile.TemporaryDirectory(prefix='iterlab-producer-') as temp:
                inputs=Path(temp)/'inputs';inputs.mkdir();outputs=Path(temp)/'isolated-run'
                value=contract();value['expected']['runtime'].update(input_root=str(inputs),output_root=str(outputs));value['sha256']=digest(value['expected'])
                payload={'contract':value,'command':'fixture training','writer':(ROOT/'lab/result_receipt_writer.py').read_text(encoding='utf-8')}
                def training(*args,**kwargs):
                    self.assertEqual(kwargs['env']['ITERLAB_RUN_ID'],'a'*32)
                    self.assertEqual(Path(kwargs['env']['ITERLAB_OUTPUT_DIR']),outputs)
                    if state=='success':(outputs/'metrics.json').write_text('{"accuracy":0.8}')
                    return type('Result',(),{'returncode':7 if state=='failure' else 0})()
                with patch('lab.cloud_runtime_worker.subprocess.run',side_effect=training):code=run(payload)
                self.assertEqual(code,0 if state=='success' else 7 if state=='failure' else 117)
                self.assertEqual((outputs/'iterlab-run.json').exists(),state=='success')
                self.assertEqual(json.loads((outputs/'iterlab-runtime.json').read_text())['exit_code'],code)
                if state=='missing':
                    outcome=json.loads((outputs/'iterlab-runtime.json').read_text())
                    self.assertEqual(outcome['failure_stage'],'result_publication')
                    self.assertEqual(outcome['error_type'],'ValueError')
                with patch('lab.cloud_runtime_worker.subprocess.run',side_effect=AssertionError('must not retrain')):self.assertEqual(run(payload),116)

    def test_changed_protocol_fails_before_training(self):
        with tempfile.TemporaryDirectory() as temp:
            inputs=Path(temp)/'inputs';inputs.mkdir();(inputs/'split.json').write_text('wrong')
            value=contract();value['expected']['runtime'].update(input_root=str(inputs),output_root=str(Path(temp)/'outputs'))
            value['expected']['protocols']=[{'path':'split.json','sha256':'f'*64}];value['sha256']=digest(value['expected'])
            payload={'contract':value,'command':'fixture','writer':(ROOT/'lab/result_receipt_writer.py').read_text(encoding='utf-8')}
            with patch('lab.cloud_runtime_worker.subprocess.run',side_effect=AssertionError('protocol changed')):self.assertEqual(run(payload),116)
            self.assertFalse((Path(temp)/'outputs').exists())

    def test_worker_uses_owned_directory_without_publishing_master_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            inputs=Path(temp)/'inputs';outputs=Path(temp)/'outputs'
            value=contract();value['expected']['runtime'].update(input_root=str(inputs),output_root=str(outputs))
            value['sha256']=digest(value['expected'])
            payload={'contract':value,'command':'fixture worker','worker_only':True,
                     'writer':(ROOT/'lab/result_receipt_writer.py').read_text(encoding='utf-8')}
            with patch('socket.gethostname',return_value='worker-a'),patch('lab.cloud_runtime_worker.subprocess.run',return_value=type('Result',(),{'returncode':0})()):
                self.assertEqual(run(payload),0)
            outcomes=list((Path(temp)/'outputs-workers').glob('*/iterlab-runtime.json'))
            self.assertEqual(len(outcomes),1)
            self.assertEqual(json.loads(outcomes[0].read_text())['state'],'training_completed')
            self.assertFalse(list(Path(temp).rglob('iterlab-run.json')))

    def test_training_cannot_publish_after_rewriting_its_frozen_work_file(self):
        import hashlib
        with tempfile.TemporaryDirectory() as temp:
            inputs=Path(temp)/'inputs';src=inputs/'implementation/train.py';src.parent.mkdir(parents=True);src.write_bytes(b'original')
            output=Path(temp)/'outputs';value=contract();expected=value['expected']
            expected['runtime'].update(input_root=str(inputs),output_root=str(output),work_files=['implementation/train.py'])
            expected['execution_files']=[{'path':'implementation/train.py','sha256':hashlib.sha256(b'original').hexdigest(),'size':8}]
            value['sha256']=digest(expected)
            payload={'contract':value,'command':'fixture train','writer':(ROOT/'lab/result_receipt_writer.py').read_text(encoding='utf-8')}
            def mutate(*args,**kwargs):
                src.write_bytes(b'changed');(output/'metrics.json').write_text('{"accuracy":1}')
                return type('Result',(),{'returncode':0})()
            with patch('lab.cloud_runtime_worker.subprocess.run',side_effect=mutate):self.assertEqual(run(payload),117)
            self.assertFalse((output/'iterlab-run.json').exists())
            self.assertEqual(json.loads((output/'iterlab-runtime.json').read_text())['state'],'results_incomplete')

    def test_isolated_trial_config_stages_under_frozen_entrypoint_without_overwrite(self):
        import base64,hashlib
        source='implementation/seed7.json';target='implementation/config.json';data=b'{"seed":7}'
        value=contract();expected=value['expected']
        spec=expected['runtime'];spec.update(work_files=[source],stage_work_files={source:target})
        self.assertEqual(cloud_runtime.normalize(spec)['stage_work_files'],{source:target})
        legacy={k:v for k,v in spec.items() if k!='stage_work_files'}|{'work_files':[source+'=>'+target]}
        self.assertEqual(cloud_runtime.normalize(legacy),cloud_runtime.normalize(spec))
        with self.assertRaises(ValueError):cloud_runtime.normalize({**legacy,'stage_work_files':{source:target}})
        for paths,mapping in [([source,target],{source:target}),([source],{source:'implementation/train.py'}),
                              ([source],{'implementation/missing.json':target})]:
            with self.assertRaises(ValueError):cloud_runtime.normalize({**spec,'work_files':paths,'stage_work_files':mapping})
        with tempfile.TemporaryDirectory() as temp:
            inputs=Path(temp)/'inputs';outputs=Path(temp)/'outputs'
            spec.update(input_root=str(inputs),output_root=str(outputs))
            ref={'path':source,'sha256':hashlib.sha256(data).hexdigest(),'size':len(data)}
            expected['execution_files']=[ref];value['sha256']=digest(expected)
            payload={'contract':value,'command':'fixture','writer':(ROOT/'lab/result_receipt_writer.py').read_text(encoding='utf-8'),
                     'inputs':{source:base64.b64encode(data).decode()}}
            def train(*args,**kwargs):
                self.assertEqual((inputs/target).read_bytes(),data)
                self.assertFalse((inputs/source).exists())
                (outputs/'metrics.json').write_text('{}')
                return type('Result',(),{'returncode':0})()
            with patch('lab.cloud_runtime_worker.subprocess.run',side_effect=train):self.assertEqual(run(payload),0)
            self.assertEqual(json.loads((outputs/'iterlab-run.json').read_text())['execution_files'],{source:ref['sha256']})

    def test_cpu_summary_is_not_compared_to_gpu_total_and_conflicts_still_reject(self):
        from lab.lpai_evidence import gpu_count
        role={'replica':1,'resource':{'Cpu':1,'cpu':0,'Gpu':0,'gpu':0,'Mem':4}}
        self.assertEqual(gpu_count([role],{'resource_type':'CPU','num':1}),0)
        for change in [{'num':2},{'num':True}]:
            with self.subTest(change=change),self.assertRaises(ValueError):gpu_count([role],{'resource_type':'CPU','num':1,**change})
        with self.assertRaises(ValueError):gpu_count([{**role,'resource':{**role['resource'],'Gpu':1}}],{'resource_type':'CPU','num':1})

    def test_subdirectory_package_freezes_new_bytes_and_stages_explicit_destinations(self):
        import base64,hashlib
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'project';root.mkdir()
            old=root/'implementation/train.py';old.parent.mkdir();old.write_bytes(b'old CPU code')
            contents={'research/gpu/implementation/train.py':b'new GPU code',
                      'research/gpu/config-manifest.json':b'{"variant":"gpu"}'}
            mapping={list(contents)[0]:'implementation/train.py',list(contents)[1]:'config-manifest.json'}
            for name,body in contents.items():
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(body)
            store=SimpleNamespace(safe_path=lambda pid,name:root/name,evidence_path=lambda pid,name:root/name)
            value=contract();spec=value['expected']['runtime']
            spec.update(work_files=[name+'=>'+mapping[name] for name in contents])
            normalized=cloud_runtime.normalize(spec)
            self.assertEqual(normalized['stage_work_files'],mapping)
            self.assertEqual(cloud_runtime.normalize(normalized),normalized)
            declaration={'required_files':['metrics.json'],'runtime':{**spec,'output_root':'/mnt/fixture/source'},'source':{
                'kind':'volume','name':'fixture','uid':'fixture-volume','mount_root':'/mnt/fixture', 'prefix':'source'}}
            # Use the actual host resolver; a bare implementation/train.py would
            # select the old file above. Explicit source bytes must win.
            refs=cloud_results.execution_files(store,'project',declaration)
            self.assertEqual({ref['path']:ref['sha256'] for ref in refs},
                             {name:hashlib.sha256(body).hexdigest() for name,body in contents.items()})
            inputs=Path(temp)/'staged';outputs=Path(temp)/'results'
            value['expected']['runtime']=normalized
            value['expected']['execution_files']=refs;value['sha256']=digest(value['expected'])
            frozen=cloud_runtime.freeze(value,self.policy,store,'project')
            self.assertEqual({name:base64.b64decode(body) for name,body in frozen['inputs'].items()},contents)
            # Host declarations use Linux paths; the worker fixture uses an
            # owned local temporary directory without executing training.
            value['expected']['runtime']={**normalized,'input_root':str(inputs),'output_root':str(outputs)}
            value['sha256']=digest(value['expected'])
            def train(*args,**kwargs):
                self.assertEqual((inputs/'implementation/train.py').read_bytes(),b'new GPU code')
                self.assertEqual((inputs/'config-manifest.json').read_bytes(),contents[list(contents)[1]])
                self.assertFalse((inputs/'research').exists())
                (outputs/'metrics.json').write_text('{}')
                return type('Result',(),{'returncode':0})()
            with patch('lab.cloud_runtime_worker.subprocess.run',side_effect=train):self.assertEqual(run({**frozen,'command':'fixture'}),0)
            self.assertEqual(old.read_bytes(),b'old CPU code')
            receipt=json.loads((outputs/'iterlab-run.json').read_text())
            self.assertEqual(receipt['execution_files'],{ref['path']:ref['sha256'] for ref in refs})

    def test_subdirectory_mapping_keeps_path_type_count_and_protocol_boundaries(self):
        spec=contract()['expected']['runtime'];src='research/gpu/train.py'
        valid={**spec,'work_files':[src+'=>implementation/train.py']}
        self.assertEqual(cloud_runtime.normalize(valid)['work_files'],[src])
        for name in ['research/.hidden/train.py','research/../train.py','research/gpu//train.py','research/gpu/train.exe']:
            with self.subTest(name=name),self.assertRaises(ValueError):
                cloud_runtime.normalize({**spec,'work_files':[name+'=>implementation/train.py']})
        for target in ['train.py','implementation/train.json','../train.py','implementation/.hidden.py']:
            with self.subTest(target=target),self.assertRaises(ValueError):
                cloud_runtime.normalize({**spec,'work_files':[src+'=>'+target]})
        with self.assertRaises(ValueError):cloud_runtime.normalize({**spec,'work_files':[src]})
        with self.assertRaises(ValueError):cloud_runtime.normalize({**spec,'work_files':[
            src+'=>implementation/train.py','research/another/train.py=>implementation/train.py']})
        eight=[f'research/gpu/file{i}.json=>implementation/file{i}.json' for i in range(8)]
        self.assertEqual(len(cloud_runtime.normalize({**spec,'work_files':eight})['work_files']),8)
        with self.assertRaises(ValueError):cloud_runtime.normalize({**spec,'work_files':eight+['research/gpu/extra.json=>extra.json']})
        value=contract()['expected'];value['runtime']=cloud_runtime.normalize({**spec,'work_files':['research/gpu/config.json=>split.json']})
        value['protocols']=[{'path':'split.json','sha256':'f'*64}]
        value['execution_files']=[{'path':'research/gpu/config.json','sha256':'e'*64}]
        with self.assertRaisesRegex(ValueError,'冲突'):cloud_runtime.input_refs(value)

    def test_sdk_empty_list_envelope_is_distinct_from_unknown(self):
        from scripts.lpai_sdk_bridge import project_items
        self.assertEqual(project_items({'items':None,'total':0}),[])
        for response in [{'items':None,'total':1},{'items':None},{'items':None,'total':False},None]:
            with self.subTest(response=response),self.assertRaises(ValueError):project_items(response)

    def test_structured_error_logs_are_not_erased_as_environment_assignments(self):
        from lab.lpai_evidence import scrub
        line='time=2026-11-03T03:44:48Z level=error msg="cannot start training" token=PRIVATE'
        redacted=scrub(line)
        self.assertIn('cannot start training',redacted);self.assertNotIn('PRIVATE',redacted)
        self.assertEqual(scrub('CUSTOM_VARIABLE=private value'),'CUSTOM_VARIABLE=[environment value omitted]')


if __name__=='__main__':unittest.main()
