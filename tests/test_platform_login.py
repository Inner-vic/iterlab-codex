import importlib.util,os,tempfile,unittest,types,sys
from pathlib import Path
from unittest.mock import patch,MagicMock
from tests import test_company as fixtures

spec=importlib.util.spec_from_file_location('interactive_platform_login',Path(__file__).resolve().parents[1]/'scripts/platform_login.py')
helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)

class CredentialWriterTests(unittest.TestCase):
    def test_agent_token_preserves_other_configuration_and_uses_official_location(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'LPAI_API_KEY':''}):
            path=Path(temp)/'.config/lpai-agent/config';path.parent.mkdir(parents=True)
            path.write_text('# keep\ntimeout=30\napi_key=old-fixture\n',encoding='utf-8')
            helper.save_agent_token('lpak_isolated_fixture',temp)
            value=path.read_text();self.assertIn('timeout=30',value);self.assertIn('api_key=lpak_isolated_fixture',value)
            self.assertNotIn('old-fixture',value)
            self.assertEqual(list(path.parent.iterdir()),[path])
    def test_environment_override_and_multiline_are_blocked_without_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict(os.environ,{'LPAI_API_KEY':'different-fixture'}),self.assertRaises(ValueError):
                helper.save_agent_token('lpak_isolated_fixture',temp)
            with self.assertRaises(ValueError):helper.save_agent_token('token\napi_key=injection',temp)
            self.assertFalse((Path(temp)/'.config').exists())
    def test_sdk_uses_official_configuration_api_and_rejects_agent_token(self):
        module=types.ModuleType('lpai.conf.lipai_conf');conf=MagicMock();module.LpaiConf=MagicMock(return_value=conf)
        with patch.dict(os.environ,{'LPAI_TOKEN':''}),patch.dict(sys.modules,{'lpai.conf.lipai_conf':module}):
            with self.assertRaises(ValueError):helper.save_sdk_token('lpak_agent_fixture','scope')
            module.LpaiConf.assert_not_called()
            helper.save_sdk_token('training_fixture_token','scope')
            module.LpaiConf.assert_called_once_with(env='prod',token='training_fixture_token',namespace='scope')
            conf.save.assert_called_once_with()

class LoginLauncherTests(unittest.TestCase):
    setUp=fixtures.CompanyTests.setUp
    tearDown=fixtures.CompanyTests.tearDown
    def test_options_do_not_claim_authentication_or_return_credentials(self):
        value=self.company.login.options()
        self.assertTrue(value['lpai_credentials_are_independent'])
        self.assertTrue(all(r['configuration_is_not_login'] for r in value['items']))
        self.assertNotIn('api_key',str(value));self.assertNotIn('access_token',str(value))
    @unittest.skipUnless(os.name=='nt','Windows interactive console launcher')
    def test_launch_is_user_only_deduplicated_and_has_no_token_argument(self):
        process=MagicMock();process.poll.return_value=None
        with patch.object(self.company.health,'check',return_value={'checks':[{'kind':'gitlab_api','state':'failed','category':'authentication'}]}),patch('lab.platform_login.subprocess.Popen',return_value=process) as launch:
            first=self.company.login.start('gitlab');second=self.company.login.start('gitlab')
        self.assertTrue(first['started']);self.assertTrue(second['already_running']);self.assertEqual(launch.call_count,1)
        args=launch.call_args.args[0];self.assertIn('gitlab',args);self.assertNotIn('--token',args)
        self.assertNotIn('shell',launch.call_args.kwargs)
    def test_active_research_and_unknown_cloud_task_prevent_account_switch(self):
        self.store.patch_session(self.session['id'],active_turn='live-turn')
        with patch.object(self.company.health,'check',return_value={'checks':[{'kind':'gitlab_api','state':'failed','category':'authentication'}]}),patch('lab.platform_login.subprocess.Popen') as launch,self.assertRaisesRegex(ValueError,'暂停'):
            self.company.login.start('gitlab')
        launch.assert_not_called()
    def test_working_login_is_verified_and_reused_without_console_even_during_research(self):
        self.store.patch_session(self.session['id'],active_turn='live-turn')
        with patch.object(self.company.health,'check',return_value={'checks':[{'kind':'gitlab_api','state':'passed'}]}) as check,patch('lab.platform_login.subprocess.Popen') as launch:
            result=self.company.login.start('gitlab')
        self.assertTrue(result['reused']);self.assertFalse(result['started']);launch.assert_not_called()
        check.assert_called_once_with('gitlab',kinds={'gitlab_api'})
    def test_network_failure_does_not_prompt_for_new_credentials(self):
        with patch.object(self.company.health,'check',return_value={'checks':[{'kind':'lpai_cli','state':'failed','category':'network','message':'network unavailable'}]}) as check,patch('lab.platform_login.subprocess.Popen') as launch:
            result=self.company.login.start('lpai_cli')
        self.assertTrue(result['needs_attention']);self.assertFalse(result['started']);launch.assert_not_called()
        check.assert_called_once_with('lpai',kinds={'lpai_cli'})
    def test_options_show_only_fresh_verified_login_as_connected(self):
        with patch('lab.platform_login.Path.is_file',return_value=True),patch.object(self.company.health,'status',return_value=[
            {'kind':'lpai_cli','stale':False,'state':'passed','category':None,'message':'read passed'},
            {'kind':'lpai_sdk','stale':False,'state':'failed','category':'authentication','message':'needs login'},
            {'kind':'gitlab_api','stale':True,'state':'passed','category':None,'message':'old login'}]):
            states={r['kind']:r['login_state'] for r in self.company.login.options()['items']}
        self.assertEqual(states,{'lpai_cli':'connected','lpai_sdk':'needs_login','gitlab':'needs_check'})
    def test_unknown_platform_is_not_an_arbitrary_command_launcher(self):
        with patch('lab.platform_login.subprocess.Popen') as launch,self.assertRaises(ValueError):self.company.login.start('terminal')
        launch.assert_not_called()
    def test_http_login_route_refuses_credentials_and_arbitrary_arguments(self):
        from lab.web import Application
        app=Application.__new__(Application);app.store=self.store;app.kernel=types.SimpleNamespace(company=self.company)
        with patch('lab.platform_login.subprocess.Popen') as launch,self.assertRaisesRegex(ValueError,'不接收凭据'):
            app.route('POST','/api/company/login',{}, {'kind':'gitlab','token':'never-accept-this'})
        launch.assert_not_called()
