import unittest,uuid,json,threading,urllib.request,urllib.error
from pathlib import Path
from lab.store import Store
from lab.kernel import Kernel
from lab.deletion import remove
from tests.test_core import FakeRPC
from lab.deletion import archive
from lab.store import now
from lab.web import Application,Handler
from lab.assets import StaticBundle
from http.server import ThreadingHTTPServer


class ProjectManagementTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(__file__).resolve().parents[1]/'.evidence/tests'/uuid.uuid4().hex
        self.store=Store(self.root);self.kernel=Kernel(self.store,FakeRPC)
        self.project=self.store.create_project('project menu');self.sid=self.store.create_session(self.project['id'],'codex')['id']
    def tearDown(self):self.kernel.close();self.store.close()
    def test_offline_session_without_an_inflight_request_can_remove_project(self):
        self.store.patch_session(self.sid,status='disconnected',thread_id='keep-native-thread')
        remove(self.kernel,'project',self.project['id'])
        self.assertEqual(self.store.navigation()['projects'],[])
        self.assertEqual(self.store.one('SELECT thread_id FROM sessions WHERE id=?',(self.sid,))['thread_id'],'keep-native-thread')
        self.assertIsNone(self.kernel.rpc)
    def test_pin_and_rename_persist_without_moving_files_or_changing_research(self):
        other=self.store.create_project('other')
        evidence=self.store.add_record(self.sid,{'kind':'question','title':'keep','content':'original'}, {})
        self.store.update_project(other['id'],'pin',True)
        self.assertEqual(self.store.navigation()['projects'][0]['id'],other['id'])
        changed=self.store.update_project(self.project['id'],'rename',' renamed ',self.project['title'])
        self.assertEqual(changed['title'],'renamed');self.assertEqual(changed['workspace'],self.project['workspace'])
        self.assertEqual(self.store.one('SELECT * FROM records WHERE id=?',(evidence['id'],)),evidence)
        again=Store(self.root)
        try:self.assertEqual(again.navigation()['projects'][0]['pinned'],1)
        finally:again.close()
        with self.assertRaises(ValueError):self.store.update_project(self.project['id'],'rename','stale','old name')
        with self.assertRaises(ValueError):self.store.update_project(self.project['id'],'rename',' ')
        with self.assertRaises(ValueError):self.store.update_project(other['id'],'pin','false')
        self.store.update_project(other['id'],'pin',False)
        self.assertIsNone(self.kernel.rpc)
    def test_archive_batch_checks_all_chats_before_changing_any(self):
        second=self.store.create_session(self.project['id'],'codex')['id']
        self.store.patch_session(second,status='unknown')
        with self.assertRaises(ValueError):archive(self.kernel,self.project['id'])
        self.assertEqual([s['archived'] for s in self.store.rows('SELECT * FROM sessions')],[0,0])
        self.assertEqual(self.store.rows('SELECT * FROM research_controls'),[])
        self.store.patch_session(second,status='idle')
        result=archive(self.kernel,self.project['id'])
        self.assertEqual(result['count'],2);self.assertEqual(len(self.store.archived_sessions()),2)
        self.assertEqual(len(self.store.navigation()['projects']),1);self.assertEqual(self.store.navigation()['sessions'],[])
        self.assertEqual(archive(self.kernel,self.project['id'])['count'],0)
        self.store.unarchive_session(self.sid)
        self.assertTrue(self.kernel.research.workflow.paused(self.sid))
        self.assertEqual(self.store.navigation()['sessions'][0]['id'],self.sid)
    def test_archiving_one_chat_does_not_hide_or_stop_another_running_chat(self):
        other=self.store.create_session(self.project['id'],'codex')['id'];self.store.patch_session(other,status='running',active_turn='original')
        archive(self.kernel,self.project['id'],self.sid)
        self.assertEqual(self.store.session(other)['active_turn'],'original')
        self.assertFalse(self.kernel.research.workflow.paused(other))
        self.store.patch_session(other,status='idle',active_turn=None)
    def test_offline_but_unknown_delivery_still_blocks_removal(self):
        self.store.patch_session(self.sid,status='disconnected')
        self.store.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)',('unknown',self.sid,'original','queue','unknown',None,None,now(),now()))
        with self.assertRaises(ValueError):remove(self.kernel,'project',self.project['id'])
        self.assertFalse(self.store.deleted('project',self.project['id']))
    def test_removed_project_restore_preserves_archive_and_pause(self):
        archive(self.kernel,self.project['id']);remove(self.kernel,'project',self.project['id'])
        self.assertTrue(self.store.archived_sessions()[0]['project_removed'])
        with self.assertRaises(ValueError):self.store.unarchive_session(self.sid)
        self.store.restore_deleted('project',self.project['id']);self.store.unarchive_session(self.sid)
        self.assertTrue(self.kernel.research.workflow.paused(self.sid));self.assertEqual(self.store.navigation()['sessions'][0]['id'],self.sid)
        with self.assertRaises(ValueError):self.store.restore_deleted('project','missing')
    def test_actual_http_project_menu_cycle_is_local_and_keeps_history(self):
        app=Application.__new__(Application);app.store=self.store;app.kernel=self.kernel;app.token='test-token';app.assets=StaticBundle(Path(__file__).resolve().parents[1]/'static');app.closed=False
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True;server.app=app
        thread=threading.Thread(target=server.serve_forever);thread.start()
        base=f'http://127.0.0.1:{server.server_port}';pid=self.project['id']
        def request(path,data=None):
            req=urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers={'X-Workbench-Token':app.token,'X-Workbench-Assets':app.assets.revision})
            with urllib.request.urlopen(req,timeout=10) as response:return json.load(response)
        try:
            self.assertTrue(request(f'/api/projects/{pid}/manage',{'action':'pin','pinned':True})['pinned'])
            self.assertEqual(request(f'/api/projects/{pid}/manage',{'action':'rename','title':'HTTP renamed'})['title'],'HTTP renamed')
            with self.assertRaises(urllib.error.HTTPError):request(f'/api/projects/{pid}/manage',{'action':'remove'})
            self.assertEqual(request(f'/api/projects/{pid}/manage',{'action':'archive_chats','confirmed':True})['count'],1)
            self.assertEqual(request('/api/archived-sessions')['items'][0]['id'],self.sid)
            request(f'/api/sessions/{self.sid}/unarchive',{})
            request(f'/api/projects/{pid}/manage',{'action':'remove','confirmed':True})
            self.assertEqual(request('/api/state')['projects'],[])
            request('/api/trash/restore',{'kind':'project','id':pid})
            self.assertEqual(len(request('/api/state')['projects']),1)
            self.assertEqual(self.store.rows('SELECT * FROM deliveries'),[]);self.assertIsNone(self.kernel.rpc)
        finally:server.shutdown();thread.join(3);server.server_close()
