import json
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
import sys
import tempfile
import queue
from unittest.mock import patch
from pathlib import Path
from http.server import ThreadingHTTPServer

from lab.transport import CodexRPC, RPCUnknown
from lab.web import Application, Handler
from test_core import FakeRPC, BarrierRPC
from lab.kernel import Kernel


class TransportTests(unittest.TestCase):
    def test_invalid_bootstrap_budget_rejected_before_process_start(self):
        for value in (0,-1,121,True,'60',float('nan'),float('inf')):
            rpc=CodexRPC([],Path('.'),lambda p:None,lambda p,r:None,lambda p:None)
            try:
                with self.subTest(value=value),self.assertRaises(ValueError):rpc.start(timeout=value)
                self.assertIsNone(rpc.process)
            finally:rpc.close()

    def test_real_stdio_process_correlation_and_exit(self):
        root=Path(__file__).resolve().parents[1]
        events,exits=[],[]
        rpc=CodexRPC([sys.executable,str(root/'tests/fake_rpc_server.py')],root,events.append,lambda p,r:None,exits.append)
        try:
            self.assertEqual(rpc.start()['userAgent'],'test')
            self.assertEqual(rpc.request('echo',{'value':'hello'})['echo'],{'value':'hello'})
            rpc.request('emit',{})
            for _ in range(100):
                if events:
                    break
                time.sleep(.01)
            self.assertEqual(events[0]['method'],'turn/completed')
            with self.assertRaises(RPCUnknown):
                rpc.request('crash',{},timeout=3)
        finally:
            rpc.close()
        self.assertIsNotNone(rpc.process.poll())


class TrackedTransportTests(unittest.TestCase):
    def peer(self):
        rpc = CodexRPC([], Path(tempfile.gettempdir()), lambda p: None, lambda p, r: None, lambda p: None)
        self.addCleanup(rpc.close)
        return rpc

    def test_timed_out_request_keeps_exact_late_ack_once(self):
        rpc = self.peer(); sent, receipts = [], []
        with patch.object(rpc, '_send', side_effect=sent.append):
            with self.assertRaises(RPCUnknown):
                rpc.request_tracked('turn/start', {'clientUserMessageId': 'original'}, lambda p, late: receipts.append((p, late)), timeout=0)
        self.assertEqual(len(sent), 1)
        request_id = sent[0]['id']
        self.assertIn(request_id, rpc.late_pending)
        packet = {'id': request_id, 'result': {'turn': {'id': 'exact-turn'}}}
        rpc._handle_response(packet); rpc._handle_response(packet)
        self.assertEqual(receipts, [(packet, True)])
        self.assertEqual(rpc.pending, {}); self.assertEqual(rpc.late_pending, {})

    def test_reply_before_waiter_timeout_does_not_lose_or_duplicate_receipt(self):
        rpc = self.peer(); receipts = []
        def reply(packet):
            rpc._handle_response({'id': packet['id'], 'result': {'turn': {'id': 'exact-turn'}}})
        with patch.object(rpc, '_send', side_effect=reply):
            result = rpc.request_tracked('turn/start', {}, lambda p, late: receipts.append((p, late)), timeout=0)
        self.assertEqual(result['turn']['id'], 'exact-turn')
        self.assertEqual(len(receipts), 1); self.assertFalse(receipts[0][1])
        self.assertEqual(rpc.late_pending, {})

    def test_plain_timeout_does_not_allocate_write_receipt_or_retry(self):
        rpc = self.peer(); sent = []
        with patch.object(rpc, '_send', side_effect=sent.append):
            with self.assertRaises(RPCUnknown): rpc.request('thread/read', {}, timeout=0)
        self.assertEqual(len(sent), 1); self.assertEqual(rpc.late_pending, {})

    def test_failed_receipt_persistence_is_unknown_not_reader_exit(self):
        rpc = self.peer()
        def broken_receipt(packet, late): raise RuntimeError('fixture persistence failure')
        def reply(packet): rpc._handle_response({'id': packet['id'], 'result': {'turn': {'id': 'exact-turn'}}})
        with patch.object(rpc, '_send', side_effect=reply), self.assertRaises(RPCUnknown):
            rpc.request_tracked('turn/start', {}, broken_receipt, timeout=0)
        self.assertEqual(rpc.pending, {})


class WebTests(unittest.TestCase):
    def test_screenshot_resource_import_endpoint_is_disabled(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/api/company/resource-queue-observation',{'queues':[],'evidence_sha256':'a'*64})
        self.assertEqual(error.exception.code,400)
        self.assertIsNone(self.app.store.one("SELECT name FROM sqlite_master WHERE name='component_queue_observations'"))

    def test_resource_selection_rechecks_the_account_that_loaded_the_page(self):
        store=self.app.store;company=self.app.kernel.company
        company.configure({**company.config(),'lpai_enabled':True})
        project=store.create_project('account-scope');sid=store.create_session(project['id'],'ept')['id']
        current={'subject':'account-b','namespace':company.config()['namespace'],'connection_revision':company.config()['revision']}
        directory={'source':'official_account_resources','live':True,'account_scope':current,'digest':'a'*64,
                   'queues':[{'key':'same-key','queue_code':'code-b','name':'same display name','resources':[]}]}
        with patch.object(company.resource_directory,'catalog',return_value=directory):
            with self.assertRaises(urllib.error.HTTPError) as rejected:
                self.request('/api/company/resource-queue-preference',{'session_id':sid,'queue_key':'same-key','expected_account_scope':{**current,'subject':'account-a'}})
        self.assertEqual(rejected.exception.code,400)
        self.assertEqual(store.rows('SELECT * FROM component_resource_preferences'),[])

    def test_large_native_output_is_deferred_without_mutating_original_and_is_scoped(self):
        store=self.app.store;project=store.create_project('large-output-fixture');sid=store.create_session(project['id'],'ept')['id']
        key=store.message(sid,'large-output','tool','output','x'*1000000,'completed','old-turn')
        store.message(sid,'visible','assistant','agentMessage','仍然可读','completed','new-turn')
        with self.request('/api/sessions/'+sid) as response:
            body=response.read();self.assertLess(len(body),100000)
        self.assertNotIn('large-output',[m['item_id'] for m in json.loads(body)['messages']]);self.assertIn('仍然可读',body.decode())
        with self.request('/api/sessions/'+sid+'/messages/'+key) as response:
            self.assertEqual(len(json.load(response)['message']['text']),1000000)
        other=store.create_session(project['id'],'ept')['id']
        with self.assertRaises(urllib.error.HTTPError):self.request('/api/sessions/'+other+'/messages/'+key)

    def test_versioned_assets_use_lossless_gzip_cache_but_html_never_caches_token(self):
        import gzip
        path='/assets/'+self.app.assets.revision+'/vendor/exploration-map.js'
        with self.request(path,extra={'Accept-Encoding':'gzip'}) as response:
            body=response.read();self.assertEqual(response.headers.get('Content-Encoding'),'gzip')
            self.assertIn('immutable',response.headers['Cache-Control'])
            self.assertEqual(gzip.decompress(body),self.app.assets.read(path))
        with self.request(path,extra={'Accept-Encoding':'gzip;q=0'}) as response:
            self.assertIsNone(response.headers.get('Content-Encoding'))
        with self.request('/') as response:self.assertEqual(response.headers['Cache-Control'],'no-store')

    def test_sse_defers_large_payload_but_preserves_event_cursor_and_source(self):
        store=self.app.store;store.event(None,'large.fixture',{'output':'z'*100000})
        original=store.one("SELECT * FROM events WHERE kind='large.fixture'")
        with self.request('/api/events?after='+str(original['seq']-1)) as response:
            line=response.readline();self.assertLess(len(line),1000)
            event=json.loads(line.decode()[6:]);self.assertEqual(event['seq'],original['seq'])
            self.assertTrue(event['payload']['details_deferred'])
        self.assertEqual(store.one('SELECT * FROM events WHERE seq=?',(original['seq'],)),original)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='iterlab-web-')
        root = Path(self.directory.name)
        def kernel_factory(store):
            kernel = Kernel(store, FakeRPC)
            kernel.settings.command = lambda *args: ['fixture']
            kernel.settings.verify_channel = lambda: None
            kernel.settings.value['channel'] = 'codex'
            return kernel
        self.app = Application(root, kernel_factory)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.daemon_threads=True
        self.server.app=self.app
        self.thread=threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}'
    def tearDown(self):
        self.server.shutdown()
        self.thread.join(3)
        self.server.server_close()
        try:
            self.app.close()
        finally:
            self.directory.cleanup()
    def request(self,path,data=None,token=True,extra=None):
        headers={'X-Workbench-Token':self.app.token} if token else {}
        headers.update(extra or {})
        req=urllib.request.Request(self.base+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
        return urllib.request.urlopen(req,timeout=3)
    def test_browser_token_origin_and_static_traversal(self):
        with self.assertRaises(urllib.error.HTTPError) as denied:
            self.request('/api/state',token=False)
        self.assertEqual(denied.exception.code,403)
        with self.assertRaises(urllib.error.HTTPError):
            self.request('/api/projects',{'title':'x'},extra={'Origin':'https://unrelated.example'})
        with self.assertRaises(urllib.error.HTTPError) as traversal:
            self.request('/../lab/settings.py')
        self.assertEqual(traversal.exception.code,404)
        with self.request('/') as response:
            self.assertIn(b'workbench-token',response.read())

    def test_service_shutdown_does_not_implicitly_cancel_submitted_local_compute(self):
        from lab.store import now
        store=self.app.store
        project=store.create_project('running compute');sid=store.create_session(project['id'],'ept')['id']
        plan=store.add_record(sid,{'kind':'plan','title':'工程运行身份','content':'模拟已提交的本地任务，不启动进程。'}, {})
        store.execute('INSERT INTO research_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
            ('fixture-active','fixture-op',project['id'],sid,plan['id'],'fixture-grant','fixture-hash','{}','running','runs/fixture',None,now(),now()))
        with self.assertRaises(urllib.error.HTTPError) as blocked:
            self.request('/api/service/stop',{})
        self.assertEqual(blocked.exception.code,409)
        with self.request('/api/state') as response:self.assertEqual(response.status,200)
        self.assertEqual(store.one('SELECT state FROM research_runs')['state'],'running')
    def test_model_catalog_http_and_page_assets_share_one_host_version(self):
        self.app.kernel.settings.value['channel']='codex'
        self.app.kernel.settings.command=lambda *args:['fixture']
        revision=self.app.assets.revision
        with self.request('/') as response:html=response.read().decode()
        self.assertIn('content="'+revision+'"',html)
        self.assertIn('/assets/'+revision+'/app.js',html)
        with self.request('/assets/'+revision+'/app.js') as response:
            self.assertIn(b"api('/api/models'",response.read())
        with self.request('/api/models',{'channel':'codex'},extra={'X-Workbench-Assets':revision}) as response:
            self.assertEqual(len(json.load(response)['models']),4)
        with self.assertRaises(urllib.error.HTTPError) as stale:
            self.request('/api/models',{'channel':'codex'},extra={'X-Workbench-Assets':'old-page'})
        self.assertEqual(stale.exception.code,409)
        self.assertEqual(self.app.store.rows('SELECT * FROM deliveries'),[])
        self.assertIsNone(self.app.kernel.rpc) # Catalog lookup never creates a research runtime.
    def test_platform_check_uses_shared_health_and_does_not_invoke_codex(self):
        from unittest.mock import patch
        company=self.app.kernel.company
        # No command/dependency needed: failures and success stay within the same API.
        with patch.object(company,'locator',return_value='fixture'):
            company.configure({'revision':0,'lpai_enabled':True})
        with patch.object(company,'lpai',return_value={'items':[]}),patch.object(company.training,'sdk',side_effect=TimeoutError('timeout')):
            with self.request('/api/company/check',{'platform':'lpai'}) as response:result=json.load(response)
        self.assertEqual([r['state'] for r in result['checks']],['passed','failed'])
        with self.request('/api/company') as response:status=json.load(response)
        self.assertEqual(len(status['health']),2)
        self.assertEqual(self.app.store.rows('SELECT * FROM deliveries'),[])
        self.assertEqual(self.app.store.rows('SELECT * FROM company_training'),[])
    def test_autonomy_http_scope_and_human_fork_keep_original_plan(self):
        from lab.research import plan_digest
        store=self.app.store;research=self.app.kernel.research
        project=store.create_project('authority-http');sid=store.create_session(project['id'],self.app.kernel.settings.value['channel'])['id']
        plan=store.add_record(sid,{'kind':'plan','title':'初始方案','content':'原始研究方案'}, {})
        research.review(sid,{'plan_id':plan['id'],'expected_digest':plan_digest(plan),'decision':'accept','reason':'隔离程序验证','allow_local':True,'max_runs':3,'max_fits':2,'timeout':5})
        scope={'objective':'隔离验证','evaluation':'固定评价','data_boundary':'本地控制数据','allowed_changes':'验证分支','frozen_files':[]}
        with self.assertRaises(urllib.error.HTTPError):self.request('/api/research/autonomy',{'session_id':sid,'action':'enable','plan_id':plan['id'],'expected_digest':plan_digest(plan),'scope':scope,'reason':'明确授权'})
        with self.request('/api/research/autonomy',{'session_id':sid,'action':'enable','plan_id':plan['id'],'expected_digest':plan_digest(plan),'scope':scope,'reason':'明确授权','confirmed':True}) as response:
            self.assertEqual(json.load(response)['mode'],'autonomous')
        with self.request('/api/research/autonomy',{'session_id':sid,'action':'fork','from_plan_id':plan['id'],'title':'人工修订','content':'保留原评价，尝试另一方向','reason':'人工提出新假设','activate':False,'confirmed':True}) as response:
            candidate=json.load(response)
        self.assertFalse(candidate['activated']);self.assertEqual(store.one('SELECT content FROM records WHERE id=?',(plan['id'],))['content'],'原始研究方案')
        self.assertEqual(store.rows('SELECT * FROM deliveries'),[])
    def test_project_attachment_file_and_persistent_state(self):
        import base64
        with self.request('/api/projects',{'title':'project'}) as response:
            project=json.load(response)
        with self.request(f"/api/projects/{project['id']}/upload",{'name':'notes.md','content':base64.b64encode(b'hello').decode()}) as response:
            attachment=json.load(response)
        with self.request(f"/api/projects/{project['id']}/file?path={attachment['path']}") as response:
            self.assertEqual(json.load(response)['text'],'hello')
        with self.request('/api/state') as response:
            self.assertEqual(len(json.load(response)['projects']),1)
        with self.assertRaises(urllib.error.HTTPError):
            self.request(f"/api/projects/{project['id']}/file?path=../../settings.json")
    def test_method_preview_and_explicit_upgrade_keep_history_without_starting_kernel(self):
        from lab.sdm import SDM
        store=self.app.store
        project=store.create_project('legacy-method');sid=store.create_session(project['id'],'ept')['id']
        self.app.kernel.sdm=SDM(store,default_mode='native_skill')
        original=self.app.kernel.sdm.binding(sid)
        old=store.one('SELECT * FROM research_methods')
        with self.request('/api/research/status',{'session_id':sid}) as response:
            preview=json.load(response)
        self.assertEqual(preview['method']['mode'],'native_skill')
        self.assertTrue(preview['method_update']['available'])
        self.assertEqual(store.rows('SELECT * FROM research_method_bindings'),[])
        data={'session_id':sid,'expected_digest':original['digest'],'reason':'用户确认研究合同迁移'}
        with self.assertRaises(urllib.error.HTTPError):self.request('/api/research/method-upgrade',data)
        with self.request('/api/research/method-upgrade',{**data,'confirmed':True}) as response:
            self.assertTrue(json.load(response)['changed'])
        self.assertEqual(store.one('SELECT * FROM research_methods'),old)
        self.assertEqual(store.session(sid)['thread_id'],None)
        self.assertEqual(store.rows('SELECT * FROM research_runs'),[])
        with self.request('/api/research/status',{'session_id':sid}) as response:
            updated=json.load(response)
        self.assertEqual(updated['method']['mode'],'builtin')
        self.assertFalse(updated['method_update']['available'])

    def test_record_inspection_exposes_changed_evidence_before_review(self):
        store=self.app.store;project=store.create_project('record-inspection')
        sid=store.create_session(project['id'],'ept')['id']
        evidence=Path(project['workspace'])/'proposal.md';evidence.write_text('original',encoding='utf-8')
        record=store.add_record(sid,{'kind':'plan','title':'proposal','content':'immutable body','evidence':['proposal.md']},{})
        with self.request('/api/records/'+record['id']) as response:self.assertTrue(json.load(response)['integrity']['valid'])
        evidence.write_text('later version annotation',encoding='utf-8')
        with self.request('/api/records/'+record['id']) as response:
            result=json.load(response)
        self.assertFalse(result['integrity']['valid'])
        self.assertEqual(result['document']['content'],'immutable body')
        self.assertEqual(store.rows('SELECT * FROM research_reviews'),[])
        self.assertEqual(store.rows('SELECT * FROM research_grants'),[])


class DeliveryHTTPTests(unittest.TestCase):
    setUp = WebTests.setUp
    tearDown = WebTests.tearDown
    request = WebTests.request

    def session(self):
        store = self.app.store
        project = store.create_project('delivery-http')
        return store.create_session(project['id'], 'codex')['id']

    def read_json(self, path, data=None):
        with self.request(path, data) as response:
            return json.load(response)

    def wait(self, predicate):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if predicate(): return
            time.sleep(.005)
        self.fail('HTTP fixture deadline expired')

    def test_research_status_exposes_exact_project_and_live_role_guard(self):
        sid=self.session();kernel=self.app.kernel
        kernel.settings.value['role_handoff_no_progress_limit']=3
        result=self.read_json('/api/research/status',{'session_id':sid})
        self.assertEqual(result['project_id'],self.app.store.session(sid)['project_id'])
        self.assertEqual(result['agent_roles']['no_progress_guard']['limit'],3)
        self.assertFalse(result['agent_roles']['role_routing_grants_execution'])
        self.assertIn('execution_authorization',result)
        self.assertIsNone(kernel.rpc)

    def test_send_errors_report_only_exact_registration_evidence(self):
        sid=self.session();store=self.app.store
        path=f'/api/sessions/{sid}/send'
        with self.assertRaises(urllib.error.HTTPError) as invalid:
            self.request(path,{'id':'invalid-original','text':'','mode':'queue'})
        result=json.load(invalid.exception)
        self.assertEqual(result['delivery'],{'id':'invalid-original','session_id':sid,'state':'not_registered'})
        self.assertEqual(store.rows('SELECT * FROM deliveries'),[])
        original=self.read_json(path,{'id':'original','text':'saved text'})
        with self.assertRaises(urllib.error.HTTPError) as conflict:
            self.request(path,{'id':'original','text':'different text'})
        self.assertEqual(json.load(conflict.exception)['delivery']['id'],original['id'])
        self.assertNotEqual(store.one("SELECT state FROM deliveries WHERE id='original'")['state'],'not_registered')
        with patch.object(self.app.kernel,'send',side_effect=RuntimeError('after registration')):
            with self.assertRaises(urllib.error.HTTPError) as after:
                self.request(path,{'id':'original','text':'saved text'})
        self.assertNotEqual(json.load(after.exception)['delivery']['state'],'not_registered')
        self.assertEqual(len(store.rows('SELECT * FROM deliveries')),1)

    def test_snapshot_cursor_waits_for_its_delta_projection(self):
        sid=self.session();kernel=self.app.kernel;store=self.app.store
        store.patch_session(sid,thread_id='snapshot-thread')
        store.message(sid,'stream','assistant','message','before','streaming','turn')
        entered,release,reading=threading.Event(),threading.Event(),threading.Event()
        errors=[];snapshots=[];message=store.message
        def delayed(*args,**kwargs):
            entered.set()
            if not release.wait(3):raise AssertionError('projection deadline expired')
            return message(*args,**kwargs)
        def emit():
            try:kernel.on_event({'method':'item/agentMessage/delta','params':{'threadId':'snapshot-thread','turnId':'turn','itemId':'stream','delta':' after'}})
            except Exception as exc:errors.append(exc)
        def snapshot():
            reading.set()
            try:snapshots.append(self.read_json('/api/sessions/'+sid))
            except Exception as exc:errors.append(exc)
        with patch.object(store,'message',side_effect=delayed):
            writer=threading.Thread(target=emit);reader=threading.Thread(target=snapshot)
            writer.start()
            try:
                self.assertTrue(entered.wait(2));reader.start();self.assertTrue(reading.wait(2))
                reader.join(.05);self.assertTrue(reader.is_alive(),'snapshot returned before its text projection')
            finally:
                release.set();writer.join(3)
                if reader.ident is not None:reader.join(3)
        self.assertEqual(errors,[])
        self.assertFalse(writer.is_alive());self.assertFalse(reader.is_alive())
        row=next(m for m in snapshots[0]['messages'] if m['item_id']=='stream')
        self.assertEqual(row['text'],'before after')
        seq=store.one("SELECT seq FROM events WHERE kind='item/agentMessage/delta'")['seq']
        self.assertGreaterEqual(snapshots[0]['cursor'],seq)

    def test_delivery_inspection_is_session_scoped_read_only_and_nine_columns(self):
        sid = self.session(); store = self.app.store; stamp = 'fixture timestamp'
        columns = ['id', 'session_id', 'text', 'mode', 'state', 'turn_id', 'error', 'created_at', 'updated_at']
        self.assertEqual([row['name'] for row in store.rows('PRAGMA table_info(deliveries)')], columns)
        for state in ['queued', 'requested', 'accepted', 'unknown', 'rejected', 'cancelled']:
            store.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)', (state, sid, state, 'queue', state, 'turn-' + state if state == 'accepted' else None, 'original error', stamp, stamp))
            store.execute('INSERT INTO research_input_origins VALUES(?,?,?)', (state, 'user' if state != 'requested' else 'background', stamp))
        other = self.session()
        self.read_json('/api/connect', {})
        with self.assertRaises(urllib.error.HTTPError) as denied:
            self.request(f'/api/sessions/{sid}/deliveries/accepted', token=False)
        self.assertEqual(denied.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as hostile:
            self.request(f'/api/sessions/{sid}/deliveries/accepted', extra={'Origin': 'https://unrelated.example'})
        self.assertEqual(hostile.exception.code, 403)
        before = store.rows('SELECT * FROM deliveries'); events = store.rows('SELECT * FROM events')
        calls = list(self.app.kernel.rpc.calls)
        for row in before:
            self.assertEqual(self.read_json(f"/api/sessions/{sid}/deliveries/{row['id']}"), row)
        with self.assertRaises(urllib.error.HTTPError) as cross:
            self.request(f'/api/sessions/{other}/deliveries/accepted')
        self.assertEqual(cross.exception.code, 400)
        self.assertEqual(store.rows('SELECT * FROM deliveries'), before)
        self.assertEqual(store.rows('SELECT * FROM events'), events, 'delivery GET must have no projection/reconciliation side effects')
        self.assertEqual(self.app.kernel.rpc.calls, calls, 'delivery GET cannot call native RPC')
        response = self.read_json('/api/sessions/' + sid)
        self.assertEqual(set(response['deliveries'][0]), set(columns))
        self.assertEqual({row['state'] for row in response['deliveries']}, {'queued', 'requested', 'unknown', 'rejected'})
        self.assertTrue(response['delivery_controls']['barrier'])
        self.assertEqual(response['delivery_controls']['unknown_delivery_ids'], ['unknown'])
        self.assertEqual(response['delivery_origins']['requested'], 'background')
        self.assertEqual(response['delivery_origins']['accepted'], 'user')
        self.assertEqual(store.rows('SELECT * FROM deliveries'), before)
        # Session projections may add their own audit; delivery GET never did.
        self.assertTrue(all(row in store.rows('SELECT * FROM events') for row in events))

    def test_session_transcript_hides_system_deliveries_and_legacy_echoes(self):
        sid=self.session();store=self.app.store;stamp='fixture timestamp'
        text='[角色交接事件 · 系统，非人类新授权]\n{"id":"internal"}'
        for id,origin,body in [('handoff','background',text),('review','review','review body'),('typed','user',text)]:
            store.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)',(id,sid,body,'queue','accepted','turn-'+id,None,stamp,stamp))
            store.execute('INSERT INTO research_input_origins VALUES(?,?,?)',(id,origin,stamp))
            store.message(sid,'delivery:'+id,'user','message',body,'completed','turn-'+id)
        store.message(sid,'native-echo','user','message',text,'completed','turn-handoff')
        store.message(sid,'native-human','user','message','直接发给 Codex 的消息','completed','native-turn')
        store.message(sid,'reply','assistant','message','Codex 的答复','completed','turn-handoff')
        store.execute('INSERT INTO research_role_handoffs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            ('internal-call',sid,'typed','executor','internal-plan','digest','internal-record','handoff',
             '先核对工作包 12345678123456781234567812345678，再提交。','handoff','dispatched',stamp))
        before=store.rows('SELECT * FROM messages WHERE session_id=?',(sid,))
        response=self.read_json('/api/sessions/'+sid)
        self.assertEqual([m['item_id'] for m in response['messages'] if m['role']!='system'],['delivery:typed','native-human','reply'])
        card=next(m for m in response['messages'] if m['kind']=='role_handoff')
        self.assertEqual(card['label'],'系统 → 执行');self.assertEqual(card['text'],'正在核对运行条件，准备执行实验。');self.assertNotIn('12345678',card['text'])
        self.assertNotIn('internal-call',json.dumps(card))
        self.assertEqual(store.rows('SELECT * FROM messages WHERE session_id=?',(sid,)),before)

    def test_finalization_guard_before_role_contract_is_not_a_human_message(self):
        sid=self.session();store=self.app.store;stamp='2026-11-04T12:00:00+00:00'
        body='[研究只读收尾事件 · 系统，非新授权] 原身份：internal'
        prefix='[宿主持久角色合同 v1]\nprivate routing contract'
        guard='本轮仅只读收尾：核对现有证据，保存结束评审并报告；不得交接、写代码或新增计算。'
        store.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)',('finalize',sid,body,'queue','accepted','original-turn',None,stamp,stamp))
        store.execute('INSERT INTO research_input_origins VALUES(?,?,?)',('finalize','background',stamp))
        store.execute('INSERT INTO research_role_envelopes VALUES(?,?)',('finalize',prefix))
        store.execute('INSERT INTO research_finalizations VALUES(?,?,?,?,?,?,?,?,?)',('finalize',sid,'authority','branch','plan','generation','no_progress',body,stamp))
        full=guard+'\n'+prefix+'\n'+body
        store.message(sid,'native-final-echo','user','message',full,'completed','original-turn')
        store.message(sid,'human-quotation','user','message',full,'completed','different-human-turn')
        before=store.rows('SELECT * FROM messages WHERE session_id=?',(sid,))
        response=self.read_json('/api/sessions/'+sid)
        self.assertNotIn('native-final-echo',[m.get('item_id') for m in response['messages']])
        self.assertIn('human-quotation',[m.get('item_id') for m in response['messages']])
        note=next(m for m in response['messages'] if m['kind']=='research_finalization')
        self.assertEqual(note['text'],'自动推进暂未取得有效进展，正在核对已有尝试并整理本轮报告。')
        self.assertEqual(store.rows('SELECT * FROM messages WHERE session_id=?',(sid,)),before)

    def test_unknown_pause_http_requires_exact_confirmation_and_preserves_unknown(self):
        sid = self.session(); store = self.app.store; stamp = 'fixture timestamp'
        store.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)', ('unknown', sid, 'original', 'queue', 'unknown', None, 'original lost ACK', stamp, stamp))
        store.patch_session(sid, status='unknown')
        before = store.one("SELECT * FROM deliveries WHERE id='unknown'")
        path = f'/api/sessions/{sid}/deliveries/unknown/pause'
        for body in [{'reason': '人工暂停'}, {'confirmed': 1, 'reason': '人工暂停'}, {'confirmed': True, 'reason': '  '}]:
            with self.subTest(body=body), self.assertRaises(urllib.error.HTTPError) as denied:
                self.request(path, body)
            self.assertEqual(denied.exception.code, 400)
        result = self.read_json(path, {'confirmed': True, 'reason': '先人工核对原请求，不重发'})
        again = self.read_json(path, {'confirmed': True, 'reason': '先人工核对原请求，不重发'})
        self.assertTrue(result['paused']); self.assertFalse(result['replayed'])
        self.assertEqual(result['delivery'], before); self.assertEqual(again['delivery'], before)
        self.assertEqual(store.one("SELECT * FROM deliveries WHERE id='unknown'"), before)
        self.assertEqual(len(store.rows('SELECT * FROM delivery_human_pauses')), 1)
        self.assertEqual(len(store.rows("SELECT * FROM events WHERE kind='delivery.human_paused'")), 1)
        self.assertTrue(result['delivery_controls']['barrier'])
        self.assertTrue(self.app.kernel.research.workflow.paused(sid))
        self.assertIsNone(self.app.kernel.rpc)
        with self.assertRaises(urllib.error.HTTPError):
            self.request(f'/api/sessions/{sid}/send', {'id': 'replacement', 'text': 'do not replay'})
        self.assertEqual(len(store.rows('SELECT * FROM deliveries')), 1)
        store.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)', ('rejected', sid, 'x', 'queue', 'rejected', None, 'original rejection', stamp, stamp))
        with self.assertRaises(urllib.error.HTTPError):
            self.request(f'/api/sessions/{sid}/deliveries/rejected/pause', {'confirmed': True, 'reason': '人工暂停'})
        other = self.session()
        with self.assertRaises(urllib.error.HTTPError):
            self.request(f'/api/sessions/{other}/deliveries/unknown/pause', {'confirmed': True, 'reason': '人工暂停'})
        self.assertEqual(store.one("SELECT state,error FROM deliveries WHERE id='rejected'"), {'state': 'rejected', 'error': 'original rejection'})

    def test_http_interrupt_returns_while_start_ack_is_pending(self):
        sid = self.session(); kernel = self.app.kernel
        kernel.transport_factory = BarrierRPC; kernel.load_thread(sid); rpc = kernel.rpc
        self.addCleanup(rpc.release_start.set)
        response = self.read_json(f'/api/sessions/{sid}/send', {'id': 'pending', 'text': 'original'})
        self.assertIn(response['state'], {'queued', 'requested'})
        self.assertTrue(rpc.start_entered.wait(2))
        result = self.read_json(f'/api/sessions/{sid}/interrupt', {})
        self.assertTrue(result['requested'])
        self.assertEqual(result['delivery_controls']['interrupt_intents'][0]['state'], 'pending')
        rpc.release_start.set()
        self.assertTrue(rpc.interrupted.wait(2))
        self.wait(lambda: self.read_json(f'/api/sessions/{sid}/deliveries/pending')['state'] == 'accepted')
        self.assertEqual([params for method, params in rpc.calls if method == 'turn/interrupt'], [{'threadId': 'native-thread', 'turnId': 'original-turn'}])
        inspected = self.read_json(f'/api/sessions/{sid}/deliveries/pending')
        self.assertEqual((inspected['id'], inspected['turn_id']), ('pending', 'original-turn'))
        self.assertEqual(len([m for m, p in rpc.calls if m == 'turn/start']), 1)

    def test_http_restore_no_echo_keeps_unknown_and_same_id_send_never_retries(self):
        sid = self.session(); kernel = self.app.kernel
        kernel.load_thread(sid); kernel.rpc.unknown = True
        self.read_json(f'/api/sessions/{sid}/send', {'id': 'lost', 'text': 'original'})
        self.wait(lambda: self.app.store.one("SELECT state FROM deliveries WHERE id='lost'")['state'] == 'unknown')
        before = self.read_json(f'/api/sessions/{sid}/deliveries/lost')
        restored = self.read_json(f'/api/sessions/{sid}/restore', {})
        self.assertEqual(restored['status'], 'unknown')
        repeated = self.read_json(f'/api/sessions/{sid}/send', {'id': 'lost', 'text': 'original'})
        self.assertEqual(repeated, before)
        self.assertEqual(len([m for m, p in kernel.rpc.calls if m == 'turn/start']), 1)
        with self.assertRaises(urllib.error.HTTPError): self.request(f'/api/sessions/{sid}/send', {'id': 'lost', 'text': 'original', 'mode': 'steer'})

    def test_restore_uses_original_turn_list_when_full_read_times_out(self):
        sid = self.session()
        kernel = self.app.kernel
        kernel.load_thread(sid)
        self.app.store.patch_session(sid, status='running', active_turn='original-turn')
        rpc = kernel.rpc
        rpc.turns = [{'id': 'original-turn', 'status': 'interrupted', 'items': []}]
        request = rpc.request

        def read(method, params, timeout=60):
            if method == 'thread/read':
                raise RPCUnknown('read timed out')
            return request(method, params, timeout=timeout)

        rpc.request = read
        restored = self.read_json(f'/api/sessions/{sid}/restore', {})
        self.assertIsNone(restored['active_turn'])
        self.assertEqual(restored['status'], 'idle')
        self.assertTrue(any(method == 'thread/turns/list' for method, _ in rpc.calls))
        self.assertFalse(any(method in {'turn/start', 'turn/interrupt'} for method, _ in rpc.calls))

    def test_native_compact_http_cannot_bypass_unknown_or_active_turn(self):
        sid = self.session(); kernel = self.app.kernel; store = self.app.store
        kernel.load_thread(sid)
        store.patch_session(sid, status='running', active_turn='original-turn')
        path = f'/api/sessions/{sid}/native'
        with self.assertRaises(urllib.error.HTTPError): self.request(path, {'method': 'thread/compact/start'})
        self.assertFalse(any(m == 'thread/compact/start' for m, p in kernel.rpc.calls))
        store.patch_session(sid, status='idle', active_turn=None)
        stamp = 'fixture timestamp'
        store.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)', ('unknown', sid, 'original', 'queue', 'unknown', None, 'lost ACK', stamp, stamp))
        with self.assertRaises(urllib.error.HTTPError): self.request(path, {'method': 'thread/compact/start'})
        self.assertFalse(any(m == 'thread/compact/start' for m, p in kernel.rpc.calls))
        self.assertEqual(store.one("SELECT state FROM deliveries WHERE id='unknown'")['state'], 'unknown')

    def test_record_http_exposes_exact_review_identity_and_frozen_plan_digest(self):
        from lab.research import plan_digest
        sid = self.session(); store = self.app.store
        project_id = store.session(sid)['project_id']
        file = Path(store.session(sid)['workspace']) / 'evidence.txt'
        file.write_text('frozen proposal evidence', encoding='utf-8')
        plan = store.add_record(sid, {'kind': 'plan', 'title': 'exact plan', 'content': 'frozen plan', 'evidence': ['evidence.txt']}, {})
        first = self.read_json('/api/records/' + plan['id'])
        self.assertEqual((first['record_id'], first['project_id'], first['plan_digest']), (plan['id'], project_id, plan_digest(plan)))
        file.write_text('changed evidence bytes', encoding='utf-8')
        second = self.read_json('/api/records/' + plan['id'])
        self.assertEqual(second['plan_digest'], first['plan_digest'])
        self.assertFalse(second['integrity']['valid'])
        record = store.add_record(sid, {'kind': 'alignment', 'title': 'discussion', 'content': 'not a plan'}, {})
        inspected = self.read_json('/api/records/' + record['id'])
        self.assertEqual(inspected['record_id'], record['id']); self.assertIsNone(inspected['plan_digest'])
        self.assertEqual(store.rows('SELECT * FROM research_reviews'), [])
        self.assertEqual(store.rows('SELECT * FROM research_grants'), [])
        self.assertIsNone(self.app.kernel.rpc)


if __name__=='__main__':
    unittest.main()
