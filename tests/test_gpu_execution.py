"""Execution location is a grant, not a hint or a runtime fallback."""
import base64
import json
from pathlib import Path
from types import SimpleNamespace
import shutil
import sys
import tarfile
import unittest
from unittest.mock import Mock, patch
import uuid

from lab import devspace
from lab.company import Company
from lab.research import Research, plan_digest
from lab.store import Store


class GPUExecutionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]/'.evidence/tests'/uuid.uuid4().hex
        self.store = Store(self.root)
        self.addCleanup(shutil.rmtree, self.root)
        self.addCleanup(self.store.close)
        self.company = Company(self.store)
        self.research = Research(self.store, self.company)
        self.addCleanup(self.research.close)
        self.project = self.store.create_project('GPU route')
        self.sid = self.store.create_session(self.project['id'], 'ept')['id']
        self.plan = self.store.add_record(self.sid, {'kind':'plan','title':'GPU comparison','content':'Compare two regularizers.',
            'scope_proposal':{'compute':{'gpu_count':1,'workflow':'interactive'}}}, {})
        self.workspace = Path(self.project['workspace'])
        (self.workspace/'train.py').write_text("raise RuntimeError('must not run locally')", encoding='utf-8')
        self.request = {'plan_id':self.plan['id'],'script':'train.py','files':['train.py'],'purpose':'fit',
                        'question':'Does it generalize?','expected_information':'Compare held-out error','outputs':['metrics.json']}
        self.space = {'uid':'space-id','name':'gpu-test','namespace':'test-ns','gpus':1,'port':2222}

    def approve(self, **extra):
        return self.research.review(self.sid, {'plan_id':self.plan['id'],'expected_digest':plan_digest(self.plan),
            'decision':'accept','reason':'Exact test resource','execution':'lpai_devspace','allow_cloud':True,
            'space_uid':'space-id','ssh_port':2222,'max_runs':3,'max_fits':3,'timeout':5,**extra})

    def approve_space(self, configured=True):
        self.store.execute('INSERT INTO company_bindings VALUES(?,?,?,?,?,?,?)',
            (self.project['id'],'lpai','test-ns','space-id','gpu-test','{}','now'))
        if configured:
            self.store.execute('INSERT INTO company_space_ssh VALUES(?,?,?,?,?)',
                (self.project['id'],'test-ns','space-id',2222,'now'))
        with patch('lab.devspace.approval', return_value=self.space) as approve:
            self.approve()
        approve.assert_called_once_with(self.company,self.project['id'],'space-id',2222,1)
        self.assertEqual(self.research.resources(self.plan)['execution_scope']['devspace'], self.space)

    def test_gpu_plan_cannot_accept_local_or_cpu_auto_component(self):
        with self.assertRaisesRegex(ValueError, '不能在本地替代'):
            self.approve(execution='local',allow_local=True,python=sys.executable)
        with self.assertRaisesRegex(ValueError, '只有 CPU'):
            self.approve(execution='lpai',component_preview_id='not-a-gpu',max_gpus=0)
        self.assertFalse(self.store.rows('SELECT id FROM research_grants'))

    def test_legacy_plan_needs_explicit_approval_gpu_limit_not_zero_or_visible_count(self):
        plan=self.store.add_record(self.sid,{'kind':'plan','title':'legacy plan','content':'GPU pending review','evidence':[]},{})
        self.plan=plan
        with patch('lab.devspace.approval',return_value=self.space) as approval:
            with self.assertRaisesRegex(ValueError,'明确max_gpus'):self.approve()
            approval.assert_not_called()
            self.approve(max_gpus=1)
        approval.assert_called_once_with(self.company,self.project['id'],'space-id',2222,1)

    def test_ssh_peer_mismatch_sends_no_training_and_never_uses_local_python(self):
        self.approve_space()
        with patch('lab.devspace.connection',return_value=['ssh.exe','fixed-host']), \
                patch('lab.devspace.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=b'other-space')), \
                patch('lab.research.subprocess.Popen') as spawn:
            with self.assertRaisesRegex(ValueError, '未上传输入'):
                self.research.run(self.sid,self.request,'wrong-peer')
        self.assertFalse(self.store.rows('SELECT id FROM research_runs'))
        spawn.assert_not_called()

    def test_missing_ssh_configuration_does_not_reserve_training(self):
        self.approve_space(configured=False)
        with patch('lab.research.subprocess.Popen') as spawn:
            with self.assertRaisesRegex(ValueError, '配置并验证开发空间 SSH'):
                self.research.run(self.sid,self.request,'missing-ssh')
        spawn.assert_not_called()
        self.assertFalse(self.store.rows('SELECT id FROM research_runs'))

    def test_cuda_preflight_failure_precedes_attempt_and_remote_submit(self):
        self.approve_space()
        before=self.research.resources(self.plan)
        with patch('lab.devspace.connection',return_value=['ssh.exe','fixed-host']),patch('lab.devspace.check_peer'), \
             patch('lab.devspace.check_compute',side_effect=ValueError('GPU最小CUDA分配未通过；未扣任务次数')), \
             patch.object(self.research.remote,'launch') as launch,patch('lab.research.subprocess.Popen') as local:
            with self.assertRaisesRegex(ValueError,'未扣任务次数'):
                self.research.run(self.sid,self.request,'blocked-cuda')
        launch.assert_not_called();local.assert_not_called()
        self.assertEqual(self.store.rows('SELECT * FROM research_runs'),[])
        self.assertEqual(self.research.resources(self.plan)['used_runs'],before['used_runs'])
        self.assertEqual(self.research.resources(self.plan)['used_fits'],before['used_fits'])
        self.assertIsNotNone(self.store.one("SELECT * FROM events WHERE kind='research.environment_blocked'"))

    def test_project_ssh_verification_persists_only_verified_endpoint(self):
        self.approve_space(configured=False)
        item = {**self.space, 'phase':'running', 'resources':{'gpu':1}}
        with patch.object(self.company, 'lpai', return_value={'item':item}), \
                patch('lab.devspace.shutil.which', return_value='ssh.exe'), \
                patch('lab.devspace.subprocess.run') as probe:
            probe.return_value = SimpleNamespace(returncode=0, stdout=b'space-id\nGPU 0: Test\n')
            command = 'ssh -p 2222 root@' + devspace.HOST
            profile = devspace.configure(self.company,self.sid,'space-id',command)
            self.assertEqual(profile['port'],2222)
            self.assertEqual(devspace.configure(self.company,self.sid,'space-id',None)['port'],2222)
            probe.return_value = SimpleNamespace(returncode=0, stdout=b'space-id\n')
            with self.assertRaisesRegex(ValueError, 'GPU 数'):
                devspace.configure(self.company,self.sid,'space-id',command.replace('2222','2223'))
            self.assertEqual(devspace.approval(self.company,self.project['id'],'space-id',None,1)['port'],2222)
            self.assertEqual(probe.call_count,3)

    def test_official_ssh_options_never_override_host_key_policy(self):
        command='ssh -p 2222 root@'+devspace.HOST+' -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'
        self.assertEqual(devspace.command_port(command),2222)
        self.assertEqual(devspace.command_port('ssh -p 2222 root@'+devspace.HOST+' -t bash'),2222)
        for command in ['ssh -p 2222 root@other.example',
                        'ssh -p 2222 root@'+devspace.HOST+' ; echo bad',
                        'ssh -p 2222 root@'+devspace.HOST+' -o ProxyCommand=other',
                        'ssh -p 2222 root@'+devspace.HOST+' -t bash;evil',
                        'ssh -p 2222 -p 2223 root@'+devspace.HOST]:
            with self.assertRaises(ValueError):devspace.command_port(command)

    def test_pending_space_saves_endpoint_but_cannot_execute(self):
        self.approve_space(configured=False)
        item={**self.space,'phase':'pending','resources':{'gpu':1}}
        with patch.object(self.company,'lpai',return_value={'item':item}),patch('lab.devspace.subprocess.run') as probe:
            saved=devspace.configure(self.company,self.sid,'space-id','ssh -p 2222 root@'+devspace.HOST)
            self.assertEqual(saved['state'],'saved');self.assertFalse(saved['verified_at'])
            probe.assert_not_called()
        self.assertIsNone(devspace.ssh_profile(self.store,self.project['id'],devspace.binding(self.store,self.project['id'])))
        with self.assertRaisesRegex(ValueError,'配置并验证'):
            self.research.run(self.sid,self.request,'pending-endpoint')
        self.assertEqual(self.store.rows('SELECT * FROM research_runs'),[])

    def test_unknown_platform_gpu_allows_identity_probe_not_implicit_gpu_authorization(self):
        self.approve_space(configured=False)
        item={**self.space,'phase':'ready','resources':{'gpu':-1}}
        with patch.object(self.company,'lpai',return_value={'item':item}),patch('lab.devspace.shutil.which',return_value='ssh.exe'),patch('lab.devspace.subprocess.run') as probe:
            probe.return_value=SimpleNamespace(returncode=0,stdout=b'space-id\nGPU 0: Test\n')
            profile=devspace.configure(self.company,self.sid,'space-id','ssh -p 2222 root@'+devspace.HOST)
            self.assertEqual(profile['gpu_count'],1)
            with self.assertRaisesRegex(ValueError,'必须明确'):
                devspace.approval(self.company,self.project['id'],'space-id',None,None)
            scope=devspace.approval(self.company,self.project['id'],'space-id',None,1)
            self.assertEqual(scope['gpus'],1)
            self.assertEqual(scope['gpu_source'],'verified_ssh_visibility_with_explicit_plan_limit')
            with self.assertRaises(ValueError):devspace.approval(self.company,self.project['id'],'space-id',None,2)

    def test_only_official_endpoint_mapping_can_initialize_space_identity(self):
        self.approve_space(configured=False)
        endpoint={'host':devspace.HOST,'user':'root','port':2222,'source':'official_devspace_access'}
        item={**self.space,'phase':'ready','resources':{'gpu':1},'ssh':endpoint}
        self.store.execute('UPDATE company_bindings SET observation=?',(json.dumps(item),))
        with patch.object(self.company,'lpai',return_value={'item':item}),patch('lab.devspace.shutil.which',return_value='ssh.exe'),patch('lab.devspace.subprocess.run') as probe:
            probe.return_value=SimpleNamespace(returncode=0,stdout=b'space-id\nGPU 0: Test\n')
            result=devspace.configure(self.company,self.sid,'space-id','ssh -p 2222 root@'+devspace.HOST+' -o StrictHostKeyChecking=no')
            self.assertEqual(result['state'],'verified');self.assertEqual(probe.call_count,2)
            command=probe.call_args_list[0].args[0]
            self.assertIn('StrictHostKeyChecking=accept-new',command)
            self.assertNotIn('StrictHostKeyChecking=no',command)
            self.assertIn('space-id',command[-1])
            self.assertIn('StrictHostKeyChecking=yes',devspace.connection(self.company,self.project['id'],self.space))
            probe.reset_mock()
            devspace.initialize_marker(self.company,self.project['id'],{**self.space,'port':2223},['ssh.exe'])
            probe.assert_not_called()

    def test_official_access_projection_does_not_expose_key_or_forward_commands(self):
        from lab.company import platform_item
        raw={'metadata':{'uid':'space-id','name':'gpu-test','namespace':'test-ns'},'status':{'phase':'ready'},
             'spec':{'sshSecretName':'never-project','accesses':[{'targetPort':22,'address':'ssh -p 2222 root@'+devspace.HOST+' -o StrictHostKeyChecking=no','pubkeyName':'never-project'}]}}
        item=platform_item(raw,'test-ns','space')
        self.assertEqual(item['ssh']['port'],2222);self.assertNotIn('never-project',json.dumps(item))
        raw['spec']['accesses'][0]['address']+=' ; dangerous'
        self.assertNotIn('ssh',platform_item(raw,'test-ns','space'))

    def test_explicit_key_is_saved_only_after_verification_and_not_projected_to_model(self):
        self.approve_space(configured=False)
        key=self.root/'existing-key';key.write_text('fixture; not a real key')
        item={**self.space,'phase':'ready','resources':{'gpu':1}}
        with patch.object(self.company,'lpai',return_value={'item':item}),patch('lab.devspace.shutil.which',return_value='ssh.exe'), \
             patch('lab.devspace.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=b'space-id\nGPU 0: fixture\n')) as probe:
            devspace.configure(self.company,self.sid,'space-id','ssh -p 2222 root@'+devspace.HOST,str(key))
            self.assertIn(str(key.resolve()),probe.call_args.args[0])
            self.assertIn(str(key.resolve()),devspace.connection(self.company,self.project['id'],self.space))
            self.assertNotIn(str(key.resolve()),json.dumps(devspace.ssh_settings(self.store,self.project['id'],devspace.binding(self.store,self.project['id']))))
            other=self.root/'other-key';other.write_text('another fixture')
            probe.return_value=SimpleNamespace(returncode=255,stdout=b'',stderr=b'Permission denied (publickey)')
            with self.assertRaisesRegex(ValueError,'密钥认证'):
                devspace.configure(self.company,self.sid,'space-id',None,str(other))
            saved=self.store.one('SELECT identity_path FROM company_space_ssh_identities')['identity_path']
            self.assertEqual(saved,str(key.resolve()))
        self.assertFalse(self.store.rows('SELECT * FROM research_runs'))

    def test_endpoint_change_blocks_original_port_and_legacy_does_not_trust_new(self):
        self.approve_space()
        item={**self.space,'phase':'ready','resources':{'gpu':1}}
        with patch.object(self.company,'lpai',return_value={'item':item}),patch('lab.devspace.shutil.which',return_value='ssh.exe'):
            self.assertIn('StrictHostKeyChecking=yes',devspace.connection(self.company,self.project['id'],self.space,trust_new=True))
            self.store.execute('UPDATE company_bindings SET observation=? WHERE project_id=?',
                (json.dumps({'ssh':{'host':devspace.HOST,'user':'root','port':2223,'source':'official_devspace_access'}}),self.project['id']))
            with self.assertRaisesRegex(ValueError,'端口已变化'):
                devspace.connection(self.company,self.project['id'],self.space)

    def test_large_transfer_has_separate_bounded_clock_not_fixed_two_minutes(self):
        from lab.devspace_jobs import DevspaceJobs
        with patch.object(Path,'stat',return_value=SimpleNamespace(st_size=600*1024*1024)):
            limit=DevspaceJobs.upload_timeout(self.workspace)
        self.assertGreater(limit,120);self.assertLessEqual(limit,3600)
        with patch.object(Path,'stat',return_value=SimpleNamespace(st_size=10*1024*1024*1024)):
            self.assertLessEqual(DevspaceJobs.upload_timeout(self.workspace),3600)

    def test_remote_job_uses_frozen_snapshot_and_returns_project_evidence(self):
        self.approve_space()
        with self.assertRaisesRegex(ValueError, '不能覆盖'):
            self.research.run(self.sid,{**self.request,'execution':'local'},'local-override')

        def launch(args,directory,mode,run_id,scope,fingerprint):
            self.assertEqual(args,['ssh.exe','fixed-host']);self.assertEqual(mode,'submit')
            with tarfile.open(directory/'.iterlab-input.tar') as archive:
                spec=json.load(archive.extractfile('.iterlab-job.json'))
                self.assertEqual(archive.getnames(),['train.py','.iterlab-job.json','.iterlab-supervisor.py'])
            self.assertEqual(spec['gpus'],1)
            self.assertEqual(spec['space_uid'],'space-id')
            self.assertEqual(spec['timeout'],5)
            files={'metrics.json':base64.b64encode(b'{"loss":0.25}').decode(),'stdout.log':'','stderr.log':''}
            return {'run_id':run_id,'space_uid':scope['uid'],'job_digest':fingerprint,
                'state':'completed','exit_code':0,'files':files}

        with patch('lab.devspace.connection',return_value=['ssh.exe','fixed-host']), patch('lab.devspace.check_peer'), patch('lab.devspace.check_compute',return_value={'ready':True}), \
                patch.object(self.research.remote,'_transport',side_effect=launch) as spawn:
            receipt=self.research.run(self.sid,self.request,'remote-1')
            self.assertEqual(receipt['state'],'completed',receipt)
            self.assertEqual(self.research.run(self.sid,self.request,'remote-1'),receipt)
        spawn.assert_called_once()
        self.assertEqual(json.loads((self.workspace/receipt['directory']/'metrics.json').read_bytes()),{'loss':0.25})
        self.assertTrue(self.store.evidence.summary('local',receipt['run_id'])['analysis_ready'])
        from lab.budgets import cloud_assessment_ready
        self.assertTrue(cloud_assessment_ready(self.store,self.project['id'],self.plan['id']))
        proof=self.store.one("SELECT manifest_path FROM run_evidence WHERE run_id=?",(receipt['run_id'],))
        (self.workspace/proof['manifest_path']).write_text('changed archive')
        self.assertFalse(cloud_assessment_ready(self.store,self.project['id'],self.plan['id']))

    def test_disconnect_keeps_unknown_and_blocks_new_attempt(self):
        self.approve_space()
        with patch('lab.devspace.connection',return_value=['ssh.exe','fixed-host']), patch('lab.devspace.check_peer'), patch('lab.devspace.check_compute',return_value={'ready':True}), \
                patch.object(self.research.remote,'_transport',side_effect=TimeoutError('ack lost')) as spawn:
            receipt=self.research.run(self.sid,self.request,'original')
            self.assertEqual(receipt['state'],'unknown',receipt)
            original=self.research.run(self.sid,self.request,'original')
            self.assertEqual(original['run_id'],receipt['run_id']);self.assertEqual(original['state'],'unknown')
            with self.assertRaises(ValueError):
                self.research.run(self.sid,self.request,'retry')
        spawn.assert_called_once()

    def test_lost_ack_restart_recovers_original_and_pause_does_not_cancel_remote(self):
        self.approve_space()
        with patch('lab.devspace.connection',return_value=['ssh.exe']),patch('lab.devspace.check_peer'),patch('lab.devspace.check_compute',return_value={'ready':True}), \
                patch.object(self.research.remote,'_transport',side_effect=TimeoutError('lost')):
            started=self.research.run(self.sid,self.request,'lost-ack')
        id=started['run_id']
        self.research.workflow.pause(self.sid)
        self.research.close()
        restored=Research(self.store,self.company);self.addCleanup(restored.close)
        self.store.execute('UPDATE devspace_jobs SET next_check=0 WHERE run_id=?',(id,))
        job=self.store.one('SELECT * FROM devspace_jobs WHERE run_id=?',(id,))
        value={'run_id':id,'space_uid':'space-id','job_digest':job['job_digest'],'state':'completed','exit_code':0,
            'files':{'stdout.log':base64.b64encode(b'finished once\n').decode(),'stderr.log':'',
                     'metrics.json':base64.b64encode(b'{"loss":0.25}').decode()}}
        with patch('lab.devspace.connection',return_value=['ssh.exe']),patch.object(restored.remote,'_transport',return_value=value) as read:
            restored.remote.poll();restored.remote.poll()
        self.assertEqual(read.call_count,1);self.assertEqual(read.call_args.args[2],'status')
        self.assertEqual(self.store.one('SELECT COUNT(*) n FROM research_runs')['n'],1)
        self.assertEqual(self.store.one('SELECT state FROM research_runs WHERE id=?',(id,))['state'],'completed')
        self.assertTrue(self.store.evidence.summary('local',id)['analysis_ready'])
        self.assertTrue(restored.workflow.paused(self.sid))
        finished=[json.loads(r['payload']) for r in self.store.rows("SELECT payload FROM events WHERE kind='research.run_finished'")]
        self.assertEqual([r['run_id'] for r in finished],[id])

    def test_interrupted_collection_is_idempotent_and_does_not_hide_corruption(self):
        self.approve_space()
        def running(args,directory,mode,id,scope,fingerprint):
            return {'run_id':id,'space_uid':scope['uid'],'job_digest':fingerprint,'state':'running'}
        with patch('lab.devspace.connection',return_value=['ssh.exe']),patch('lab.devspace.check_peer'),patch('lab.devspace.check_compute',return_value={'ready':True}),patch.object(self.research.remote,'_transport',side_effect=running):
            start=self.research.run(self.sid,self.request,'once')
        id=start['run_id'];job=self.store.one('SELECT * FROM devspace_jobs WHERE run_id=?',(id,));run=self.store.one('SELECT * FROM research_runs WHERE id=?',(id,))
        self.assertTrue(self.research.workflow.external_pending(self.sid))
        response={'run_id':id,'space_uid':'space-id','job_digest':job['job_digest'],'state':'completed','exit_code':0,
            'files':{'stdout.log':base64.b64encode(b'one log\n').decode(),'stderr.log':'','metrics.json':base64.b64encode(b'{"ok":1}').decode()}}
        with patch.object(self.store.evidence,'collect_local',side_effect=OSError('disk busy')):
            with self.assertRaises(OSError):self.research.remote._observe(run,response)
        self.store.execute('UPDATE devspace_jobs SET next_check=0 WHERE run_id=?',(id,))
        with patch.object(self.research.remote,'_transport') as read:self.research.remote.poll()
        read.assert_not_called()
        directory=self.workspace/run['directory']
        self.assertEqual((directory/'stdout.log').read_bytes(),b'one log\n')
        self.assertEqual(self.store.one('SELECT state FROM devspace_jobs WHERE run_id=?',(id,))['state'],'collected')
        (directory/'metrics.json').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'不符'):
            devspace.collect(directory,id,'space-id',['metrics.json'],idempotent=True)
        self.assertEqual((directory/'metrics.json').read_bytes(),b'changed')
