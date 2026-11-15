import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch

from lab.company import Company
from lab.platform_health import PlatformError,error_category,credential_revision
from lab.store import Store

ROOT=Path(__file__).resolve().parents[1]


class HealthTests(unittest.TestCase):
    def test_login_preflight_checks_only_requested_adapter(self):
        with patch.object(self.company,'lpai',return_value={'items':[]}) as cli,patch.object(self.company.training,'sdk',side_effect=AssertionError('Do not inspect unrelated credentials')):
            result=self.company.health.check('lpai',kinds={'lpai_cli'})
        self.assertEqual([r['kind'] for r in result['checks']],['lpai_cli']);cli.assert_called_once()
        with self.assertRaises(ValueError):self.company.health.check('lpai',kinds={'gitlab_api'})
    def test_proxy_refusal_and_wrapped_network_errors_are_actionable_without_raw_output(self):
        self.assertEqual(error_category(ValueError('proxyconnect tcp: connectex connection refused')),'proxy')
        try:
            try:raise OSError('proxyconnect tcp')
            except OSError as cause:raise RuntimeError('SDK failed') from cause
        except RuntimeError as error:self.assertEqual(error_category(error),'proxy')
        HttpClientException=type('HttpClientException',(Exception,),{})
        self.assertEqual(error_category(HttpClientException()),'http_client')
    def setUp(self):
        self.store=Store(ROOT/'.evidence/tests'/uuid.uuid4().hex)
        self.calls=[]
        def runner(args,**kwargs):
            self.calls.append((args,kwargs))
            return subprocess.CompletedProcess(args,0,'a'*40+'\tHEAD\n','')
        self.company=Company(self.store,runner=runner,locator=lambda name:name)
        self.company.configure({'revision':0,'lpai_enabled':True,'gitlab_enabled':True,'gitlab_host':'gitlab.example'})
        self.item={'host':'gitlab.example','path_with_namespace':'group/repo',
                   'http_url_to_repo':'https://gitlab.example/group/repo.git','ssh_url_to_repo':'git@gitlab.example:group/repo.git'}

    def tearDown(self):self.company.close();self.store.close()

    def test_cli_success_does_not_hide_sdk_failure_or_store_secrets(self):
        original=self.company.config()
        with patch.object(self.company,'lpai',return_value={'items':[]}),patch.object(self.company.training,'sdk',side_effect=ValueError('401 private_token=NEVER')):
            receipt=self.company.health.check('lpai')
        self.assertEqual([r['state'] for r in receipt['checks']],['passed','failed'])
        self.assertEqual(receipt['checks'][1]['category'],'authentication')
        self.assertNotIn('NEVER',json.dumps(self.company.status()))
        self.assertEqual(self.company.config(),original)
        self.assertEqual(self.store.rows('SELECT * FROM company_operations'),[])
        self.assertEqual(self.store.rows('SELECT * FROM company_training'),[])

    def test_health_is_durable_offline_stale_and_keeps_last_success(self):
        with patch.object(self.company,'lpai',return_value={'items':[]}),patch.object(self.company.training,'sdk',return_value={'read_verified':True}):
            first=self.company.health.check('lpai')
        with patch.object(self.company,'lpai',side_effect=TimeoutError('timeout')),patch.object(self.company.training,'sdk',side_effect=PlatformError('permission')):
            second=self.company.health.check('lpai')
        self.assertEqual(second['checks'][0]['last_success_at'],first['checks'][0]['checked_at'])
        self.assertEqual(second['checks'][1]['category'],'permission')
        reconstructed=Company(self.store,runner=lambda *a,**kw: self.fail('GET/status must not access platform'),locator=lambda n:n)
        self.assertEqual(len(reconstructed.status()['health']),2)
        self.company.configure({'revision':1,'namespace':'another-scope'})
        self.assertTrue(all(r['stale'] for r in reconstructed.status()['health']))
        reconstructed.close()

    def test_git_account_not_misrepresented_as_source_access(self):
        with patch.object(self.company,'gitlab',return_value={'username':'fixture'}):receipt=self.company.health.check('gitlab')
        self.assertTrue(receipt['git_transport_not_checked'])
        self.assertEqual(len(receipt['checks']),1)
    def test_repository_permission_failure_does_not_hide_account_success(self):
        def read(operation,*args):
            if operation=='repository':raise ValueError('403 Forbidden private_token=NEVER')
            return {'username':'fixture'}
        with patch.object(self.company,'gitlab',side_effect=read):receipt=self.company.health.check('gitlab',project_id='group/repo')
        self.assertEqual([r['state'] for r in receipt['checks']],['passed','failed'])
        self.assertEqual(receipt['checks'][1]['category'],'permission')
        self.assertNotIn('NEVER',json.dumps(receipt))

    def test_git_probe_uses_selected_protocol_and_official_helper_without_mutation(self):
        original=self.company.config()
        with patch.object(self.company,'gitlab',side_effect=lambda op,*a: self.item if op=='repository' else {'username':'fixture'}):
            receipt=self.company.health.check('gitlab',project_id='group/repo')
        self.assertEqual([r['state'] for r in receipt['checks']],['passed','passed'])
        args,kwargs=self.calls[-1]
        self.assertIn('https://gitlab.example/group/repo.git',args)
        self.assertIn('credential.https://gitlab.example.helper=!"glab" auth git-credential',args)
        self.assertIn('ls-remote',args);self.assertNotIn('clone',args)
        self.assertEqual(kwargs['env']['GCM_INTERACTIVE'],'never')
        self.assertEqual(self.company.config(),original)
        self.company.configure({'revision':1,'git_protocol':'ssh'})
        self.company.git_probe(self.item)
        args=self.calls[-1][0];self.assertIn(self.item['ssh_url_to_repo'],args)
        self.assertTrue(any('StrictHostKeyChecking=yes' in arg for arg in args))
        self.assertFalse(any('credential.' in arg for arg in args))

    def test_clone_uses_https_and_existing_origin_is_not_rewritten(self):
        project=self.store.create_project('git fixture');sid=self.store.create_session(project['id'],'ept')['id']
        with patch.object(self.company,'gitlab',return_value={'id':1,'name':'repo',**self.item}):self.company.bind_repository(sid,1)
        result=self.company.perform(sid,{'action':'git_clone'},'clone-check',True)
        self.assertEqual(result['state'],'completed')
        args=self.calls[-1][0];self.assertIn('clone',args);self.assertIn(self.item['http_url_to_repo'],args)
        self.assertNotIn('set-url',args)

    def test_changed_config_cannot_receive_old_health_result(self):
        def read(*a):
            self.company.configure({'revision':1,'namespace':'new-scope'});return {'items':[]}
        with patch.object(self.company,'lpai',side_effect=read):
            with self.assertRaisesRegex(ValueError,'配置已变化'):self.company.health.check('lpai')
        self.assertEqual(self.store.rows('SELECT * FROM company_health'),[])

    def credentials(self):
        home=self.store.root/'credential-fixture';agent=home/'.config/lpai-agent/config';sdk=home/'.lpai/conf.json'
        agent.parent.mkdir(parents=True);sdk.parent.mkdir(parents=True)
        agent.write_text('api_key=FAKE_INITIAL_TOKEN',encoding='utf8');sdk.write_text('{"token":"FAKE_SDK_TOKEN"}',encoding='utf8')
        return home,agent,sdk

    def test_agent_key_change_invalidates_only_agent_observation_without_copying_key(self):
        home,agent,sdk=self.credentials()
        with patch('lab.platform_health.Path.home',return_value=home),patch.object(self.company,'lpai',return_value={'items':[]}),patch.object(self.company.training,'sdk',return_value={'read_verified':True}):
            self.company.health.check('lpai')
            self.assertTrue(all(not r['stale'] for r in self.company.health.status()))
            agent.write_text('api_key=DO_NOT_STORE_NEW_TOKEN',encoding='utf8')
            latest={r['kind']:r for r in self.company.health.status()}
            self.assertEqual(latest['lpai_cli']['stale_reason'],'credentials_changed')
            self.assertFalse(latest['lpai_sdk']['stale'])
            self.assertEqual(latest['lpai_cli']['authentication_source'],'LPAI Agent 配置')
            self.assertNotIn('DO_NOT_STORE',json.dumps(self.store.rows('SELECT * FROM company_health_sources')))
            # A new credential failure cannot inherit the old identity's success.
            with patch.object(self.company,'lpai',side_effect=PlatformError('authentication')):
                receipt=self.company.health.check('lpai')
            self.assertIsNone(receipt['checks'][0]['last_success_at'])
            self.assertNotIn('DO_NOT_STORE',json.dumps(self.company.status()))

    def test_sdk_login_change_does_not_claim_agent_token_changed(self):
        home,agent,sdk=self.credentials()
        with patch('lab.platform_health.Path.home',return_value=home),patch.object(self.company,'lpai',return_value={'items':[]}),patch.object(self.company.training,'sdk',return_value={'read_verified':True}):
            self.company.health.check('lpai')
            sdk.write_text('{"token":"DIFFERENT_FAKE_SDK_TOKEN"}',encoding='utf8')
            latest={r['kind']:r for r in self.company.health.status()}
            self.assertFalse(latest['lpai_cli']['stale'])
            self.assertEqual(latest['lpai_sdk']['stale_reason'],'credentials_changed')

    def test_credentials_changed_during_check_discard_old_result(self):
        home,agent,sdk=self.credentials()
        def rotate(*args,**kwargs):
            agent.write_text('api_key=CHANGED_DURING_REQUEST',encoding='utf8');return {'items':[]}
        with patch('lab.platform_health.Path.home',return_value=home),patch.object(self.company,'lpai',side_effect=rotate):
            with self.assertRaisesRegex(ValueError,'官方认证配置已变化'):self.company.health.check('lpai')
        self.assertFalse(self.store.rows('SELECT * FROM company_health'))

    def test_credential_revision_uses_metadata_without_reading_credentials(self):
        home,agent,sdk=self.credentials()
        with patch('lab.platform_health.Path.home',return_value=home),patch.object(Path,'open',side_effect=AssertionError('Credential contents must not be read')):
            self.assertEqual(len(credential_revision('lpai_cli')),64)
            self.assertEqual(len(credential_revision('lpai_sdk')),64)
            self.assertEqual(len(credential_revision('gitlab_api')),64)
            self.assertIsNone(credential_revision('unregistered'))


class SDKProtocolTests(unittest.TestCase):
    def test_actual_bridge_repairs_gbk_streams_and_preserves_chinese(self):
        code="""import importlib.util,sys
for stream in (sys.stdin,sys.stdout,sys.stderr):stream.reconfigure(encoding='gbk')
spec=importlib.util.spec_from_file_location('bridge',sys.argv[1]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
m.execute=lambda request: {'text':request['text']}
m.main()
"""
        text='故障诊断：容量控制，负结果也要分析。'
        result=subprocess.run([sys.executable,'-I','-c',code,str(ROOT/'scripts/lpai_sdk_bridge.py')],
            input=json.dumps({'text':text},ensure_ascii=False).encode('utf-8'),capture_output=True,timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout.decode('utf-8'))['result']['text'],text)

    def test_sdk_local_config_is_not_connection_verification(self):
        spec=importlib.util.spec_from_file_location('bridge',ROOT/'scripts/lpai_sdk_bridge.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        import types
        conf=types.SimpleNamespace(get=lambda key:'fake-secret',get_namespace=lambda:'vc-chassis')
        modules={n:types.ModuleType(n) for n in ['lpai','lpai.conf','lpai.conf.lipai_conf','lpai.pipelinev2','lpai.pipelinev2.project','lpai.pipelinev2.task']}
        modules['lpai.conf.lipai_conf'].LpaiConf=lambda **kw:conf
        project=unittest.mock.Mock();project.list.return_value={'items':[]}
        modules['lpai.pipelinev2.project'].Project=lambda config:project
        modules['lpai.pipelinev2.task'].Task=unittest.mock.Mock()
        with patch.dict(sys.modules,modules):
            result=module.execute({'operation':'auth_status','namespace':'vc-chassis'})
            self.assertFalse(result['connection_checked']);project.list.assert_not_called()
            result=module.execute({'operation':'connection_check','namespace':'vc-chassis'})
            self.assertTrue(result['read_verified']);self.assertFalse(result['write_verified'])
            project.list.assert_called_once_with(page_size=1,page_num=1)
            with self.assertRaisesRegex(ValueError,'namespace'):module.execute({'operation':'connection_check','namespace':'wrong'})
            conf.get=lambda key:None
            with self.assertRaises(ValueError) as missing:module.execute({'operation':'connection_check','namespace':'wrong'})
            self.assertEqual(error_category(missing.exception),'authentication')
        self.assertEqual(error_category(PlatformError('network')),'network')


if __name__=='__main__':unittest.main()
