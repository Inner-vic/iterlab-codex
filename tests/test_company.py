import json
import os
from pathlib import Path
import shutil
import subprocess
import types
import unittest
import uuid
from unittest.mock import patch

from lab.company import Company
from lab.store import Store


class CompanyTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(__file__).resolve().parents[1]/'.evidence/tests'/uuid.uuid4().hex
        self.store=Store(self.root)
        self.project=self.store.create_project('company-fixture')
        self.session=self.store.create_session(self.project['id'],'ept')
        self.phase='Running'
        self.start_count=0
        self.timeout_start=False
        def runner(args,**kwargs):
            if 'start' in args:
                self.start_count+=1
                if self.timeout_start:
                    raise subprocess.TimeoutExpired(args,30)
                value={'status':'pending_approval','message':'需要确认，请查看飞书'}
            elif 'api' in args:
                endpoint=args[-1]
                if endpoint=='user':
                    value={'id':1,'username':'fixture','private_token':'never-project'}
                else:
                    value={'id':12,'name':'repo','path_with_namespace':'group/repo','web_url':'https://gitlab.example/group/repo','ssh_url_to_repo':'git@gitlab.example:group/repo.git','http_url_to_repo':'https://gitlab.example/group/repo.git','private_token':'never-project'}
                    if endpoint.startswith('projects?'):
                        value=[value]
            else:
                raw={'metadata':{'name':'fixture-space' if 'devspaces' in args else 'fixture-task','uid':'space-uid' if 'devspaces' in args else 'task-uid','namespace':'vc-chassis'},
                     'status':{'phase':self.phase},'spec':{'resources':{'cpu':1},'env':{'TOKEN':'never-project'}}}
                value={'status':'ok','data':{'items':[raw],'total':1} if 'list' in args else raw}
            return subprocess.CompletedProcess(args,0,json.dumps(value),'')
        self.company=Company(self.store,runner=runner,locator=lambda name:name)
        # This suite scripts the platform; do not run a real asset SDK child.
        self.company.outputs.collect=lambda *a,**kw:{'state':'files_archived','task_uid':'task-uid','report_path':'fixture.md','files_saved':1}
        self.company.configure({'revision':0,'lpai_enabled':True,'gitlab_enabled':True,'gitlab_host':'gitlab.example'})
    def tearDown(self):
        self.company.close()
        self.store.close()
    def test_official_results_are_allowlisted(self):
        spaces=self.company.lpai('spaces')
        self.assertEqual(spaces['items'][0]['uid'],'space-uid')
        self.assertNotIn('never-project',json.dumps(spaces))
        self.assertNotIn('never-project',json.dumps(self.company.gitlab('account')))
        self.assertNotIn('never-project',json.dumps(self.company.gitlab('repositories')))
    def test_unchanged_connection_check_keeps_watch_authority(self):
        original=self.company.config()
        self.company.configure(original)
        self.assertEqual(self.company.config(),original)
    def test_native_context_exposes_exact_recipe_contract_without_probe_or_defaults(self):
        with patch.object(self.company,'runner',side_effect=AssertionError('No platform/model IO for contract')):
            brief=self.company.context(self.session['id'],brief=True)
        from lab.component_configuration import RECIPE_FIELDS
        contract=brief['component_recipe_contract']
        self.assertEqual(set(contract['required_fields']),RECIPE_FIELDS)
        self.assertEqual(contract['shapes']['image'],['uid','version'])
        self.assertEqual(self.store.rows('SELECT * FROM company_training'),[])
    def test_shared_capability_declaration_never_probes_or_requires_skill(self):
        with patch.object(self.company,'runner',side_effect=AssertionError('声明不应访问平台或模型')):
            status=self.company.status()
            context=self.company.context(self.session['id'])
        capabilities=status['capabilities']
        self.assertEqual(capabilities,context['connections']['capabilities'])
        self.assertFalse(capabilities['lpai']['skill_required'])
        self.assertFalse(capabilities['lpai']['connection_verified'])
        self.assertEqual(capabilities['lpai']['availability'],'configured_not_probed')
        self.assertNotIn('collect_training_artifacts',capabilities['lpai']['not_implemented'])
        boundaries=capabilities['lpai']['operation_boundaries']
        self.assertTrue(boundaries['artifact_collection']['implemented'])
        self.assertEqual(boundaries['artifact_collection']['mode'],'bounded_small_files_and_checkpoint_index')
        self.assertTrue(boundaries['devspace_training']['implemented'])
        self.assertTrue(boundaries['devspace_training']['disconnect_auto_recovery'])
        self.assertEqual(boundaries['devspace_training']['recovery_scope'],'new_durable_ssh_jobs_only; unknown reads never relaunch')
        self.assertFalse(boundaries['devspace_training']['local_fallback'])
        self.assertFalse(boundaries['runtime_verified'])
        self.assertIn('large_checkpoint_download',capabilities['lpai']['not_implemented'])
        self.assertIn('recover_requested_submission',capabilities['lpai']['implemented_operations'])
        self.assertNotIn('submit_training',capabilities['lpai']['implemented_operations'])
        self.company.configure({'revision':1,'lpai_enabled':False,'gitlab_enabled':False})
        self.assertEqual(self.company.status()['capabilities']['lpai']['availability'],'disabled')
        self.assertTrue(self.company.status()['capabilities']['git']['write_requires_exact_approval'])
    def test_space_identity_exclusive_and_not_implicitly_started(self):
        self.company.bind_space(self.session['id'],'fixture-space','space-uid')
        other=self.store.create_project('other')
        other_session=self.store.create_session(other['id'],'ept')
        with self.assertRaises(ValueError):
            self.company.bind_space(other_session['id'],'fixture-space','space-uid')
        self.assertEqual(self.start_count,0)
    def test_component_id_cannot_be_used_to_start_a_space_even_after_approval(self):
        bad={'action':'lpai_start','submission_id':'prepared-component-run'}
        with patch.object(self.company,'_perform') as perform,patch.object(self.company,'context') as context:
            with self.assertRaisesRegex(ValueError,'lpai_submit.*未启动空间'):
                self.company.perform(self.session['id'],bad,'wrong-action',True)
            with self.assertRaisesRegex(ValueError,'不接受submission_id'):
                self.company.review_text(self.session['id'],bad)
            perform.assert_not_called();context.assert_not_called()
        self.assertEqual(self.store.rows('SELECT * FROM company_operations'),[])
        self.assertEqual(self.start_count,0)

    def test_component_submit_requires_only_its_exact_preview_identity(self):
        for bad in [{'action':'lpai_submit'},{'action':'lpai_submit','submission_id':''},
                    {'action':'lpai_submit','submission_id':'id','branch':'unexpected'}]:
            with self.assertRaisesRegex(ValueError,'lpai_submit仅接受'):
                self.company.perform(self.session['id'],bad,'bad-submit',True)
        with patch.object(self.company,'_perform',return_value={'state':'submitted'}) as perform:
            value=self.company.perform(self.session['id'],{'action':'lpai_submit','submission_id':'exact-id'},'valid-submit',True)
            self.assertEqual(value['state'],'submitted')
            perform.assert_called_once()
    def test_pending_or_unknown_start_not_resubmitted(self):
        self.phase='Stopped'
        self.company.bind_space(self.session['id'],'fixture-space','space-uid')
        request={'action':'lpai_start'}
        first=self.company.perform(self.session['id'],request,'same-operation',True)
        second=self.company.perform(self.session['id'],request,'same-operation',True)
        self.assertEqual(first['state'],'pending_approval')
        self.assertEqual(first,second)
        self.assertEqual(self.start_count,1)
        with self.assertRaises(ValueError):
            self.company.perform(self.session['id'],request,'new-id-same-pending-space',True)
        self.phase='Running'
        self.company.lpai('space',name='fixture-space')
        self.assertEqual(self.store.one("SELECT state FROM company_operations WHERE id='same-operation'")['state'],'observed_active')
        self.assertEqual(self.company.context(self.session['id'])['bindings'][0]['observation']['phase'],'Running')
    def test_unknown_start_and_approval_gate(self):
        self.phase='Stopped';self.timeout_start=True
        self.company.bind_space(self.session['id'],'fixture-space','space-uid')
        with self.assertRaises(ValueError):
            self.company.perform(self.session['id'],{'action':'lpai_start'},'not-approved')
        result=self.company.perform(self.session['id'],{'action':'lpai_start'},'unknown-start',True)
        self.assertEqual(result['state'],'unknown')
        self.company.perform(self.session['id'],{'action':'lpai_start'},'unknown-start',True)
        self.assertEqual(self.start_count,1)
    def test_nonzero_cli_start_can_have_unknown_remote_outcome(self):
        self.phase='Stopped'
        self.company.bind_space(self.session['id'],'fixture-space','space-uid')
        original=self.company.runner
        def fail_after_send(args,**kwargs):
            if 'start' in args:return subprocess.CompletedProcess(args,1,'','connection reset after send')
            return original(args,**kwargs)
        self.company.runner=fail_after_send
        result=self.company.perform(self.session['id'],{'action':'lpai_start'},'lost-cli-response',True)
        self.assertEqual(result['state'],'unknown')
        with self.assertRaisesRegex(ValueError,'原开发空间启动请求'):
            self.company.perform(self.session['id'],{'action':'lpai_start'},'must-not-repeat',True)
    def test_remote_must_match_bound_repository_without_credentials(self):
        item=self.company.gitlab('repository',12)
        self.company._check_remote('git@gitlab.example:group/repo.git',item)
        for bad in ['https://token@gitlab.example/group/repo.git','git@gitlab.example:another/repo.git','file:///tmp/repo']:
            with self.assertRaises(ValueError):
                self.company._check_remote(bad,item)
    def test_background_observation_does_not_call_model_and_wakes_once(self):
        sid=self.session['id']
        self.company.watch(sid,'task-uid',True)
        sent=[]
        origins=[]
        kernel=types.SimpleNamespace(settings=types.SimpleNamespace(value={'channel':'ept'}),send=lambda *args,**kw:(sent.append(args),origins.append(kw.get('origin'))))
        self.phase='Succeeded'
        self.company.poll_watches(kernel,clock=10**12)
        self.company.poll_watches(kernel,clock=10**12+1000)
        self.assertEqual(len(sent),1)
        self.assertEqual(sent[0][-1],'lpai-terminal:'+self.store.one('SELECT id FROM company_watches')['id'])
        self.assertIn('非用户新输入',sent[0][1])
        self.assertEqual(origins,['background'])
    def test_changed_authority_prevents_stale_wake(self):
        self.company.watch(self.session['id'],'task-uid',True)
        self.company.configure({'revision':1,'gitlab_host':'other.gitlab.example'})
        sent=[]
        kernel=types.SimpleNamespace(settings=types.SimpleNamespace(value={'channel':'ept'}),send=lambda *args:sent.append(args))
        self.phase='Failed'
        self.company.poll_watches(kernel,clock=10**12)
        self.assertEqual(sent,[])
        self.assertEqual(self.store.one('SELECT wake_state FROM company_watches')['wake_state'],'authority_changed')

    def test_session_runs_only_include_explicit_own_watches_and_preserve_history(self):
        sid=self.session['id'];other=self.store.create_session(self.project['id'],'ept')['id']
        self.company.watch(sid,'task-uid',True)
        self.company.watch(other,'task-uid',False)
        with patch.object(self.company,'runner',side_effect=AssertionError('运行面板只读本地索引，不遍历平台')):
            own=self.company.session_runs(sid)
        self.assertEqual(len(own['active']),1)
        self.assertEqual(own['active'][0]['auto_resume'],True)
        self.store.execute("UPDATE company_watches SET state='Succeeded' WHERE session_id=?",(sid,))
        view=self.company.session_runs(sid)
        self.assertEqual(view['active'],[]);self.assertEqual(len(view['history']),1)
        self.assertEqual(len(self.company.session_runs(other)['active']),1)
        with self.assertRaises(ValueError):self.company.watch_preference(other,view['history'][0]['watch_id'],False)

    def test_project_run_view_combines_chats_without_enumerating_the_platform(self):
        sid=self.session['id'];other=self.store.create_session(self.project['id'],'ept')['id']
        outside=self.store.create_project('different-project')
        foreign=self.store.create_session(outside['id'],'ept')['id']
        self.company.watch(other,'task-uid',False)
        self.company.watch(foreign,'task-uid',False)
        with patch.object(self.company,'runner',side_effect=AssertionError('no remote query for project display')):
            own=self.company.session_runs(sid)
            all_runs=self.company.session_runs(sid,'project')
        self.assertEqual(own['active'],[])
        self.assertEqual(len(all_runs['active']),1)
        self.assertEqual(all_runs['active'][0]['session_id'],other)
        self.assertEqual(all_runs['project_id'],self.project['id'])

    def test_fast_submitted_task_is_analyzed_once_but_old_terminal_binding_is_not(self):
        self.phase='Succeeded';sid=self.session['id'];sent=[]
        kernel=types.SimpleNamespace(settings=types.SimpleNamespace(value={'channel':'ept'}),send=lambda *args,**kw:sent.append(args))
        self.company.watch(sid,'task-uid',True,analyze_terminal=True)
        self.company.poll_watches(kernel,clock=10**12)
        self.company.watch(sid,'task-uid',True,analyze_terminal=True)
        self.company.poll_watches(kernel,clock=10**12+1000)
        self.assertEqual(len(sent),1)
        other=self.store.create_session(self.project['id'],'ept')['id']
        self.company.watch(other,'task-uid',True)
        self.company.poll_watches(kernel,clock=10**12+2000)
        self.assertEqual(len(sent),1)

    def test_rebinding_a_running_task_at_completion_keeps_original_continuation(self):
        sid=self.session['id'];self.company.watch(sid,'task-uid',True)
        self.phase='Succeeded';self.company.watch(sid,'task-uid',True)
        row=self.store.one('SELECT * FROM company_watches WHERE session_id=?',(sid,))
        self.assertTrue(row['auto_resume']);self.assertEqual(row['wake_state'],'none')

    def test_watch_survives_restart_and_paused_session_is_not_woken(self):
        from lab.research import Research
        sid=self.session['id'];self.company.watch(sid,'task-uid',True)
        runner=self.company.runner;self.company.close();self.store.close()
        self.store=Store(self.root);self.company=Company(self.store,runner=runner,locator=lambda name:name)
        self.company.outputs.collect=lambda *a,**kw:{'state':'files_archived','task_uid':'task-uid','report_path':'fixture.md','files_saved':1}
        research=Research(self.store);research.pause(sid);self.phase='Succeeded';sent=[]
        kernel=types.SimpleNamespace(settings=types.SimpleNamespace(value={'channel':'ept'}),send=lambda *args,**kw:sent.append(args))
        self.company.poll_watches(kernel,clock=10**12)
        self.assertEqual(sent,[]);self.assertEqual(self.store.one('SELECT state FROM company_watches')['state'],'Succeeded')
        self.assertEqual(self.store.one('SELECT auto_resume FROM company_watches')['auto_resume'],1)
        self.assertEqual(len(self.company.session_runs(sid)['history']),1)
        self.store.execute('UPDATE research_controls SET paused=0 WHERE session_id=?',(sid,))
        self.company.poll_watches(kernel,clock=10**12+1000)
        self.assertEqual(len(sent),1) # stopping dialogue did not disable result continuation
        research.close()

    def test_watch_preference_does_not_stop_platform_or_reactivate_terminal(self):
        sid=self.session['id'];self.company.watch(sid,'task-uid',True)
        row=self.store.one('SELECT * FROM company_watches')
        with patch.object(self.company,'runner',side_effect=AssertionError('不操作平台')):
            self.company.watch_preference(sid,row['id'],False)
        self.assertFalse(self.store.one('SELECT auto_resume FROM company_watches')['auto_resume'])
        self.store.execute("UPDATE company_watches SET state='Succeeded'")
        with self.assertRaises(ValueError):self.company.watch_preference(sid,row['id'],True)

    def test_observer_repair_defers_old_removed_or_changed_scopes_without_starving_next(self):
        from lab.store import now
        sid=self.session['id'];pid=self.project['id']
        for index in range(4):
            request={'authority':{'revision':0}} if index in {1,2} else {}
            self.store.execute('INSERT INTO company_training VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                ('repair-'+str(index),'removed-session' if index==0 else sid,pid,'plan','trial','component',
                 'task-uid' if index==3 else 'task-'+str(index),json.dumps(request),'submitted',None,now(),now()))
        self.company.repair_watches(100)
        self.assertEqual(self.store.rows('SELECT * FROM company_watches'),[])
        repairs=self.store.rows('SELECT * FROM company_watch_repairs')
        self.assertEqual(len(repairs),3)
        self.assertTrue(all(row['next_check']>100 for row in repairs))
        self.company.repair_watches(101)
        self.assertEqual(self.store.one('SELECT state FROM company_watch_repairs WHERE submission_id=?',('repair-3',))['state'],'attached')
        self.assertEqual(self.start_count,0)

    def test_run_link_targets_exact_platform_record_when_component_is_known(self):
        from lab.session_runs import task_url
        url=task_url({'project_uid':'project-fixture'},'task-fixture','vc-chassis')
        self.assertTrue(url.endswith('/components/detail/project-fixture/record/task-fixture'))
        self.assertIsNone(task_url({'project_uid':'../../bad'},'task-fixture','vc-chassis'))
    def test_personal_channel_cannot_receive_company_auto_analysis(self):
        sid=self.session['id']
        self.company.watch(sid,'task-uid',True)
        # Simulate an externally changed legacy row; normal UI keeps channels immutable.
        self.store.execute('UPDATE sessions SET channel=? WHERE id=?',('codex',sid))
        with self.assertRaises(ValueError):
            self.company.watch(sid,'task-uid',True)
        sent=[]
        kernel=types.SimpleNamespace(settings=types.SimpleNamespace(value={'channel':'codex'}),send=lambda *args:sent.append(args))
        self.phase='Succeeded'
        self.company.poll_watches(kernel,clock=10**12)
        self.assertEqual(sent,[])
        self.assertEqual(self.store.one('SELECT wake_state FROM company_watches')['wake_state'],'authority_changed')


class LocalGitTests(unittest.TestCase):
    def test_real_local_branch_and_selected_file_commit(self):
        executable=shutil.which('git')
        if not executable:
            self.skipTest('Git unavailable')
        root=Path(__file__).resolve().parents[1]/'.evidence/tests'/uuid.uuid4().hex
        store=Store(root)
        project=store.create_project('local-git-fixture')
        session=store.create_session(project['id'],'ept')
        workspace=Path(project['workspace'])
        flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
        try:
            subprocess.run([executable,'init',str(workspace)],capture_output=True,check=True,creationflags=flags)
            company=Company(store)
            (workspace/'result.txt').write_text('first result')
            with patch.dict(os.environ,{'GIT_AUTHOR_NAME':'IterLab fixture','GIT_AUTHOR_EMAIL':'fixture@example.invalid','GIT_COMMITTER_NAME':'IterLab fixture','GIT_COMMITTER_EMAIL':'fixture@example.invalid'}):
                result=company.perform(session['id'],{'action':'git_commit','message':'initial result','files':['result.txt']},'commit-1',True)
            self.assertEqual(result['state'],'completed',result)
            branch=company.perform(session['id'],{'action':'git_branch','branch':'experiment/native-kernel'},'branch-1',True)
            self.assertEqual(branch['state'],'completed',branch)
            self.assertIn('experiment/native-kernel',company.git_read(session['id'],'branches')['text'])
            again=company.perform(session['id'],{'action':'git_branch','branch':'experiment/native-kernel'},'branch-1',True)
            self.assertEqual(again,branch)
        finally:
            store.close()


if __name__=='__main__':
    unittest.main()
