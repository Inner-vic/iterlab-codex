import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lab import devspace,ssh_discovery
from tests import test_gpu_execution as fixtures


class SSHDiscoveryTests(unittest.TestCase):
    setUp=fixtures.GPUExecutionTests.setUp
    approve_space=fixtures.GPUExecutionTests.approve_space
    approve=fixtures.GPUExecutionTests.approve

    def setup_space(self):
        self.approve_space(configured=False)
        endpoint={'host':devspace.HOST,'user':'root','port':2222,'source':'official_devspace_access'}
        item={'name':'gpu-test','uid':'space-id','namespace':'test-ns','phase':'ready','resources':{'gpu':0},'ssh':endpoint}
        self.store.execute('UPDATE company_bindings SET observation=?',(json.dumps(item),))
        raw={'metadata':{'name':'gpu-test','uid':'space-id','namespace':'test-ns'},'status':{'phase':'ready'},
             'spec':{'resources':{'gpu':0},'sshSecretName':'matching-key','accesses':[
                 {'targetPort':22,'address':'ssh -p 2222 root@'+devspace.HOST}]}}
        return item,raw

    def test_key_names_rank_only_existing_public_private_pairs(self):
        directory=self.root/'ssh';directory.mkdir()
        for name in ['default','bmad_roadmodel_ed25519','other']:
            (directory/name).write_text('private test content must never be returned')
            (directory/(name+'.pub')).write_text('ssh-ed25519 public-fixture')
        (directory/'public-only.pub').write_text('ssh-ed25519 public-only')
        (directory/'invalid.pub').write_text('not a public key')
        names=ssh_discovery.local_identities(['bmad-roadmodel-20261009'],directory)
        self.assertEqual(names[0].name,'bmad_roadmodel_ed25519')
        self.assertEqual(len(names),3)

    def test_only_auth_failure_advances_to_next_key_and_success_is_verified_before_saved(self):
        _,raw=self.setup_space();first=self.root/'first';second=self.root/'second'
        with patch.object(self.company,'_run',return_value={'status':'ok','data':raw}), \
             patch('lab.ssh_discovery.local_identities',return_value=[first,second]), \
             patch('lab.devspace.connection',return_value=['ssh.exe']) as connect, \
             patch('lab.ssh_discovery.subprocess.run',side_effect=[SimpleNamespace(returncode=255,stderr=b'Permission denied (publickey)',stdout=b''),SimpleNamespace(returncode=0,stdout=b'iterlab-ssh-auth-ok\n')]), \
             patch('lab.devspace.configure',return_value={'state':'verified','port':2222}) as configure:
            result=ssh_discovery.configure_automatically(self.company,self.sid,'space-id')
        self.assertEqual(result['state'],'verified');self.assertEqual(connect.call_count,2)
        self.assertEqual(configure.call_args.args[-1],str(second.resolve()))
        self.assertEqual(self.store.rows('SELECT * FROM research_runs'),[])

    def test_network_or_changed_host_key_does_not_trigger_key_scanning(self):
        _,raw=self.setup_space()
        for error in [b'Connection refused',b'REMOTE HOST IDENTIFICATION HAS CHANGED']:
            with self.subTest(error=error),patch.object(self.company,'_run',return_value={'status':'ok','data':raw}), \
                 patch('lab.ssh_discovery.local_identities',return_value=[self.root/'first',self.root/'second']), \
                 patch('lab.devspace.connection',return_value=['ssh.exe']), \
                 patch('lab.ssh_discovery.subprocess.run',return_value=SimpleNamespace(returncode=255,stderr=error,stdout=b'')) as run, \
                 patch('lab.devspace.configure') as configure:
                with self.assertRaises(ValueError):ssh_discovery.configure_automatically(self.company,self.sid,'space-id')
                self.assertEqual(run.call_count,1);configure.assert_not_called()

    def test_wrong_space_uid_is_rejected_before_any_ssh_attempt(self):
        _,raw=self.setup_space();raw['metadata']['uid']='replaced-object'
        with patch.object(self.company,'_run',return_value={'status':'ok','data':raw}),patch('lab.ssh_discovery.subprocess.run') as run:
            with self.assertRaisesRegex(ValueError,'身份已变化'):ssh_discovery.configure_automatically(self.company,self.sid,'space-id')
            run.assert_not_called()

    def test_cpu_space_checks_identity_and_python_without_requiring_nvidia_tools(self):
        item,_=self.setup_space();before=self.store.rows('SELECT * FROM research_grants')
        with patch.object(self.company,'lpai',return_value={'item':item}),patch('lab.devspace.shutil.which',return_value='ssh.exe'), \
             patch('lab.devspace.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=b'space-id\n')) as run:
            result=devspace.configure(self.company,self.sid,'space-id',None)
        self.assertEqual(result['state'],'verified');self.assertEqual(result['gpu_count'],0)
        self.assertNotIn('nvidia-smi',run.call_args.args[0][-1])
        self.assertIn('cat /lpai/.iterlab-space-uid',run.call_args.args[0][-1])
        self.assertEqual(self.store.rows('SELECT * FROM research_grants'),before)
        self.assertEqual(self.store.rows('SELECT * FROM research_runs'),[])

    def test_failed_authentication_retains_a_readable_failure_instead_of_generic_saved(self):
        item,_=self.setup_space()
        with patch.object(self.company,'lpai',return_value={'item':item}),patch('lab.devspace.shutil.which',return_value='ssh.exe'), \
             patch('lab.devspace.subprocess.run',return_value=SimpleNamespace(returncode=255,stdout=b'',stderr=b'Permission denied (publickey)')):
            with self.assertRaisesRegex(ValueError,'密钥认证'):devspace.configure(self.company,self.sid,'space-id',None)
        saved=devspace.ssh_settings(self.store,self.project['id'],devspace.binding(self.store,self.project['id']))
        self.assertEqual(saved['state'],'failed');self.assertIn('密钥认证',saved['message'])
        self.assertIsNone(devspace.ssh_profile(self.store,self.project['id'],devspace.binding(self.store,self.project['id'])))
