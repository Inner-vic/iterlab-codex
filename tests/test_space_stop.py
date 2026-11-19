"""Manual space lifecycle fixtures. No real CLI, credentials, SDK or model."""
import json
from pathlib import Path
import subprocess
import sys
import threading
import types
import unittest
import urllib.error
import urllib.request
import uuid
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from lab.company import Company,redact
from lab.research import Research,plan_digest
from lab.store import Store,dumps,now
from lab.web import Application,Handler
from lab import rounds


class SpaceStopTests(unittest.TestCase):
    def setUp(self):
        self.directory=Path(__file__).resolve().parents[1]/'.evidence/tests'/uuid.uuid4().hex
        self.store=Store(self.directory)
        self.project=self.store.create_project('isolated-space-stop')
        self.sid=self.store.create_session(self.project['id'],'ept')['id']
        self.phase='Running';self.uid='space-fixture';self.response={'status':'ok'}
        self.calls=[];self.before_read=None;self.before_write=None
        self.company=Company(self.store,runner=self.runner,locator=lambda name:name)
        self.research=Research(self.store,self.company)
        self.company.configure({'revision':0,'lpai_enabled':True})
        self.company.bind_space(self.sid,'bound-fixture',self.uid)

    def tearDown(self):
        self.research.close();self.company.close();self.store.close()

    def runner(self,args,**kwargs):
        self.calls.append(args)
        self.assertIn('devspaces',args)
        if 'stop' in args:
            self.assertFalse(self.store.lock._is_owned(),'no global store lock across platform writes')
            pending=rounds.pending_platform_operation(self.store,self.project['id'])
            self.assertEqual(json.loads(pending['request'])['action'],'lpai_stop')
            if self.before_write:self.before_write()
            if isinstance(self.response,Exception):raise self.response
            if self.response=='nonzero':return subprocess.CompletedProcess(args,1,'','lost response after send')
            value=self.response
        else:
            self.assertIn('get',args)
            if self.before_read:self.before_read()
            value={'status':'ok','data':{'metadata':{'uid':self.uid,'name':'bound-fixture','namespace':'vc-chassis'},
                                       'status':{'phase':self.phase},'spec':{}}}
        return subprocess.CompletedProcess(args,0,json.dumps(value),'')

    def stop(self,id='manual-stop',**changes):
        args={'session_id':self.sid,'uid':'space-fixture','operation_id':id,'confirmed':True,
              'expected_authority':self.company.authority(self.sid),**changes}
        return self.company.stop_space(**args)

    def writes(self):return [args for args in self.calls if 'stop' in args]

    def add_run(self,state='running'):
        self.store.execute('INSERT INTO research_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
            ('run-fixture','run-op',self.project['id'],self.sid,'plan','grant','hash','{}',state,'runs/fixture',None,now(),now()))

    def add_operation(self,action='lpai_start',state='pending_approval',id='original'):
        request={'action':action,'bound_targets':[{'kind':'lpai','scope':'vc-chassis','uid':'space-fixture','name':'bound-fixture'}]}
        self.store.execute('INSERT INTO company_operations VALUES(?,?,?,?,?,?,?,?)',
            (id,self.sid,'original-hash',dumps(request),state,None,now(),now()))

    def test_human_route_submits_once_and_official_readback_is_the_only_stopped_evidence(self):
        result=self.stop()
        self.assertEqual(result['state'],'submitted')
        self.assertEqual(self.writes()[0][-5:],['stop','--namespace','vc-chassis','--name','bound-fixture'])
        self.assertEqual(self.stop()['state'],'submitted');self.assertEqual(len(self.writes()),1)
        with self.assertRaisesRegex(ValueError,'未决平台操作'):self.stop('new-stop')
        self.company.refresh_space(self.sid,'space-fixture')
        self.assertEqual(self.company.context(self.sid)['devspace_stop']['operation']['state'],'submitted')
        self.phase='Stopped';self.company.refresh_space(self.sid,'space-fixture')
        context=self.company.context(self.sid)
        self.assertEqual(context['devspace_stop']['operation']['state'],'observed_stopped')
        self.assertEqual(context['devspace_operation']['state'],'observed_stopped')
        self.assertEqual(len(self.writes()),1)

    def test_pending_approval_and_unknown_outcomes_survive_restart_and_never_repeat(self):
        for response in ({'status':'pending_approval'}, {'status':'ok','data':{'status':'pending_approval'}},
                         {'status':'ok','message':'需要确认，请查看飞书'}, {'status':'unexpected'},'nonzero',
                         subprocess.TimeoutExpired('fake-stop',30)):
            with self.subTest(response=response):
                self.store.execute('DELETE FROM company_operations');self.calls=[];self.response=response
                result=self.stop()
                expected='pending_approval' if isinstance(response,dict) and (response.get('status')=='pending_approval' or (response.get('data') or {}).get('status')=='pending_approval') else 'unknown'
                self.assertEqual(result['state'],expected)
                self.company.close()
                self.company=Company(self.store,runner=self.runner,locator=lambda name:name)
                self.assertEqual(self.stop()['state'],expected)
                with self.assertRaises(ValueError):self.stop('new-id')
                self.assertEqual(len(self.writes()),1)
                self.assertEqual(self.company.context(self.sid)['devspace_operation']['state'],expected)

    def test_requested_crash_record_is_retained_until_exact_stopped_uid_readback(self):
        self.add_operation('lpai_stop','requested')
        with self.assertRaises(ValueError):self.stop()
        self.uid='same-name-new-uid';self.phase='Stopped'
        with self.assertRaisesRegex(ValueError,'绑定不一致'):self.company.refresh_space(self.sid,'space-fixture')
        self.assertEqual(self.store.one("SELECT state FROM company_operations WHERE id='original'")['state'],'requested')
        self.uid='space-fixture';self.company.refresh_space(self.sid,'space-fixture')
        self.assertEqual(self.store.one("SELECT state FROM company_operations WHERE id='original'")['state'],'observed_stopped')
        self.assertEqual(self.writes(),[])

    def test_native_action_and_generic_http_action_cannot_stop_even_if_confirmed(self):
        app=Application.__new__(Application);app.store=self.store;app.kernel=types.SimpleNamespace(company=self.company)
        for invoke in (lambda:self.company.perform(self.sid,{'action':'lpai_stop'},'llm-stop',True),
                       lambda:self.company.review_text(self.sid,{'action':'lpai_stop'}),
                       lambda:app.route('POST','/api/company/action',{}, {'session_id':self.sid,'id':'generic-stop',
                           'request':{'action':'lpai_stop'},'confirmed':True,'expected_authority':self.company.authority(self.sid)})):
            with self.assertRaisesRegex(ValueError,'未注册'):invoke()
        self.assertEqual(self.store.rows('SELECT * FROM company_operations'),[]);self.assertEqual(self.writes(),[])

    def test_confirmation_uid_operation_id_and_connection_are_exact(self):
        authority=self.company.authority(self.sid)
        for changes in ({'confirmed':False},{'confirmed':1},{'uid':'foreign-space'},{'operation_id':'bad id'},
                        {'expected_authority':{**authority,'revision':0}}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.stop(**changes)
        self.assertEqual(self.writes(),[]);self.assertEqual(self.store.rows('SELECT * FROM company_operations'),[])
        self.stop()
        self.company.configure({'revision':1,'namespace':'other-scope'})
        with self.assertRaises(ValueError):self.stop()
        self.assertEqual(len(self.writes()),1)

    def test_unknown_transition_not_found_and_recreated_spaces_do_not_send_stop(self):
        for phase in ('Unknown','Starting','Stopping','Pending','Creating','not_found'):
            self.phase=phase
            with self.subTest(phase=phase),self.assertRaisesRegex(ValueError,'状态未知'):self.stop()
        self.phase='Running';self.uid='same-name-new-uid'
        with self.assertRaisesRegex(ValueError,'UID'):self.stop()
        self.assertEqual(self.writes(),[]);self.assertEqual(self.store.rows('SELECT * FROM company_operations'),[])

    def test_explicit_official_not_found_formats_preserve_binding_ssh_and_pending_stop(self):
        project_id=self.project['id']
        self.store.execute('INSERT INTO company_space_ssh VALUES(?,?,?,?,?)',(project_id,'vc-chassis','space-fixture',2222,now()))
        self.add_operation('lpai_stop','unknown')
        ssh=self.store.rows('SELECT * FROM company_space_ssh');operations=self.store.rows('SELECT * FROM company_operations')
        original=self.store.one("SELECT * FROM company_bindings WHERE project_id=? AND kind='lpai'",(project_id,))
        for message in ('Error: HTTP 404: [DEVSPACE_NOT_FOUND] devspace not found',
                        'Error: HTTP 404: [NOT_FOUND] notebooks/bound-fixture not found'):
            with self.subTest(message=message):
                self.store.execute("UPDATE company_bindings SET observation=? WHERE project_id=? AND kind='lpai'",(original['observation'],project_id))
                start=len(self.calls)
                def missing(args,**kwargs):
                    self.calls.append(args)
                    self.assertEqual(args[-6:],['devspaces','get','--namespace','vc-chassis','--name','bound-fixture'])
                    return subprocess.CompletedProcess(args,1,'',message)
                with patch.object(self.company,'runner',side_effect=missing):
                    result=self.company.refresh_space(self.sid,'space-fixture')
                self.assertEqual(len(self.calls)-start,1)
                self.assertEqual(result['item']['phase'],'not_found');self.assertEqual(result['item']['uid'],'space-fixture')
                bound=self.store.one("SELECT * FROM company_bindings WHERE project_id=? AND kind='lpai'",(project_id,))
                self.assertEqual({k:v for k,v in bound.items() if k!='observation'}, {k:v for k,v in original.items() if k!='observation'})
                self.assertEqual(self.store.rows('SELECT * FROM company_space_ssh'),ssh)
                self.assertEqual(self.store.rows('SELECT * FROM company_operations'),operations)
                self.assertFalse(self.company.context(self.sid)['devspace_stop']['available'])
        self.assertEqual(self.writes(),[])

    def test_auth_network_and_unrelated_404_never_mark_space_missing(self):
        original=self.store.rows('SELECT * FROM company_bindings')
        for message in ('Error: HTTP 401: [DEVSPACE_NOT_FOUND] devspace not found',
                        'Error: HTTP 403: [DEVSPACE_NOT_FOUND] devspace not found',
                        'Error: HTTP 404: [NOT_FOUND] notebooks/another-space not found',
                        'Error: HTTP 404: [NOT_FOUND] devspace not found',
                        'Error: HTTP 404: [VOLUME_NOT_FOUND] devspace not found',
                        'Error: HTTP 404: [DEVSPACE_NOT_FOUND] permission denied',
                        'Error: HTTP 404: [DEVSPACE_NOT_FOUND] devspace not found on another endpoint',
                        'Error: connection reset after devspace request',
                        'Error: [DEVSPACE_NOT_FOUND] devspace not found'):
            with self.subTest(message=message),patch.object(self.company,'runner',return_value=subprocess.CompletedProcess([],1,'',message)):
                with self.assertRaises(ValueError):self.company.refresh_space(self.sid,'space-fixture')
            self.assertEqual(self.store.rows('SELECT * FROM company_bindings'),original)
        with patch.object(self.company,'runner',side_effect=subprocess.TimeoutExpired('fake-get',30)),self.assertRaises(TimeoutError):
            self.company.refresh_space(self.sid,'space-fixture')
        self.assertEqual(self.store.rows('SELECT * FROM company_bindings'),original)
        self.assertEqual(self.writes(),[])

    def test_missing_read_does_not_overwrite_a_changed_binding_or_newer_observation(self):
        project_id=self.project['id']
        for change in ('binding','observation'):
            with self.subTest(change=change):
                original=self.store.one("SELECT * FROM company_bindings WHERE project_id=? AND kind='lpai'",(project_id,))
                changed={**original,'uid':'rebound-space'} if change=='binding' else {**original,'observation':dumps({**json.loads(original['observation']),'phase':'Stopped'})}
                def missing(args,**kwargs):
                    self.store.execute("UPDATE company_bindings SET uid=?,observation=? WHERE project_id=? AND kind='lpai'",
                        (changed['uid'],changed['observation'],project_id))
                    return subprocess.CompletedProcess(args,1,'','Error: HTTP 404: [DEVSPACE_NOT_FOUND] devspace not found')
                with patch.object(self.company,'runner',side_effect=missing),self.assertRaisesRegex(ValueError,'绑定或观测已变化'):
                    self.company.refresh_space(self.sid,'space-fixture')
                self.assertEqual(self.store.one("SELECT * FROM company_bindings WHERE project_id=? AND kind='lpai'",(project_id,)),changed)
                self.store.execute("UPDATE company_bindings SET uid=?,observation=? WHERE project_id=? AND kind='lpai'",
                    (original['uid'],original['observation'],project_id))
        self.assertEqual(self.writes(),[])

    def test_missing_read_does_not_mark_old_binding_when_connection_changed(self):
        original=self.store.rows('SELECT * FROM company_bindings')
        def missing(args,**kwargs):
            self.company.configure({'revision':1,'namespace':'other-scope'})
            return subprocess.CompletedProcess(args,1,'','Error: HTTP 404: [DEVSPACE_NOT_FOUND] devspace not found')
        with patch.object(self.company,'runner',side_effect=missing),self.assertRaisesRegex(ValueError,'连接已变化'):
            self.company.refresh_space(self.sid,'space-fixture')
        self.assertEqual(self.store.rows('SELECT * FROM company_bindings'),original)
        self.assertEqual(self.writes(),[])

    def test_already_stopped_returns_read_evidence_without_sending(self):
        self.phase='Stopped'
        result=self.stop();self.assertEqual(result['state'],'already_stopped');self.assertEqual(self.writes(),[])

    def test_start_pending_is_visible_and_stop_pending_prevents_start(self):
        self.add_operation()
        self.assertEqual(self.company.context(self.sid)['devspace_operation']['state'],'pending_approval')
        with self.assertRaisesRegex(ValueError,'未决平台操作'):self.stop()
        self.store.execute('DELETE FROM company_operations');self.stop()
        with self.assertRaisesRegex(ValueError,'不会用新操作ID重复发送'):
            self.company.perform(self.sid,{'action':'lpai_start'},'start-during-stop',True)
        self.assertFalse(any('start' in call for call in self.calls))

    def test_project_execution_training_tasks_and_unknown_states_block_without_mutation(self):
        other=self.store.create_session(self.project['id'],'ept')['id']
        def train(state):self.store.execute('INSERT INTO company_training VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            ('training',other,self.project['id'],'plan','trial','component',None,'{}',state,None,now(),now()))
        def watch(state):self.store.execute('INSERT INTO company_watches VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            ('watch',other,'vc-chassis','task','task',state,'{}',0,0,'none',now()))
        scenarios=[(lambda:self.store.patch_session(other,active_turn='active'),lambda:self.store.patch_session(other,active_turn=None)),
                   (lambda:self.add_run('unknown'),lambda:self.store.execute('DELETE FROM research_runs')),
                   (lambda:self.add_run('unrecognized_phase'),lambda:self.store.execute('DELETE FROM research_runs')),
                   (lambda:train('unknown'),lambda:self.store.execute('DELETE FROM company_training')),
                   (lambda:watch('Running'),lambda:self.store.execute('DELETE FROM company_watches')),
                   (lambda:watch('unrecognized_phase'),lambda:self.store.execute('DELETE FROM company_watches'))]
        for setup,cleanup in scenarios:
            setup();sessions=self.store.rows('SELECT * FROM sessions')
            status=self.company.context(self.sid)['devspace_stop']
            self.assertFalse(status['available']);self.assertTrue(all(b['message'] for b in status['blockers']))
            with self.assertRaises(ValueError):self.stop()
            self.assertEqual(self.store.rows('SELECT * FROM sessions'),sessions)
            self.assertEqual(self.writes(),[]);self.assertEqual(self.store.rows('SELECT * FROM company_operations'),[])
            cleanup()

    def test_run_registered_during_read_cannot_cross_stop_boundary(self):
        self.before_read=lambda:self.add_run('requested')
        with self.assertRaisesRegex(ValueError,'实验'):self.stop()
        self.assertEqual(self.writes(),[]);self.assertEqual(self.store.rows('SELECT * FROM company_operations'),[])

    def test_unresolved_stop_blocks_real_research_entry_before_any_process(self):
        plan=self.store.add_record(self.sid,{'kind':'plan','title':'isolated approved plan','content':'fixture','status':'proposed'}, {})
        self.research.review(self.sid,{'plan_id':plan['id'],'expected_digest':plan_digest(plan),'decision':'accept','reason':'fixture only',
            'execution':'local','allow_local':True,'python':sys.executable,'max_runs':1,'max_fits':0,'timeout':3})
        workspace=Path(self.store.session(self.sid)['workspace']);(workspace/'probe.py').write_text('raise AssertionError("must never launch")')
        self.stop()
        with patch('subprocess.run',side_effect=AssertionError('no process')),patch('subprocess.Popen',side_effect=AssertionError('no child')):
            with self.assertRaisesRegex(ValueError,'未决平台操作'):
                self.research.run(self.sid,{'plan_id':plan['id'],'script':'probe.py','files':['probe.py'],'args':[],
                    'purpose':'diagnostic','question':'fixture','expected_information':'fixture','outputs':[]},'blocked-run')
        self.assertEqual(self.store.rows('SELECT * FROM research_runs'),[])

    def test_bare_lpaki_token_is_redacted_in_cli_failures(self):
        fake='lpak_' + 'fixture_only_not_a_secret-123'
        self.assertNotIn(fake,redact('upstream failure: '+fake))
        with patch.object(self.company,'runner',return_value=subprocess.CompletedProcess([],1,'','upstream failure: '+fake)):
            with self.assertRaises(ValueError) as error:self.company.lpai('space',name='bound-fixture')
        self.assertNotIn(fake,str(error.exception));self.assertIn('[redacted]',str(error.exception))


class SpaceStopHTTPTests(unittest.TestCase):
    setUp=SpaceStopTests.setUp
    tearDown=SpaceStopTests.tearDown
    runner=SpaceStopTests.runner
    writes=SpaceStopTests.writes
    # Keep the HTTP checks separate from the scripted state-machine checks.
    def test_human_stop_route_enforces_auth_origin_exact_fields_and_confirmation(self):
        app=Application.__new__(Application);app.store=self.store;app.kernel=types.SimpleNamespace(company=self.company)
        app.token='isolated-stop-token';app.closed=False
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.app=app
        thread=threading.Thread(target=server.serve_forever);thread.start()
        body={'session_id':self.sid,'uid':'space-fixture','id':'http-stop','confirmed':True,'expected_authority':self.company.authority(self.sid)}
        def request(value,token=True,origin=None,path='/api/company/stop-space'):
            headers={'X-Workbench-Token':app.token} if token else {}
            if origin:headers['Origin']=origin
            req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}'+path,data=json.dumps(value).encode(),headers=headers)
            return urllib.request.urlopen(req,timeout=3)
        try:
            for payload,token,origin,code in ((body,False,None,403),(body,True,'https://unrelated.invalid',403),
                    ({**body,'confirmed':False},True,None,400),({**body,'command':'stop all'},True,None,400),
                    ({k:v for k,v in body.items() if k!='expected_authority'},True,None,400)):
                with self.subTest(payload=payload,token=token,origin=origin),self.assertRaises(urllib.error.HTTPError) as error:
                    request(payload,token,origin)
                self.assertEqual(error.exception.code,code)
            self.assertEqual(self.writes(),[])
            with request(body) as response:result=json.load(response)
            self.assertEqual(result['state'],'submitted');self.assertEqual(len(self.writes()),1)
            with request(body) as response:result=json.load(response)
            self.assertEqual(result['state'],'submitted');self.assertEqual(len(self.writes()),1)
        finally:server.shutdown();thread.join(3);server.server_close()


if __name__=='__main__':unittest.main()
